"""
store/tasks.py
──────────────
Celery-Tasks für App-Prüfung & Veröffentlichung.

Datei-Strategie:
  • Neue Uploads: Datei liegt lokal → zur JDS Cloud hochladen → von Cloud prüfen
  • Re-Checks / alle Fälle wo Cloud-URL existiert: direkt von Cloud herunterladen
  • Lokale Datei wird NIE mehr direkt für Schritt 2-5 genutzt – nur für den Upload
  • Extension/MIME wird aus original_filename (DB-Feld) oder aus dem
    Content-Type-Header der Cloud-Antwort ermittelt
"""

import os
import logging
import mimetypes
import tarfile
import zipfile
import hashlib
import tempfile
import requests as _requests
import pefile
import pyclamd
from celery import shared_task
from django.utils import timezone
from django.conf import settings as django_settings
from django.core.mail import EmailMultiAlternatives
from .models import Version, Notification, App, VersionDownload
from django.template.loader import render_to_string
from settings.models import NotificationSettings
from .jds_cloud import upload_file as upload_to_jds_cloud
from . import onesignal

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# MIME → Extension Map  (Content-Type → .ext)
# ─────────────────────────────────────────────────────────────────────────────
_MIME_TO_EXT = {
    "application/vnd.android.package-archive": ".apk",
    "application/x-authorware-bin":            ".aab",
    "application/octet-stream":                ".bin",   # generic fallback
    "application/vnd.debian.binary-package":   ".deb",
    "application/x-rpm":                       ".rpm",
    "application/x-apple-diskimage":           ".dmg",
    "application/x-msdos-program":             ".exe",
    "application/x-msdownload":                ".exe",
    "application/x-msi":                       ".msi",
    "application/x-tar":                       ".tar.gz",
    "application/x-bzip2":                     ".tar.gz",
    "application/gzip":                        ".gz",
    "application/zip":                         ".zip",
}

ALLOWED_EXTS = {
    ".apk", ".aab", ".ipa", ".exe", ".msi", ".dmg", ".pkg",
    ".deb", ".rpm", ".appimage", ".tar.gz", ".tgz", ".gz",
}


def _ext_from_name(name: str) -> str:
    """Gibt Dateiendung aus einem Dateinamen zurück."""
    name = name.lower()
    if name.endswith(".tar.gz"):
        return ".tar.gz"
    return os.path.splitext(name)[1].lower()


def _ext_from_content_type(ct: str) -> str:
    """Ermittelt Dateiendung aus HTTP Content-Type Header."""
    if not ct:
        return ""
    ct_base = ct.split(";")[0].strip().lower()
    return _MIME_TO_EXT.get(ct_base, "")


# ─────────────────────────────────────────────────────────────────────────────
# Datei von JDS Cloud herunterladen
# ─────────────────────────────────────────────────────────────────────────────
def _download_from_cloud(cloud_url: str, hint_ext: str, log: list) -> tuple:
    """
    Lädt die Datei von der JDS Cloud in eine temporäre Datei herunter.
    Gibt (tmp_path, detected_ext) zurück oder (None, '') bei Fehler.

    detected_ext: Dateiendung aus Content-Type Header oder hint_ext
    """
    log.append(f"  ↓ Lade von JDS Cloud: {cloud_url}")
    try:
        with _requests.get(cloud_url, timeout=600, stream=True) as r:
            r.raise_for_status()

            # Extension aus Content-Type ermitteln
            ct  = r.headers.get("Content-Type", "")
            ext = _ext_from_content_type(ct)
            if not ext or ext == ".bin":
                ext = hint_ext  # Fallback auf hint (aus original_filename)
            if not ext:
                ext = ".bin"

            log.append(f"  Content-Type: {ct or 'unbekannt'}  →  Extension: {ext}")

            tmp_fd, tmp_path = tempfile.mkstemp(suffix=ext, prefix="jds_")
            os.close(tmp_fd)
            total = 0
            with open(tmp_path, "wb") as tf:
                for chunk in r.iter_content(chunk_size=65536):
                    tf.write(chunk)
                    total += len(chunk)

        log.append(f"  ✓ Download OK: {total/(1024*1024):.2f} MB → {tmp_path}")
        return tmp_path, ext

    except Exception as e:
        log.append(f"  ✗ Download fehlgeschlagen: {e}")
        return None, ""


# ─────────────────────────────────────────────────────────────────────────────
# E-Mail & Notification Helpers
# ─────────────────────────────────────────────────────────────────────────────
def send_check_email(user, subject_de, subject_en, message_de, message_en,
                     log_lines, app=None, version=None, level="info",
                     error_msg=None):
    log_text = "\n".join(log_lines)
    new_apps  = App.objects.filter(
        published=True, published_at__isnull=False
    ).order_by("-published_at")[:6]

    html_content = render_to_string("emails/notification.html", {
        "user":        user,
        "subject_de":  subject_de,
        "subject_en":  subject_en,
        "message_de":  message_de,
        "message_en":  message_en,
        "log_text":    log_text,
        "app":         app,
        "version":     version,
        "level":       level,
        "error_msg":   error_msg,
        "latest_apps": new_apps,
    })
    text = (
        f"{message_de}\n\nPrüfprotokoll:\n{log_text}"
        f"\n\n---\n{message_en}\n\nCheck log:\n{log_text}"
    )
    if error_msg:
        text += f"\n\nFEHLER / ERROR:\n{error_msg}"

    msg = EmailMultiAlternatives(
        f"{subject_de} / {subject_en}", text, to=[user.email]
    )
    msg.attach_alternative(html_content, "text/html")
    msg.send()


def create_notification(user, title, message, app=None, version=None, level="info"):
    Notification.objects.create(
        user=user, title=title, message=message,
        app=app, version=version, level=level,
    )


def _notify_installed_users_of_update(app: App, version: Version, tag_info: str) -> None:
    """OneSignal-Push an alle User, die eine ältere Version dieser App installiert haben."""
    user_ids = list(
        VersionDownload.objects.filter(version__app=app)
        .exclude(version=version)
        .values_list("user_id", flat=True)
        .distinct()
    )
    if not user_ids:
        return
    onesignal.send_to_users(
        user_ids,
        title=f"Update für {app.name}",
        message=f"Version {version.version_number}{tag_info} ist verfügbar.",
        url=f"{getattr(django_settings, 'SITE_URL', '')}/app/{app.id}/",
        data={"type": "app_update", "app_id": app.id, "version_id": version.id},
    )


def _notify_recommendations(app: App) -> None:
    """OneSignal-Push an User, die andere Apps derselben Kategorie/Plattform installiert haben."""
    similar_apps = App.objects.filter(
        platform=app.platform, category=app.category, published=True
    ).exclude(id=app.id)
    user_ids = list(
        VersionDownload.objects.filter(version__app__in=similar_apps)
        .exclude(user__in=VersionDownload.objects.filter(version__app=app).values("user"))
        .values_list("user_id", flat=True)
        .distinct()
    )
    if not user_ids:
        return
    onesignal.send_to_users(
        user_ids,
        title="Neue App-Empfehlung",
        message=f"{app.name} könnte dir gefallen – jetzt entdecken.",
        url=f"{getattr(django_settings, 'SITE_URL', '')}/app/{app.id}/",
        data={"type": "recommendation", "app_id": app.id},
    )


def _do_publish(version: Version) -> None:
    app           = version.app
    dev           = app.developer
    notif_cfg     = NotificationSettings.objects.filter(user=dev.user).first()
    release_label = version.get_release_label()
    tag_info      = f" [{release_label}]" if release_label else ""

    old_ver = app.versions.filter(
        approved=True, new_version=True
    ).exclude(id=version.id).order_by("-uploaded_at").first()

    if old_ver:
        old_ver.new_version = False
        old_ver.save(update_fields=["new_version"])

    version.release_held = False
    version.new_version  = True
    version.save(update_fields=["release_held", "new_version"])

    if not app.published:
        app.published    = True
        app.published_at = timezone.now()
        app.save()

    log = [f"Veröffentlicht / Published: {timezone.now().strftime('%d.%m.%Y %H:%M')} UTC"]

    if notif_cfg and notif_cfg.email_notifications:
        send_check_email(
            user=dev.user,
            subject_de=f"✓ {app.name} v{version.version_number}{tag_info} ist jetzt live",
            subject_en=f"✓ {app.name} v{version.version_number}{tag_info} is now live",
            message_de=(
                f"{'Das Update' if old_ver else 'Deine App'} {app.name} "
                f"v{version.version_number}{tag_info} ist jetzt im JDS AppStore verfügbar."
            ),
            message_en=(
                f"{'The update for' if old_ver else 'Your app'} {app.name} "
                f"v{version.version_number}{tag_info} is now publicly available on JDS AppStore."
            ),
            log_lines=log, app=app, version=version, level="success_2",
        )
    if notif_cfg and notif_cfg.push_notifications:
        create_notification(
            user=dev.user,
            title=f"🚀 {app.name} ist jetzt live",
            message=f"Version {version.version_number}{tag_info} ist veröffentlicht.",
            app=app, version=version, level="success_2",
        )

    # ── OneSignal: userbezogene Push-Benachrichtigungen ──────────────────────
    onesignal.send_to_user(
        dev.user,
        title=f"🚀 {app.name} ist jetzt live",
        message=f"Version {version.version_number}{tag_info} ist veröffentlicht.",
        url=f"{getattr(django_settings, 'SITE_URL', '')}/app/{app.id}/",
        data={"type": "published", "app_id": app.id, "version_id": version.id},
    )
    if old_ver:
        _notify_installed_users_of_update(app, version, tag_info)
    else:
        _notify_recommendations(app)


# ─────────────────────────────────────────────────────────────────────────────
# Core check pipeline
# ─────────────────────────────────────────────────────────────────────────────
def _run_check(version: Version) -> bool:
    log:      list[str] = []
    dev       = version.app.developer
    notif_cfg = NotificationSettings.objects.filter(user=dev.user).first()
    app       = version.app

    def progress(step: int, msg: str):
        log.append(msg)
        version.checking_progress = step
        version.checking_log = "\n".join(log)
        version.save(update_fields=["checking_progress", "checking_log"])

    def fail(msg_de: str, msg_en: str = None):
        msg_en = msg_en or msg_de
        log.append(f"✖ FEHLER / ERROR: {msg_de}")
        version.checking_status = "failed"
        version.approved        = False
        version.checking_log    = "\n".join(log)
        version.save()
        if notif_cfg and notif_cfg.email_notifications:
            send_check_email(
                user=dev.user,
                subject_de=f"Prüfung fehlgeschlagen: {app.name} v{version.version_number}",
                subject_en=f"Review failed: {app.name} v{version.version_number}",
                message_de="Die automatische Prüfung ist fehlgeschlagen. Bitte lade eine korrigierte Version hoch.",
                message_en="The automated review has failed. Please upload a corrected version.",
                log_lines=log, app=app, version=version,
                level="error", error_msg=msg_de,
            )
        if notif_cfg and notif_cfg.push_notifications:
            create_notification(
                user=dev.user,
                title=f"Prüfung fehlgeschlagen: {app.name}",
                message=msg_de, app=app, version=version, level="error",
            )
        return False

    # ── Init ─────────────────────────────────────────────────────────────────
    version.checking_status   = "running"
    version.checking_log      = ""
    version.checking_progress = 0
    version.save()

    # ── Step 1: Basisvalidierung ─────────────────────────────────────────────
    progress(1, "▶ Schritt 1/5 – Basisvalidierung / Basic validation")

    # Extension aus gespeichertem Originalnamen ermitteln
    original_name = (version.original_filename or "").strip()
    hint_ext      = _ext_from_name(original_name) if original_name else ""
    log.append(f"  Originaldatei: '{original_name or '(unbekannt)'}' → hint_ext='{hint_ext}'")

    tmp_path = None   # temporäre Datei (von Cloud geladen) – wird am Ende gelöscht

    # ── Step 3 zuerst: In JDS Cloud hochladen (falls noch nicht geschehen) ───
    # Lade lokal nur für den Upload, dann sofort zur Cloud
    progress(1, "▶ Schritt 1/5 – Basisvalidierung / Basic validation")

    if not version.jds_cloud_url:
        # Lokale Datei für Upload suchen
        log.append("  Suche lokale Datei für Upload zur JDS Cloud…")
        local_path = None
        from django.conf import settings as _s

        for candidate in [
            getattr(version.file, 'path', None),
            os.path.join(_s.MEDIA_ROOT, str(version.file)) if hasattr(_s, 'MEDIA_ROOT') else None,
            os.path.join(_s.MEDIA_ROOT, os.path.basename(str(version.file))) if hasattr(_s, 'MEDIA_ROOT') else None,
        ]:
            if candidate and os.path.isfile(candidate):
                local_path = candidate
                log.append(f"  Lokale Datei: {local_path}")
                break

        if local_path:
            # Extension für den Dateinamen in der Cloud
            upload_ext  = hint_ext or os.path.splitext(local_path)[1].lower() or ".bin"
            upload_name = f"{app.name.replace(' ', '_')}_{version.version_number}{upload_ext}"
            log.append(f"  Lade zur JDS Cloud hoch als '{upload_name}'…")

            progress(3, "▶ Schritt 3/5 – Upload zur JDS Cloud / Uploading to JDS Cloud")
            result = upload_to_jds_cloud(local_path, upload_name)

            if not result["success"]:
                detail = result.get("error", "Unbekannt")
                if result.get("status"):
                    detail = f"HTTP {result['status']}: {detail}"
                log.append(f"  ✗ API: {result.get('body','')[:200]}")
                return fail(
                    f"JDS-Cloud-Upload fehlgeschlagen: {detail}",
                    f"JDS Cloud upload failed: {detail}",
                )

            version.jds_cloud_file_id  = result["file_id"]
            version.jds_cloud_url      = result["download_url"]
            version.jds_cloud_view_url = result["view_url"]
            version.save(update_fields=["jds_cloud_file_id","jds_cloud_url","jds_cloud_view_url"])
            log.append(f"  ✓ JDS Cloud Upload OK – ID: {result['file_id']}")
            log.append(f"  URL: {result['download_url']}")
        else:
            log.append("  Keine lokale Datei – überspringe direkten Upload (Cloud-URL wird vorausgesetzt)")

    else:
        log.append(f"  ✓ Bereits in JDS Cloud: {version.jds_cloud_url}")

    # ── Datei von Cloud laden (für Prüfung) ──────────────────────────────────
    cloud_url = version.jds_cloud_url
    if not cloud_url:
        return fail(
            "Keine JDS-Cloud-URL vorhanden. Bitte Datei erneut hochladen.",
            "No JDS Cloud URL available. Please re-upload the file.",
        )

    progress(1, "▶ Schritt 1/5 – Basisvalidierung / Basic validation")
    tmp_path, detected_ext = _download_from_cloud(cloud_url, hint_ext, log)

    if not tmp_path:
        return fail(
            "Cloud-Datei konnte nicht heruntergeladen werden.",
            "Could not download file from JDS Cloud.",
        )

    try:
        # Endgültige Extension: bevorzuge hint_ext (vom Originalnamen), dann Content-Type
        ext = hint_ext if hint_ext in ALLOWED_EXTS else detected_ext
        if ext not in ALLOWED_EXTS:
            return fail(
                f"Nicht erlaubter Dateityp: '{ext}' (Originaldatei: '{original_name}'). "
                f"Erlaubt: {', '.join(sorted(ALLOWED_EXTS))}",
                f"File type not allowed: '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTS))}",
            )

        size = os.path.getsize(tmp_path)
        log.append(f"  Größe / Size: {size/(1024*1024):.2f} MB")
        if size == 0:
            return fail("Datei ist leer (0 Bytes).", "File is empty (0 bytes).")
        if size > 1024 * 1024 * 1024:
            return fail("Datei überschreitet 1 GB.", "File exceeds 1 GB limit.")

        sha256 = hashlib.sha256()
        with open(tmp_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                sha256.update(chunk)
        log.append(f"  SHA-256: {sha256.hexdigest()}")
        log.append(f"  Extension: {ext}  |  MIME: {mimetypes.guess_type(original_name or tmp_path)[0] or 'unknown'}")

        # ── Step 2: Strukturprüfung ───────────────────────────────────────────
        progress(2, "▶ Schritt 2/5 – Strukturprüfung / Structure check")

        if ext in (".exe", ".msi"):
            try:
                pe = pefile.PE(tmp_path, fast_load=True)
                log.append(f"  PE EntryPoint: {hex(pe.OPTIONAL_HEADER.AddressOfEntryPoint)}")
                has_cert = hasattr(pe, "DIRECTORY_ENTRY_SECURITY") and pe.DIRECTORY_ENTRY_SECURITY
                log.append(f"  Certificate: {'✓' if has_cert else '⚠ missing (recommended)'}")
                pe.close()
            except pefile.PEFormatError as e:
                return fail(f"Ungültige EXE/MSI: {e}", f"Invalid EXE/MSI: {e}")

        elif ext in (".ipa", ".apk", ".aab", ".deb"):
            try:
                with zipfile.ZipFile(tmp_path, "r") as zf:
                    names   = zf.namelist()
                    c_size  = sum(i.compress_size for i in zf.infolist())
                    uc_size = sum(i.file_size      for i in zf.infolist())
                    log.append(f"  {len(names)} Dateien  |  {c_size/1024:.0f} KB komprimiert")
                    if c_size and (uc_size / max(c_size, 1)) > 200:
                        return fail("ZIP-Bombe erkannt.", "ZIP bomb detected.")
                    bad = [n for n in names if ".." in n or n.startswith("/")]
                    if bad:
                        return fail(f"Unsichere Pfade: {bad[:3]}", f"Unsafe paths: {bad[:3]}")
                    if ext == ".ipa" and not any(n.startswith("Payload/") for n in names):
                        return fail("Kein Payload/-Ordner in IPA.", "No Payload/ in IPA.")
                    if ext in (".apk", ".aab") and "AndroidManifest.xml" not in names:
                        return fail("Keine AndroidManifest.xml.", "No AndroidManifest.xml.")
                    log.append("  ✓ Archivstruktur OK / Archive structure OK")
            except zipfile.BadZipFile as e:
                return fail(f"Beschädigtes Archiv: {e}", f"Corrupted archive: {e}")

        elif ext in (".tar.gz", ".tgz"):
            try:
                with tarfile.open(tmp_path, "r:gz") as tar:
                    members = tar.getmembers()
                    log.append(f"  {len(members)} Einträge / entries")
                    bad = [m.name for m in members if m.name.startswith("/") or ".." in m.name]
                    if bad:
                        return fail(f"Unsichere Pfade: {bad[:3]}", f"Unsafe paths: {bad[:3]}")
            except Exception as e:
                return fail(f"tar.gz Fehler: {e}", f"tar.gz error: {e}")

        log.append("  ✓ Strukturprüfung bestanden / Structure check passed")

        # Step 3 wurde bereits oben erledigt (Upload)
        progress(3, "▶ Schritt 3/5 – JDS Cloud / JDS Cloud")
        log.append(f"  ✓ In JDS Cloud gespeichert")
        log.append(f"  URL: {version.jds_cloud_url}")

        # ── Step 4: Virenscan ─────────────────────────────────────────────────
        progress(4, "▶ Schritt 4/5 – Virenscan / Malware scan")
        try:
            cd = pyclamd.ClamdNetworkSocket()
            if cd.ping():
                scan = cd.scan_file(tmp_path)
                if scan:
                    return fail(f"Malware erkannt: {scan}", f"Malware detected: {scan}")
                log.append("  ✓ Kein Malware-Fund / No malware found")
            else:
                log.append("  ⚠ ClamAV nicht erreichbar – Scan übersprungen")
        except Exception as e:
            log.append(f"  ⚠ Scan-Fehler: {e} – wird fortgesetzt")

        # ── Step 5: Freigabe ──────────────────────────────────────────────────
        progress(5, "▶ Schritt 5/5 – Freigabe / Release")

        release_label = version.get_release_label()
        tag_info      = f" [{release_label}]" if release_label else ""

        version.checking_status   = "passed"
        version.approved          = True
        version.checking_log      = "\n".join(log)
        version.checking_progress = 5
        version.save()

        onesignal.send_to_user(
            dev.user,
            title=f"✓ Prüfung bestanden: {app.name}",
            message=f"Version {version.version_number}{tag_info} hat die Prüfung bestanden.",
            url=f"{getattr(django_settings, 'SITE_URL', '')}/developer/{version.id}/check/",
            data={"type": "check_passed", "app_id": app.id, "version_id": version.id},
        )

        # Geplantes Release?
        if version.scheduled_release_at and version.scheduled_release_at > timezone.now():
            version.release_held = True
            version.save(update_fields=["release_held"])
            rel_dt = version.scheduled_release_at.strftime("%d.%m.%Y %H:%M")
            log.append(f"  ⏰ Geplante Veröffentlichung: {rel_dt} UTC")
            if notif_cfg and notif_cfg.email_notifications:
                send_check_email(
                    user=dev.user,
                    subject_de=f"✓ Prüfung bestanden – Release am {rel_dt} UTC",
                    subject_en=f"✓ Review passed – scheduled for {rel_dt} UTC",
                    message_de=f"Prüfung für {app.name} v{version.version_number}{tag_info} bestanden. Veröffentlichung am {rel_dt} UTC.",
                    message_en=f"Review for {app.name} v{version.version_number}{tag_info} passed. Will publish on {rel_dt} UTC.",
                    log_lines=log, app=app, version=version, level="success_1",
                )
            if notif_cfg and notif_cfg.push_notifications:
                create_notification(
                    user=dev.user,
                    title=f"Prüfung bestanden – Release am {rel_dt}",
                    message=f"{app.name} v{version.version_number}{tag_info} wird am {rel_dt} UTC veröffentlicht.",
                    app=app, version=version, level="success_1",
                )
            return True

        # Sofort veröffentlichen
        log.append("  \u2192 Sofortige Ver\u00f6ffentlichung / Immediate release")
        _do_publish(version)
        return True

    finally:
        # Temporäre Datei immer aufräumen
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
                log.append(f"  Temporäre Datei gelöscht.")
            except Exception:
                pass
            # Log nochmal speichern nach cleanup
            version.checking_log = "\n".join(log)
            version.save(update_fields=["checking_log"])


# ─────────────────────────────────────────────────────────────────────────────
# Celery Tasks
# ─────────────────────────────────────────────────────────────────────────────
@shared_task
def start_background_check(version_id: int):
    try:
        version = Version.objects.select_related(
            "app", "app__developer", "app__developer__user"
        ).get(id=version_id)
    except Version.DoesNotExist:
        return
    _run_check(version)


@shared_task
def start_background_check_version(version_id: int):
    try:
        version = Version.objects.select_related(
            "app", "app__developer", "app__developer__user"
        ).get(id=version_id)
    except Version.DoesNotExist:
        return
    _run_check(version)


# ─────────────────────────────────────────────────────────────────────────────
# Cron-triggered publish
# ─────────────────────────────────────────────────────────────────────────────
def publish_due_scheduled_releases() -> dict:
    due = Version.objects.filter(
        approved=True,
        checking_status="passed",
        release_held=True,
        scheduled_release_at__lte=timezone.now(),
    ).select_related("app", "app__developer", "app__developer__user")

    published, errors = [], []
    for version in due:
        try:
            _do_publish(version)
            published.append({"id": version.id, "app": version.app.name, "version": version.version_number})
        except Exception as exc:
            errors.append({"id": version.id, "app": version.app.name, "error": str(exc)})

    return {
        "published_count": len(published),
        "error_count":     len(errors),
        "published":       published,
        "errors":          errors,
        "checked_at":      timezone.now().isoformat(),
    }
