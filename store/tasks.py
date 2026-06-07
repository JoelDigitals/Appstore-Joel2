"""
store/tasks.py
──────────────
Celery-Tasks für App-Prüfung & Veröffentlichung.

Datei-Strategie beim Prüfen:
  1. Lokale Datei vorhanden → direkt prüfen + zur JDS Cloud hochladen
  2. Datei bereits in JDS Cloud (jds_cloud_url gesetzt) → von Cloud
     herunterladen, lokal prüfen (temp), aufräumen
  3. Erst Stufe 1 schlägt fehl → Stufe 2 automatisch als Fallback
"""

import os
import logging
import mimetypes
import tarfile
import zipfile
import hashlib
import tempfile
import shutil
import pefile
import pyclamd
import requests as _requests
from celery import shared_task
from django.utils import timezone
from django.core.mail import EmailMultiAlternatives
from .models import Version, Notification, App
from django.template.loader import render_to_string
from settings.models import NotificationSettings
from .jds_cloud import upload_file as upload_to_jds_cloud

logger = logging.getLogger(__name__)


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


def _do_publish(version: Version) -> None:
    app           = version.app
    dev           = app.developer
    notif_cfg     = NotificationSettings.objects.filter(user=dev.user).first()
    release_label = version.get_release_label()
    tag_info      = f" [{release_label}]" if release_label else ""

    old_ver = app.versions.filter(
        approved=True, new_version=True
    ).exclude(id=version.id).order_by("-uploaded_at").first()

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
                f"v{version.version_number}{tag_info} is now publicly available on the JDS AppStore."
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


# ─────────────────────────────────────────────────────────────────────────────
# Datei beschaffen – lokal oder von JDS Cloud herunterladen
# ─────────────────────────────────────────────────────────────────────────────
def _resolve_file(version: Version, log: list) -> tuple:
    """
    Gibt (file_path, is_temp) zurück.
    is_temp=True → Aufrufer muss die Datei danach löschen.

    Strategie:
      A) Lokale Datei vorhanden → nutzen (is_temp=False)
      B) JDS Cloud URL vorhanden → herunterladen nach temp (is_temp=True)
      C) Nichts → (None, False)
    """
    from django.conf import settings as _s

    # ── A: Lokale Datei suchen ────────────────────────────────────────────
    local_path = None
    tried = []

    # A1: version.file.path
    try:
        p = version.file.path
        tried.append(p)
        if os.path.isfile(p):
            local_path = p
    except (ValueError, NotImplementedError, AttributeError):
        pass

    # A2: MEDIA_ROOT + file.name
    if not local_path:
        try:
            p = os.path.join(_s.MEDIA_ROOT, str(version.file))
            tried.append(p)
            if os.path.isfile(p):
                local_path = p
        except Exception:
            pass

    # A3: MEDIA_ROOT + basename
    if not local_path:
        try:
            basename = os.path.basename(str(version.file))
            p = os.path.join(_s.MEDIA_ROOT, basename)
            tried.append(p)
            if os.path.isfile(p):
                local_path = p
        except Exception:
            pass

    # A4: rekursive Suche in MEDIA_ROOT
    if not local_path:
        try:
            basename = os.path.basename(str(version.file))
            for root, _, files in os.walk(_s.MEDIA_ROOT):
                if basename in files:
                    p = os.path.join(root, basename)
                    tried.append(p)
                    local_path = p
                    break
        except Exception:
            pass

    if local_path:
        log.append(f"  Datei lokal gefunden: {local_path}")
        return local_path, False

    log.append(f"  Lokal nicht gefunden. Geprüfte Pfade: {tried}")

    # ── B: Von JDS Cloud herunterladen ────────────────────────────────────
    cloud_url = version.jds_cloud_url or ""
    if cloud_url:
        log.append(f"  Lade von JDS Cloud herunter: {cloud_url}")
        ext = os.path.splitext(str(version.file))[1].lower() or ".bin"
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=ext, prefix="jds_check_")
            os.close(tmp_fd)
            with _requests.get(cloud_url, timeout=600, stream=True) as r:
                r.raise_for_status()
                with open(tmp_path, "wb") as tf:
                    for chunk in r.iter_content(chunk_size=65536):
                        tf.write(chunk)
            size = os.path.getsize(tmp_path)
            log.append(f"  ✓ Cloud-Download OK: {size/(1024*1024):.2f} MB → {tmp_path}")
            return tmp_path, True
        except Exception as dl_err:
            log.append(f"  ✗ Cloud-Download fehlgeschlagen: {dl_err}")
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            return None, False

    log.append("  Keine Cloud-URL und keine lokale Datei vorhanden.")
    return None, False


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

    # ── Datei beschaffen ─────────────────────────────────────────────────────
    progress(1, "▶ Schritt 1/5 – Basisvalidierung / Basic validation")
    log.append("  Suche Datei (lokal → JDS Cloud)…")

    file_path, is_temp = _resolve_file(version, log)

    if not file_path:
        return fail(
            f"Datei '{version.file}' weder lokal noch in der JDS Cloud gefunden. "
            f"Bitte erneut hochladen.",
            f"File '{version.file}' not found locally or in JDS Cloud. Please re-upload.",
        )

    # ── Step 1: Basic validation ──────────────────────────────────────────────
    try:
        size = os.path.getsize(file_path)
    except OSError as e:
        return fail(f"Dateigröße nicht lesbar: {e}", f"Cannot read file size: {e}")

    log.append(f"  Größe / Size: {size / (1024 * 1024):.2f} MB")
    if size == 0:
        return fail("Datei ist leer (0 Bytes).", "File is empty (0 bytes).")
    if size > 1024 * 1024 * 1024:
        return fail("Datei überschreitet 1 GB.", "File exceeds 1 GB limit.")

    # Extension aus dem Originalnamen ableiten (nicht aus temp-Pfad)
    original_name = os.path.basename(str(version.file))
    ext = os.path.splitext(original_name)[1].lower()
    if original_name.lower().endswith(".tar.gz"):
        ext = ".tar.gz"

    mime_type, _ = mimetypes.guess_type(original_name)
    log.append(f"  Dateiname: {original_name}  |  Extension: {ext}  |  MIME: {mime_type or 'unknown'}")

    ALLOWED = {
        ".apk", ".aab", ".ipa", ".exe", ".msi", ".dmg", ".pkg",
        ".deb", ".rpm", ".appimage", ".tar.gz", ".tgz", ".gz",
    }
    if ext not in ALLOWED:
        if is_temp:
            try: os.remove(file_path)
            except: pass
        return fail(
            f"Nicht erlaubter Dateityp: '{ext}'.",
            f"File type not allowed: '{ext}'.",
        )

    sha256 = hashlib.sha256()
    with open(file_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            sha256.update(chunk)
    log.append(f"  SHA-256: {sha256.hexdigest()}")

    # ── Step 2: Structure check ───────────────────────────────────────────────
    progress(2, "▶ Schritt 2/5 – Strukturprüfung / Structure check")

    if ext in (".exe", ".msi"):
        try:
            pe = pefile.PE(file_path, fast_load=True)
            log.append(f"  PE EntryPoint: {hex(pe.OPTIONAL_HEADER.AddressOfEntryPoint)}")
            has_cert = hasattr(pe, "DIRECTORY_ENTRY_SECURITY") and pe.DIRECTORY_ENTRY_SECURITY
            log.append(f"  Certificate: {'✓' if has_cert else '⚠ missing (recommended)'}")
            pe.close()
        except pefile.PEFormatError as e:
            if is_temp: os.remove(file_path)
            return fail(f"Ungültige EXE/MSI: {e}", f"Invalid EXE/MSI: {e}")

    elif ext in (".ipa", ".apk", ".aab", ".deb"):
        try:
            with zipfile.ZipFile(file_path, "r") as zf:
                names   = zf.namelist()
                c_size  = sum(i.compress_size for i in zf.infolist())
                uc_size = sum(i.file_size      for i in zf.infolist())
                log.append(f"  {len(names)} files  |  {c_size/1024:.0f} KB compressed")
                if c_size and (uc_size / max(c_size, 1)) > 200:
                    if is_temp: os.remove(file_path)
                    return fail("ZIP-Bombe erkannt.", "ZIP bomb detected.")
                bad = [n for n in names if ".." in n or n.startswith("/")]
                if bad:
                    if is_temp: os.remove(file_path)
                    return fail(f"Unsichere Pfade: {bad[:3]}", f"Unsafe paths: {bad[:3]}")
                if ext == ".ipa" and not any(n.startswith("Payload/") for n in names):
                    if is_temp: os.remove(file_path)
                    return fail("Kein Payload/-Ordner in IPA.", "No Payload/ folder in IPA.")
                if ext in (".apk", ".aab") and "AndroidManifest.xml" not in names:
                    if is_temp: os.remove(file_path)
                    return fail("Keine AndroidManifest.xml.", "No AndroidManifest.xml.")
                log.append("  ✓ Archive structure OK")
        except zipfile.BadZipFile as e:
            if is_temp: os.remove(file_path)
            return fail(f"Beschädigtes Archiv: {e}", f"Corrupted archive: {e}")

    elif ext in (".tar.gz", ".tgz"):
        try:
            with tarfile.open(file_path, "r:gz") as tar:
                members = tar.getmembers()
                log.append(f"  {len(members)} entries")
                bad = [m.name for m in members if m.name.startswith("/") or ".." in m.name]
                if bad:
                    if is_temp: os.remove(file_path)
                    return fail(f"Unsichere Pfade: {bad[:3]}", f"Unsafe paths: {bad[:3]}")
        except Exception as e:
            if is_temp: os.remove(file_path)
            return fail(f"tar.gz Fehler: {e}", f"tar.gz error: {e}")

    log.append("  ✓ Structure check passed")

    # ── Step 3: JDS Cloud Upload (nur wenn noch nicht hochgeladen) ────────────
    progress(3, "▶ Schritt 3/5 – JDS Cloud / Upload to JDS Cloud")

    if version.jds_cloud_url:
        # Datei wurde bereits hochgeladen (z.B. war is_temp=True → von Cloud geladen)
        log.append(f"  ✓ Bereits in JDS Cloud vorhanden")
        log.append(f"  URL: {version.jds_cloud_url}")
    else:
        fname  = f"{app.name.replace(' ', '_')}_{version.version_number}{ext}"
        result = upload_to_jds_cloud(file_path, fname)

        if not result["success"]:
            detail = result.get("error", "Unbekannt")
            if result.get("status"):
                detail = f"HTTP {result['status']}: {detail}"
            if result.get("body"):
                log.append(f"  ✗ API-Antwort: {result['body'][:300]}")
            if is_temp:
                try: os.remove(file_path)
                except: pass
            return fail(
                f"JDS-Cloud-Upload fehlgeschlagen: {detail}",
                f"JDS Cloud upload failed: {detail}",
            )

        version.jds_cloud_file_id  = result["file_id"]
        version.jds_cloud_url      = result["download_url"]
        version.jds_cloud_view_url = result["view_url"]
        version.save(update_fields=["jds_cloud_file_id", "jds_cloud_url", "jds_cloud_view_url"])
        log.append(f"  ✓ Hochgeladen / Uploaded")
        log.append(f"  ID:   {result['file_id']}")
        log.append(f"  Name: {result['name']}  ({result['size'] / (1024*1024):.2f} MB)")
        log.append(f"  URL:  {result['download_url']}")

    # ── Temporäre Datei aufräumen (wenn von Cloud geladen) ────────────────────
    if is_temp:
        try:
            os.remove(file_path)
            log.append(f"  Temporäre Datei gelöscht.")
        except Exception:
            pass

    # ── Step 4: ClamAV Scan ───────────────────────────────────────────────────
    progress(4, "▶ Schritt 4/5 – Virenscan / Malware scan")

    # Für den Scan laden wir die Cloud-Datei erneut herunter (sauber, frisch von Cloud)
    cloud_url  = version.jds_cloud_url or ""
    scan_path  = None
    scan_is_temp = False

    if cloud_url:
        log.append(f"  Lade Cloud-Datei für Scan…")
        ext2 = os.path.splitext(original_name)[1].lower() or ".bin"
        try:
            tmp_fd2, scan_path = tempfile.mkstemp(suffix=ext2, prefix="jds_scan_")
            os.close(tmp_fd2)
            with _requests.get(cloud_url, timeout=600, stream=True) as r:
                r.raise_for_status()
                with open(scan_path, "wb") as tf:
                    for chunk in r.iter_content(chunk_size=65536):
                        tf.write(chunk)
            scan_is_temp = True
            log.append(f"  ✓ Cloud-Datei für Scan bereit")
        except Exception as dl_err:
            log.append(f"  ⚠ Cloud-Download für Scan fehlgeschlagen: {dl_err} – Scan übersprungen")
            scan_path = None

    try:
        if scan_path and os.path.isfile(scan_path):
            cd = pyclamd.ClamdNetworkSocket()
            if cd.ping():
                scan = cd.scan_file(scan_path)
                if scan:
                    return fail(f"Malware erkannt: {scan}", f"Malware detected: {scan}")
                log.append("  ✓ No malware found")
            else:
                log.append("  ⚠ ClamAV unavailable – scan skipped")
        else:
            log.append("  ⚠ Keine Scan-Datei verfügbar – Scan übersprungen")
    except Exception as e:
        log.append(f"  ⚠ Scan error: {e} – continuing")
    finally:
        if scan_is_temp and scan_path:
            try: os.remove(scan_path)
            except: pass

    # ── Step 5: Release / Schedule ────────────────────────────────────────────
    progress(5, "▶ Schritt 5/5 – Freigabe / Release")

    release_label = version.get_release_label()
    tag_info      = f" [{release_label}]" if release_label else ""

    version.checking_status   = "passed"
    version.approved          = True
    version.checking_log      = "\n".join(log)
    version.checking_progress = 5
    version.save()

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
                subject_en=f"✓ Review passed – Release scheduled for {rel_dt} UTC",
                message_de=(
                    f"Prüfung für {app.name} v{version.version_number}{tag_info} bestanden. "
                    f"Veröffentlichung am {rel_dt} UTC."
                ),
                message_en=(
                    f"Review for {app.name} v{version.version_number}{tag_info} passed. "
                    f"Will be published on {rel_dt} UTC."
                ),
                log_lines=log, app=app, version=version, level="success_1",
            )
        if notif_cfg and notif_cfg.push_notifications:
            create_notification(
                user=dev.user,
                title=f"Prüfung bestanden: {app.name} – Release am {rel_dt}",
                message=f"Version {version.version_number}{tag_info} wird am {rel_dt} UTC veröffentlicht.",
                app=app, version=version, level="success_1",
            )
        return True

    # Sofortige Veröffentlichung
    log.append("  → Sofortige Veröffentlichung / Immediate release")

    if notif_cfg and notif_cfg.email_notifications:
        send_check_email(
            user=dev.user,
            subject_de=f"✓ Prüfung bestanden: {app.name} v{version.version_number}{tag_info}",
            subject_en=f"✓ Review passed: {app.name} v{version.version_number}{tag_info}",
            message_de=(
                f"Version {version.version_number}{tag_info} von {app.name} "
                f"wurde geprüft und in der JDS Cloud gespeichert."
            ),
            message_en=(
                f"Version {version.version_number}{tag_info} of {app.name} "
                f"has been reviewed and stored in the JDS Cloud."
            ),
            log_lines=log, app=app, version=version, level="success_1",
        )
    if notif_cfg and notif_cfg.push_notifications:
        create_notification(
            user=dev.user,
            title=f"Prüfung bestanden: {app.name} v{version.version_number}",
            message="Version wird jetzt veröffentlicht.",
            app=app, version=version, level="success_1",
        )

    _do_publish(version)
    return True


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
# Cron-triggered publish  (called by HTTP endpoint every 5 minutes)
# ─────────────────────────────────────────────────────────────────────────────
def publish_due_scheduled_releases() -> dict:
    due = Version.objects.filter(
        approved=True,
        checking_status="passed",
        release_held=True,
        scheduled_release_at__lte=timezone.now(),
    ).select_related("app", "app__developer", "app__developer__user")

    published = []
    errors    = []

    for version in due:
        try:
            _do_publish(version)
            published.append({
                "id":      version.id,
                "app":     version.app.name,
                "version": version.version_number,
            })
        except Exception as exc:
            errors.append({
                "id":    version.id,
                "app":   version.app.name,
                "error": str(exc),
            })

    return {
        "published_count": len(published),
        "error_count":     len(errors),
        "published":       published,
        "errors":          errors,
        "checked_at":      timezone.now().isoformat(),
    }
