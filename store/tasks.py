import os
import mimetypes
import tarfile
import zipfile
import time
import random
import pefile
import pyclamd
from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from django.core.mail import EmailMultiAlternatives
from .models import Version, Notification, App
from django.template.loader import render_to_string
from django.conf import settings
from settings.models import NotificationSettings

def send_check_email(user, subject, message, log_lines, app=None, version=None, level='info', error_msg=None):
    """
    level: 'success_1', 'success_2', 'error', 'info'
    """
    log_text = "\n".join(log_lines)

    new_apps = App.objects.filter(
        published=True,
        published_at__isnull=False
    ).order_by('-published_at')[:6]

    html_content = render_to_string('emails/notification.html', {
        'user': user,
        'subject': subject,
        'message': message,
        'log_text': log_text,
        'app': app,
        'version': version,
        'level': level,        # <-- hier statt success
        'error_msg': error_msg,
        'latest_apps': new_apps,
    })

    text_content = f"{message}\n\nPrüfprotokoll:\n{log_text}"
    if error_msg:
        text_content += f"\n\nFEHLER:\n{error_msg}"

    msg = EmailMultiAlternatives(subject, text_content, to=[user.email])
    msg.attach_alternative(html_content, "text/html")
    msg.send()

def create_notification(user, title, message, app=None, version=None, level='info'):
    Notification.objects.create(
        user=user,
        title=title,
        message=message,
        app=app,
        version=version,
        level=level
    )

@shared_task
def start_background_check(version_id):
    try:
        version = Version.objects.get(id=version_id)
    except Version.DoesNotExist:
        return



    version.checking_status = 'running'
    version.checking_log = ''
    version.save()

    file_path = version.file.path
    log = []
    dev = version.app.developer
    notification = NotificationSettings.objects.filter(user=dev.user.user).first

    def fail(msg):
        log.append(f"*** FEHLER: {msg}")
        version.checking_status = 'failed'
        version.approved = False
        version.checking_log = "\n".join(log) + "\n\nFEHLER: " + msg
        version.save()
        if notification.email_notifications:
            send_check_email(
                user=dev.user,
                subject=f"Prüfung fehlgeschlagen: {version.app.name}",
                message="Die App-Prüfung ist fehlgeschlagen.",
                log_lines=log,
                app=version.app,
                version=version,
                level='error',          # <-- hier
                error_msg=msg
            )
            
        if notification.push_notifications:
            create_notification(
                user=dev.user.user,
                title=f"Prüfung fehlgeschlagen: {version.app.name}",
                message=msg,
                app=version.app,
                version=version,
                level='error'
            )

    try:
        log.append(f"Starte Prüfung für Datei: {file_path}")

        mime_type, encoding = mimetypes.guess_type(file_path)
        log.append(f"Ermittelter MIME-Typ (per mimetypes): {mime_type or 'unbekannt'}")

        ext = os.path.splitext(file_path)[1].lower()
        log.append(f"Dateiendung: {ext}")

        size = os.path.getsize(file_path)
        log.append(f"Dateigröße: {size} Bytes")
        if size > 500 * 1024 * 1024:
            return fail("Datei ist größer als 500MB.")

        version.checking_progress = 1  # Update den Fortschritt
        version.save()
        time.sleep(random.randint(1, 3) * 60)

        if ext == '.exe':
            log.append("Prüfe EXE-Datei (PE-Analyse).")
            try:
                pe = pefile.PE(file_path)
                log.append(f"PE EntryPoint: {hex(pe.OPTIONAL_HEADER.AddressOfEntryPoint)}")
                if hasattr(pe, 'DIRECTORY_ENTRY_SECURITY') and pe.DIRECTORY_ENTRY_SECURITY:
                    log.append("Digitales Zertifikat gefunden.")
                else:
                    log.append("Kein digitales Zertifikat gefunden.")
                pe.close()
            except pefile.PEFormatError as e:
                return fail(f"Ungültige EXE-Datei: {e}")

        elif ext in ['.ipa', '.apk', '.aab']:
            log.append(f"Prüfe ZIP-Archiv mit Endung {ext}.")
            try:
                with zipfile.ZipFile(file_path, 'r') as z:
                    names = z.namelist()
                    log.append(f"ZIP-Archiv enthält {len(names)} Dateien.")
                    comp_size = sum(info.compress_size for info in z.infolist())
                    uncomp_size = sum(info.file_size for info in z.infolist())
                    log.append(f"Komprimierte Größe: {comp_size}, Unkomprimierte Größe: {uncomp_size}")
                    if comp_size and uncomp_size / comp_size > 100:
                        return fail("Verdacht auf ZIP-Bombe: Verhältnis unkomprimiert zu komprimiert zu hoch.")
                    if ext == '.ipa' and not any(n.startswith('Payload/') for n in names):
                        return fail("Fehlender Payload-Ordner in IPA.")
                    if ext in ['.apk', '.aab'] and 'AndroidManifest.xml' not in names:
                        return fail("Fehlende AndroidManifest.xml in APK/AAB.")
                    for n in names:
                        if '..' in n or n.startswith('/'):
                            return fail(f"Unsichere Pfad-Referenz im Archiv: {n}")
            except zipfile.BadZipFile as e:
                return fail(f"Ungültiges ZIP-Archiv: {e}")

        elif ext in ['.tar.gz', '.tgz']:
            log.append("Prüfe tar.gz-Archiv.")
            try:
                with tarfile.open(file_path, 'r:gz') as tar:
                    members = tar.getmembers()
                    log.append(f"tar.gz enthält {len(members)} Einträge.")
                    for m in members:
                        if m.name.startswith('/') or '..' in m.name:
                            return fail(f"Unsichere Pfad-Referenz im tar.gz: {m.name}")
            except Exception as e:
                return fail(f"Fehler beim Entpacken des tar.gz: {e}")

        elif ext == '.gz':
            log.append("Prüfe .gz-Datei, versuche Entpacken.")
            try:
                import gzip, shutil
                temp_out = file_path + '_ungzipped'
                with gzip.open(file_path, 'rb') as f_in, open(temp_out, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
                log.append(f".gz entpackt: {temp_out}")
                if temp_out.endswith('.ipa') or zipfile.is_zipfile(temp_out):
                    with zipfile.ZipFile(temp_out, 'r') as z:
                        names = z.namelist()
                        log.append(f"Entpacktes Archiv enthält {len(names)} Dateien.")
                        if not any(n.startswith('Payload/') for n in names):
                            return fail("Entpackte IPA fehlt Payload-Ordner.")
                else:
                    log.append("Keine ZIP-Struktur erkannt.")
                os.remove(temp_out)
            except Exception as e:
                return fail(f"Fehler beim Entpacken der .gz-Datei: {e}")

        else:
            return fail(f"Unbekannter oder nicht erlaubter Dateityp: {ext}")

        version.checking_progress = 2 # Update den Fortschritt
        version.save()
        # Wartezeit simulieren, um realistische Prüfungsdauer zu haben
        time.sleep(random.randint(1, 3) * 60)

        try:
            log.append("Starte Virenscan mit ClamAV.")
            cd = pyclamd.ClamdNetworkSocket()
            if not cd.ping():
                log.append("Warnung: ClamAV nicht erreichbar.")
            else:
                scan = cd.scan_file(file_path)
                if scan:
                    return fail(f"Malware erkannt: {scan}")
                else:
                    log.append("Kein Malware-Fund im Scan.")
        except Exception as e:
            log.append(f"Virenscan-Fehler: {e}")

        version.checking_progress = 3  # Update den Fortschritt
        version.save()
        time.sleep(random.randint(1, 3) * 60)

        version.checking_status = 'passed'
        version.approved = True
        version.checking_log = "Erfolgreich geprüft:\n" + "\n".join(log)
        version.save()

        subject = f"{version.app.name} wurde erfolgreich geprüft"
        message = f"Die Version {version.version_number} Ihrer App wurde erfolgreich geprüft und freigegeben."
        if notification.email_notifications:
            # Nach erfolgreicher Prüfung (success_1)
            send_check_email(
                dev,
                subject,
                message,
                log,
                app=version.app,
                version=version,
                level='success_1'
            )

        if notification.push_notifications:
            create_notification(
                user=dev.user.user,
                title=f"Prüfung erfolgreich: {version.app.name}",
                message=message,
                app=version.app,
                version=version,
                level='success_1'
            )

        version.checking_progress = 4  # Update den Fortschritt
        version.save()
        time.sleep(random.randint(5, 10, 15, 20) * 60)

        version.checking_progress = 5
        version.new_version = True  # Markiere die Version als neu
        version.save
        version.app.published = True
        version.app.save()
        if notification.email_notifications:
            send_check_email(
                dev,
                f"{version.app.name} ist jetzt veröffentlicht",
                "Ihre App wurde soeben freigegeben und ist jetzt öffentlich sichtbar.",
                log,
                app=version.app,
                version=version,
                level='success_2'
            )

        if notification.push_notifications:
            create_notification(
                user=dev.user.user,
                title=f"{version.app.name} ist jetzt veröffentlicht",
                message="Ihre App wurde soeben freigegeben und ist jetzt öffentlich sichtbar.",
                app=version.app,
                version=version,
                level='success_2'
            )

    except Exception as e:
        error_message = f"Unerwarteter Fehler: {e}"
        version.checking_status = 'failed'
        version.approved = False
        version.checking_log = "\n".join(log) + "\n\nFEHLER: " + error_message
        version.save()
        if notification.email_notifications:
            send_check_email(
                user=dev.user.user,
                subject=f"Unerwarteter Fehler bei der Prüfung: {version.app.name}",
                message="Die App-Prüfung ist aufgrund eines unerwarteten Fehlers fehlgeschlagen.",
                log_lines=log,
                app=version.app,
                version=version,
                success=False,
                error_msg=error_message
            )
        if notification.push_notifications:
            create_notification(
                user=dev.user.user,
                title=f"Prüfung fehlgeschlagen: {version.app.name}",
                message=error_message,
                app=version.app,
                version=version,
                level='error'
            )




@shared_task
def start_background_check_version(version_id):
    try:
        version = Version.objects.get(id=version_id)
    except Version.DoesNotExist:
        return
    
    old_version = version.app.versions.filter(
        approved=True,
        new_version=True
    ).order_by('-uploaded_at').first()  

    version.checking_status = 'running'
    version.checking_log = ''
    version.save()

    file_path = version.file.path
    log = []
    dev = version.app.developer
    notification = NotificationSettings.objects.filter(user=dev.user).first
    

    def fail(msg):
        log.append(f"*** FEHLER: {msg}")
        version.checking_status = 'failed'
        version.approved = False
        version.checking_log = "\n".join(log) + "\n\nFEHLER: " + msg
        version.save()
        if notification.email_notifications:
            send_check_email(
                user=dev.user,
                subject=f"Prüfung fehlgeschlagen: {version.app.name}",
                message="Die App-Prüfung ist fehlgeschlagen.",
                log_lines=log,
                app=version.app,
                version=version,
                level='error',          # <-- hier
                error_msg=msg
            )

        if notification.push_notifications:
            create_notification(
                user=dev.user.user,
                title=f"Prüfung fehlgeschlagen: {version.app.name}",
                message=msg,
                app=version.app,
                version=version,
                level='error'
            )

    try:
        log.append(f"Starte Prüfung für Datei: {file_path}")

        mime_type, encoding = mimetypes.guess_type(file_path)
        log.append(f"Ermittelter MIME-Typ (per mimetypes): {mime_type or 'unbekannt'}")

        ext = os.path.splitext(file_path)[1].lower()
        log.append(f"Dateiendung: {ext}")

        size = os.path.getsize(file_path)
        log.append(f"Dateigröße: {size} Bytes")
        if size > 500 * 1024 * 1024:
            return fail("Datei ist größer als 500MB.")

        version.checking_progress = 1  # Update den Fortschritt
        version.save()
        #time.sleep(random.randint(1, 3) * 60)

        if ext == '.exe':
            log.append("Prüfe EXE-Datei (PE-Analyse).")
            try:
                pe = pefile.PE(file_path)
                log.append(f"PE EntryPoint: {hex(pe.OPTIONAL_HEADER.AddressOfEntryPoint)}")
                if hasattr(pe, 'DIRECTORY_ENTRY_SECURITY') and pe.DIRECTORY_ENTRY_SECURITY:
                    log.append("Digitales Zertifikat gefunden.")
                else:
                    log.append("Kein digitales Zertifikat gefunden.")
                pe.close()
            except pefile.PEFormatError as e:
                return fail(f"Ungültige EXE-Datei: {e}")

        elif ext in ['.ipa', '.apk', '.aab']:
            log.append(f"Prüfe ZIP-Archiv mit Endung {ext}.")
            try:
                with zipfile.ZipFile(file_path, 'r') as z:
                    names = z.namelist()
                    log.append(f"ZIP-Archiv enthält {len(names)} Dateien.")
                    comp_size = sum(info.compress_size for info in z.infolist())
                    uncomp_size = sum(info.file_size for info in z.infolist())
                    log.append(f"Komprimierte Größe: {comp_size}, Unkomprimierte Größe: {uncomp_size}")
                    if comp_size and uncomp_size / comp_size > 100:
                        return fail("Verdacht auf ZIP-Bombe: Verhältnis unkomprimiert zu komprimiert zu hoch.")
                    if ext == '.ipa' and not any(n.startswith('Payload/') for n in names):
                        return fail("Fehlender Payload-Ordner in IPA.")
                    if ext in ['.apk', '.aab'] and 'AndroidManifest.xml' not in names:
                        return fail("Fehlende AndroidManifest.xml in APK/AAB.")
                    for n in names:
                        if '..' in n or n.startswith('/'):
                            return fail(f"Unsichere Pfad-Referenz im Archiv: {n}")
            except zipfile.BadZipFile as e:
                return fail(f"Ungültiges ZIP-Archiv: {e}")

        elif ext in ['.tar.gz', '.tgz']:
            log.append("Prüfe tar.gz-Archiv.")
            try:
                with tarfile.open(file_path, 'r:gz') as tar:
                    members = tar.getmembers()
                    log.append(f"tar.gz enthält {len(members)} Einträge.")
                    for m in members:
                        if m.name.startswith('/') or '..' in m.name:
                            return fail(f"Unsichere Pfad-Referenz im tar.gz: {m.name}")
            except Exception as e:
                return fail(f"Fehler beim Entpacken des tar.gz: {e}")

        elif ext == '.gz':
            log.append("Prüfe .gz-Datei, versuche Entpacken.")
            try:
                import gzip, shutil
                temp_out = file_path + '_ungzipped'
                with gzip.open(file_path, 'rb') as f_in, open(temp_out, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
                log.append(f".gz entpackt: {temp_out}")
                if temp_out.endswith('.ipa') or zipfile.is_zipfile(temp_out):
                    with zipfile.ZipFile(temp_out, 'r') as z:
                        names = z.namelist()
                        log.append(f"Entpacktes Archiv enthält {len(names)} Dateien.")
                        if not any(n.startswith('Payload/') for n in names):
                            return fail("Entpackte IPA fehlt Payload-Ordner.")
                else:
                    log.append("Keine ZIP-Struktur erkannt.")
                os.remove(temp_out)
            except Exception as e:
                return fail(f"Fehler beim Entpacken der .gz-Datei: {e}")

        else:
            return fail(f"Unbekannter oder nicht erlaubter Dateityp: {ext}")

        version.checking_progress = 2  # Update den Fortschritt
        version.save()
        #time.sleep(random.randint(1, 3) * 60)

        try:
            log.append("Starte Virenscan mit ClamAV.")
            cd = pyclamd.ClamdNetworkSocket()
            if not cd.ping():
                log.append("Warnung: ClamAV nicht erreichbar.")
            else:
                scan = cd.scan_file(file_path)
                if scan:
                    return fail(f"Malware erkannt: {scan}")
                else:
                    log.append("Kein Malware-Fund im Scan.")
        except Exception as e:
            log.append(f"Virenscan-Fehler: {e}")

        version.checking_progress = 3  # Update den Fortschritt
        version.save()
        #time.sleep(random.randint(1, 2) * 60)

        version.checking_status = 'passed'
        version.approved = True
        version.checking_log = "Erfolgreich geprüft:\n" + "\n".join(log)
        version.save()

        subject = f"{version} wurde erfolgreich geprüft"
        message = f"Die Version {version.version_number} Ihrer App wurde erfolgreich geprüft."
        if notification.email_notifications:
            # Nach erfolgreicher Prüfung (success_1)
            send_check_email(
                dev,
                subject,
                message,
                log,
                app=version.app,
                version=version,
                level='success_1'
            )

        if notification.push_notifications:
            create_notification(
                user=dev.user.user,
                title=f"Prüfung erfolgreich: {version}",
                message=message,
                app=version.app,
                version=version,
                level='success_1'
            )
        version.checking_progress = 4  # Update den Fortschritt
        version.save()
        #time.sleep(random.randint(1, 2, 5, 10, 15, 20) * 60)

        if old_version:
            log.append(f"Gefundene vorherige Version: {old_version.version_number}")
        else:
            log.append("Keine vorherige Version gefunden (Erstveröffentlichung).")
  
        version.new_version = True  # Markiere die neue Version als neu
        version.checking_progress = 5  # Update den Fortschritt
        version.save()

        if notification.email_notifications:
            send_check_email(
                dev,
                f"{version.app.name} ist jetzt veröffentlicht",
                "Ihre App wurde soeben freigegeben und ist jetzt öffentlich sichtbar.",
                log,
                app=version.app,
                version=version,
                level='success_2'
            )

        if notification.push_notifications:
            create_notification(
                user=dev.user.user,
                title=f"{version} ist jetzt veröffentlicht",
                message="Ihre Version wurde soeben freigegeben und ist jetzt öffentlich sichtbar.",
                app=version.app,
                version=version,
                level='success_2'
            )

    except Exception as e:
        error_message = f"Unerwarteter Fehler: {e}"
        version.checking_status = 'failed'
        version.approved = False
        version.checking_log = "\n".join(log) + "\n\nFEHLER: " + error_message
        version.save()
        if notification.email_notifications:
            send_check_email(
                user=dev.user.user,
                subject=f"Unerwarteter Fehler bei der Prüfung: {version.app.name}",
                message="Die App-Prüfung ist aufgrund eines unerwarteten Fehlers fehlgeschlagen.",
                log_lines=log,
                app=version.app,
                version=version,
                success=False,
                error_msg=error_message
            )
        if notification.push_notifications:
            create_notification(
                user=dev.user.user,
                title=f"Prüfung fehlgeschlagen: {version.app.name}",
                message=error_message,
                app=version.app,
                version=version,
                level='error'
            )