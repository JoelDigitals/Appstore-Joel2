"""
store/tasks.py
──────────────
Celery-Tasks für App-Prüfung & Veröffentlichung.

Geplante Releases (scheduled_release_at):
  • Die Prüfpipeline setzt release_held=True  wenn das Datum noch in der
    Zukunft liegt – die App ist geprüft und bereit, aber noch nicht live.
  • Ein externer Cron-Dienst ruft alle 5 Minuten
      POST /api/cron/scheduled-releases/
    auf und löst publish_due_scheduled_releases() aus, das alle fälligen
    Versionen veröffentlicht.  Kein Celery Beat benötigt.
"""

import os
import logging
import mimetypes
import tarfile
import zipfile
import hashlib
import pefile
import pyclamd
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
    """Send bilingual DE/EN notification e-mail."""
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

    subject = f"{subject_de} / {subject_en}"
    msg = EmailMultiAlternatives(subject, text, to=[user.email])
    msg.attach_alternative(html_content, "text/html")
    msg.send()


def create_notification(user, title, message, app=None, version=None, level="info"):
    Notification.objects.create(
        user=user, title=title, message=message,
        app=app, version=version, level=level,
    )


def _do_publish(version: Version) -> None:
    """
    Core publish action: mark version live, publish app if needed,
    send e-mail + push notification.
    Called both from _run_check (immediate) and publish_due_scheduled_releases (cron).
    """
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
                f"v{version.version_number}{tag_info} ist jetzt im JDS AppStore "
                f"öffentlich verfügbar."
            ),
            message_en=(
                f"{'The update for' if old_ver else 'Your app'} {app.name} "
                f"v{version.version_number}{tag_info} is now publicly available "
                f"on the JDS AppStore."
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
# Core check pipeline
# ─────────────────────────────────────────────────────────────────────────────
def _run_check(version: Version) -> bool:
    """
    5-step review pipeline:
      1. Basic validation (size, extension, SHA-256)
      2. Structure check  (ZIP bomb, manifest, PE analysis)
      3. JDS Cloud upload
      4. ClamAV malware scan
      5. Release (immediate or schedule)
    """
    log:      list[str] = []
    dev       = version.app.developer
    notif_cfg = NotificationSettings.objects.filter(user=dev.user).first()
    app       = version.app

    # ── helpers ──────────────────────────────────────────────────────────────
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
                message_de=(
                    "Die automatische Prüfung ist fehlgeschlagen. "
                    "Bitte lade eine korrigierte Version hoch."
                ),
                message_en=(
                    "The automated review has failed. "
                    "Please upload a corrected version."
                ),
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

    try:
        file_path = version.file.path
    except Exception:
        return fail("Datei nicht gefunden.", "File not found.")

    # ── Step 1: Basic validation ─────────────────────────────────────────────
    progress(1, "▶ Schritt 1/5 – Basisvalidierung / Basic validation")

    try:
        size = os.path.getsize(file_path)
    except FileNotFoundError:
        return fail(
            "Datei existiert nicht mehr auf dem Server.",
            "File no longer exists on the server.",
        )

    log.append(f"  Größe / Size: {size / (1024 * 1024):.2f} MB")
    if size == 0:
        return fail("Datei ist leer (0 Bytes).", "File is empty (0 bytes).")
    if size > 1024 * 1024 * 1024:
        return fail("Datei überschreitet 1 GB.", "File exceeds 1 GB limit.")

    ext = os.path.splitext(file_path)[1].lower()
    if file_path.lower().endswith(".tar.gz"):
        ext = ".tar.gz"
    mime_type, _ = mimetypes.guess_type(file_path)
    log.append(f"  Extension: {ext}  |  MIME: {mime_type or 'unknown'}")

    ALLOWED = {
        ".apk", ".aab", ".ipa", ".exe", ".msi", ".dmg", ".pkg",
        ".deb", ".rpm", ".appimage", ".tar.gz", ".tgz", ".gz",
    }
    if ext not in ALLOWED:
        return fail(
            f"Nicht erlaubter Dateityp: '{ext}'.",
            f"File type not allowed: '{ext}'.",
        )

    sha256 = hashlib.sha256()
    with open(file_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            sha256.update(chunk)
    log.append(f"  SHA-256: {sha256.hexdigest()}")

    # ── Step 2: Structure check (auf lokaler Datei – vor dem Upload) ────────
    progress(2, "▶ Schritt 2/5 – Strukturprüfung / Structure check")

    if ext in (".exe", ".msi"):
        try:
            pe = pefile.PE(file_path, fast_load=True)
            log.append(f"  PE EntryPoint: {hex(pe.OPTIONAL_HEADER.AddressOfEntryPoint)}")
            has_cert = hasattr(pe, "DIRECTORY_ENTRY_SECURITY") and pe.DIRECTORY_ENTRY_SECURITY
            log.append(f"  Certificate: {'✓' if has_cert else '⚠ missing (recommended)'}")
            pe.close()
        except pefile.PEFormatError as e:
            return fail(f"Ungültige EXE/MSI: {e}", f"Invalid EXE/MSI: {e}")

    elif ext in (".ipa", ".apk", ".aab", ".deb"):
        try:
            with zipfile.ZipFile(file_path, "r") as zf:
                names   = zf.namelist()
                c_size  = sum(i.compress_size for i in zf.infolist())
                uc_size = sum(i.file_size      for i in zf.infolist())
                log.append(f"  {len(names)} files  |  {c_size/1024:.0f} KB compressed")
                if c_size and (uc_size / max(c_size, 1)) > 200:
                    return fail("ZIP-Bombe erkannt.", "ZIP bomb detected.")
                bad = [n for n in names if ".." in n or n.startswith("/")]
                if bad:
                    return fail(f"Unsichere Pfade: {bad[:3]}", f"Unsafe paths: {bad[:3]}")
                if ext == ".ipa" and not any(n.startswith("Payload/") for n in names):
                    return fail("Kein Payload/-Ordner in IPA.", "No Payload/ folder in IPA.")
                if ext in (".apk", ".aab") and "AndroidManifest.xml" not in names:
                    return fail("Keine AndroidManifest.xml.", "No AndroidManifest.xml.")
                log.append("  ✓ Archive structure OK")
        except zipfile.BadZipFile as e:
            return fail(f"Beschädigtes Archiv: {e}", f"Corrupted archive: {e}")

    elif ext in (".tar.gz", ".tgz"):
        try:
            with tarfile.open(file_path, "r:gz") as tar:
                members = tar.getmembers()
                log.append(f"  {len(members)} entries")
                bad = [m.name for m in members if m.name.startswith("/") or ".." in m.name]
                if bad:
                    return fail(f"Unsichere Pfade: {bad[:3]}", f"Unsafe paths: {bad[:3]}")
        except Exception as e:
            return fail(f"tar.gz Fehler: {e}", f"tar.gz error: {e}")

    elif ext == ".gz":
        try:
            import gzip, shutil
            tmp = file_path + "_ungz"
            with gzip.open(file_path, "rb") as fi, open(tmp, "wb") as fo:
                shutil.copyfileobj(fi, fo)
            if zipfile.is_zipfile(tmp):
                with zipfile.ZipFile(tmp, "r") as zf:
                    log.append(f"  {len(zf.namelist())} files in decompressed zip")
            os.remove(tmp)
        except Exception as e:
            return fail(f"gz-Fehler: {e}", f"gz error: {e}")

    log.append("  ✓ Structure check passed")

    # ── Step 3: JDS Cloud Upload ─────────────────────────────────────────────
    progress(3, "▶ Schritt 3/5 – Upload zur JDS Cloud / Upload to JDS Cloud")

    fname  = f"{app.name.replace(' ', '_')}_{version.version_number}{ext}"
    result = upload_to_jds_cloud(file_path, fname)

    if not result["success"]:
        detail = result.get("error", "Unbekannt")
        if result.get("status"):
            detail = f"HTTP {result['status']}: {detail}"
        if result.get("body"):
            log.append(f"  ✗ API-Antwort: {result['body'][:300]}")
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

    # ── Step 4: Malware scan – auf der JDS Cloud URL (falls vorhanden) ───────
    # Bevorzuge Cloud-URL; falle auf lokale Datei zurück wenn ClamAV
    # remote scannen kann, sonst lokale Datei.
    progress(4, "▶ Schritt 4/5 – Virenscan / Malware scan")

    cloud_url = version.jds_cloud_url or ""
    scan_target = file_path  # Standard: lokale Datei

    # Wenn Cloud-URL vorhanden: Datei temporär herunterladen für den Scan
    if cloud_url:
        log.append(f"  Lade Datei von JDS Cloud für Scan herunter…")
        try:
            import requests as _req, tempfile
            with _req.get(cloud_url, timeout=300, stream=True) as r:
                if r.status_code == 200:
                    suffix = os.path.splitext(fname)[1]
                    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
                    os.close(tmp_fd)
                    with open(tmp_path, "wb") as tf:
                        for chunk in r.iter_content(chunk_size=65536):
                            tf.write(chunk)
                    scan_target = tmp_path
                    log.append(f"  ✓ Cloud-Datei heruntergeladen für Scan: {tmp_path}")
                else:
                    log.append(f"  ⚠ Cloud-Download für Scan fehlgeschlagen (HTTP {r.status_code}) – nutze lokale Datei")
        except Exception as dl_err:
            log.append(f"  ⚠ Cloud-Download Fehler: {dl_err} – nutze lokale Datei")

    tmp_scan_path = scan_target if scan_target != file_path else None

    try:
        cd = pyclamd.ClamdNetworkSocket()
        if cd.ping():
            scan = cd.scan_file(scan_target)
            if scan:
                if tmp_scan_path:
                    try: os.remove(tmp_scan_path)
                    except: pass
                return fail(f"Malware erkannt: {scan}", f"Malware detected: {scan}")
            log.append("  ✓ No malware found")
        else:
            log.append("  ⚠ ClamAV unavailable – scan skipped")
    except Exception as e:
        log.append(f"  ⚠ Scan error: {e} – continuing")
    finally:
        # Temporäre Scan-Datei aufräumen
        if tmp_scan_path:
            try: os.remove(tmp_scan_path)
            except: pass

    # ── Step 5: Release / Schedule ───────────────────────────────────────────
    progress(5, "▶ Schritt 5/5 – Freigabe / Release")

    release_label = version.get_release_label()
    tag_info      = f" [{release_label}]" if release_label else ""

    version.checking_status   = "passed"
    version.approved          = True
    version.checking_log      = "\n".join(log)
    version.checking_progress = 5
    version.save()

    # ── Scheduled: Prüfung OK, aber Datum noch in der Zukunft ────────────────
    if version.scheduled_release_at and version.scheduled_release_at > timezone.now():
        version.release_held = True
        version.save(update_fields=["release_held"])

        rel_dt = version.scheduled_release_at.strftime("%d.%m.%Y %H:%M")
        log.append(f"  ⏰ Scheduled release: {rel_dt} UTC")

        if notif_cfg and notif_cfg.email_notifications:
            send_check_email(
                user=dev.user,
                subject_de=f"✓ Prüfung bestanden – Release am {rel_dt} UTC",
                subject_en=f"✓ Review passed – Release scheduled for {rel_dt} UTC",
                message_de=(
                    f"Die Prüfung für {app.name} v{version.version_number}{tag_info} "
                    f"wurde bestanden. Die App wird automatisch am {rel_dt} UTC "
                    f"veröffentlicht."
                ),
                message_en=(
                    f"The review for {app.name} v{version.version_number}{tag_info} "
                    f"passed. The app will be automatically published on {rel_dt} UTC."
                ),
                log_lines=log, app=app, version=version, level="success_1",
            )
        if notif_cfg and notif_cfg.push_notifications:
            create_notification(
                user=dev.user,
                title=f"Prüfung bestanden: {app.name} – Release am {rel_dt}",
                message=(
                    f"Version {version.version_number}{tag_info} wird am "
                    f"{rel_dt} UTC veröffentlicht."
                ),
                app=app, version=version, level="success_1",
            )
        # NOTE: No Celery Beat needed – the cron endpoint
        # POST /api/cron/scheduled-releases/
        # will pick this up when the time comes.
        return True

    # ── Immediate release ─────────────────────────────────────────────────────
    log.append("  → Sofortige Veröffentlichung / Immediate release")
    version.checking_log = "\n".join(log)
    version.save(update_fields=["checking_log"])

    # Inform developer: review passed
    if notif_cfg and notif_cfg.email_notifications:
        send_check_email(
            user=dev.user,
            subject_de=f"✓ Prüfung bestanden: {app.name} v{version.version_number}{tag_info}",
            subject_en=f"✓ Review passed: {app.name} v{version.version_number}{tag_info}",
            message_de=(
                f"Version {version.version_number}{tag_info} von {app.name} "
                f"wurde erfolgreich geprüft und in der JDS Cloud gespeichert."
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
            message=f"Version wird jetzt veröffentlicht.",
            app=app, version=version, level="success_1",
        )

    _do_publish(version)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Celery Tasks
# ─────────────────────────────────────────────────────────────────────────────
@shared_task
def start_background_check(version_id: int):
    """Full review pipeline – for new apps (first upload)."""
    try:
        version = Version.objects.select_related(
            "app", "app__developer", "app__developer__user"
        ).get(id=version_id)
    except Version.DoesNotExist:
        return
    _run_check(version)


@shared_task
def start_background_check_version(version_id: int):
    """Full review pipeline – for version updates."""
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
    """
    Find all versions that:
      - passed review  (approved=True, checking_status='passed')
      - are held back  (release_held=True)
      - their scheduled_release_at is now ≤ now

    Publish each one and return a summary dict.
    Called synchronously from the cron-endpoint view.
    No Celery Beat required.
    """
    due = Version.objects.filter(
        approved=True,
        checking_status="passed",
        release_held=True,
        scheduled_release_at__lte=timezone.now(),
    ).select_related("app", "app__developer", "app__developer__user")

    published     = []
    errors        = []

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
