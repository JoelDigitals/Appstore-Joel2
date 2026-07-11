from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth import login, logout
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from .models import App, Version, PushSubscription, Developer, VersionDownload, Notification, EmailVerificationCode, AppUpdate, RoadmapItem, User, AppReview, OneSignalDevice
from .forms import AppWithVersionForm, VersionForm, DeveloperForm, AppEditForm, CustomUserCreationForm, AppReviewForm
from .tasks import start_background_check, start_background_check_version, notify_security_event
from settings.models import record_login_session
from django.http import FileResponse, JsonResponse, HttpResponse, FileResponse, HttpResponseNotFound, HttpResponseForbidden
import json
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from kombu.exceptions import OperationalError as KombuOpError
from celery import Celery
import os
import zipfile
from io import BytesIO
from django.utils import timezone
from django.db.models import F
from django.db.models import Count
from django.db.models import Sum
from datetime import timedelta
from django.db.models import Q
from .utils import send_push_notification_to_admins
from django.core.cache import cache
from django.conf import settings
import mimetypes
import threading
from django.contrib.auth.views import PasswordResetConfirmView
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.core.mail import send_mail
from django.urls import reverse
import secrets
from settings.models import UserSecurity
from . import models

app_celery = Celery()

from django.http import JsonResponse

from django.http import JsonResponse

def assetlinks(request):
    """
    Android Digital Asset Links JSON für Deep Links
    JDS AppStore - Android App: co.median.android.ljrnrp
    """
    data = [
      {
        "relation": [
          "delegate_permission/common.handle_all_urls"
        ],
        "target": {
          "namespace": "android_app",
          "package_name": "co.median.android.ljrnrp",
          "sha256_cert_fingerprints": [
            "99:29:5E:21:AA:AE:3C:4F:BF:4D:C4:B1:5A:89:81:91:CF:21:14:F4:D8:4E:52:B5:F0:7E:1A:40:BA:22:C3:84"
          ]
        }
      }
    ]
    
    response = JsonResponse(data, safe=False)
    response['Content-Type'] = 'application/json'
    return response

def password_reset_request(request):
    if request.method == "POST":
        username = request.POST.get("username")

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            messages.error(request, "Benutzer nicht gefunden.")
            return redirect("password_reset")

        # Token und Link generieren
        token = default_token_generator.make_token(user)
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        reset_link = request.build_absolute_uri(
            reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": token})
        )

        # Nachricht
        subject = "Passwort zurücksetzen"
        message = f"Hallo {user.username},\n\nHier ist der Link zum Zurücksetzen deines Passworts:\n{reset_link}"

        # E-Mail oder SMS-Versand
        if user.email:
            send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [user.email])
        elif hasattr(user, "phone_number") and user.phone_number:
            print(f"SMS an {user.phone_number}: {message}")  # Simulierter SMS-Versand
        else:
            messages.error(request, "Keine E-Mail oder Telefonnummer hinterlegt.")
            return redirect("password_reset")

        return redirect("password_reset_done")

    return render(request, "accounts/password_reset.html")


def password_reset_done(request):
    return render(request, "accounts/password_reset_done.html")

def password_reset_confirm(request, uidb64, token):
    return PasswordResetConfirmView.as_view(
        template_name="accounts/password_reset_confirm.html",
        success_url=reverse("password_reset_complete")
    )(request, uidb64=uidb64, token=token)

def password_reset_complete(request):
    return render(request, "accounts/password_reset_complete.html")




def get_notifications_for_user(request):
    # Unauthenticated users → leere Liste zurückgeben, kein 500
    if not request.user.is_authenticated:
        return JsonResponse({"count": 0, "notifications": []})

    user = request.user
    notification_count = cache.get(f'notification_count_{user.id}')
    if notification_count is None:
        notification_count = Notification.objects.filter(user=user, read=False).count()
        cache.set(f'notification_count_{user.id}', notification_count, timeout=60*5)

    notifications_qs = Notification.objects.filter(
        user=user, read=False
    ).order_by('-created_at')[:10]

    notifications = []
    for n in notifications_qs:
        notifications.append({
            'id':         n.id,
            'message':    n.message,
            'created_at': n.created_at.strftime("%d.%m.%Y %H:%M"),
        })

    return JsonResponse({"count": notification_count, "notifications": notifications})




@login_required
def notifications_check(request):
    user = request.user
    notifications_qs = Notification.objects.filter(user=user, is_read=False).order_by('-created_at')[:10]
    notifications = list(notifications_qs.values('id', 'message', 'created_at'))
    count = len(notifications)
    for n in notifications:
        n['created_at'] = n['created_at'].strftime("%d.%m.%Y %H:%M")
    return JsonResponse({
        "count": count,
        "notifications": notifications,
    })

@login_required
@csrf_exempt
def push_subscribe(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        endpoint = data.get('endpoint')
        if not endpoint:
            return JsonResponse({'error': 'No endpoint'}, status=400)

        # Existierende Subscriptions löschen, falls mehrfach vorhanden
        PushSubscription.objects.filter(endpoint=endpoint).delete()

        # Neue Subscription speichern
        PushSubscription.objects.create(
            user=request.user,
            endpoint=endpoint,
            data=data
        )
        return JsonResponse({'success': True})
    return JsonResponse({'error': 'Invalid method'}, status=405)


@login_required
@staff_member_required
def download_all_media(request):
    # Verzeichnis mit den Medien-Dateien
    media_root = settings.MEDIA_ROOT

    # Temporärer Speicher im RAM für ZIP-Datei
    zip_buffer = BytesIO()

    # ZIP-Datei erstellen
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for root, dirs, files in os.walk(media_root):
            for file in files:
                full_path = os.path.join(root, file)
                # Relativer Pfad innerhalb der ZIP-Datei
                relative_path = os.path.relpath(full_path, media_root)
                zip_file.write(full_path, arcname=relative_path)

    # ZIP-Puffer an den Anfang zurücksetzen
    zip_buffer.seek(0)

    # Antwort mit ZIP als Download
    response = HttpResponse(zip_buffer, content_type='application/zip')
    response['Content-Disposition'] = 'attachment; filename="media_files.zip"'
    return response



import secrets
from django.shortcuts import render, redirect
from django.contrib.auth import login, get_user_model
from django.contrib import messages
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.utils import timezone
from .models import EmailVerificationCode
from settings.models import UserSecurity

User = get_user_model()


def send_verification_email(user, code, request=None):
    """
    Sendet eine HTML-Verifizierungs-E-Mail an den Benutzer
    """
    subject = 'Reaktiviere deinen Account - JDS Appstore'
    
    # Context für das Template
    context = {
        'username': user.username,
        'code': code,
        'timestamp': timezone.now().strftime('%d.%m.%Y %H:%M Uhr'),
        'user_agent': request.META.get('HTTP_USER_AGENT', 'Unbekannt') if request else 'Unbekannt',
        'ip_address': get_client_ip(request) if request else 'Unbekannt',
    }
    
    # HTML-Content rendern
    html_content = render_to_string('emails/verification_email.html', context)
    text_content = strip_tags(html_content)  # Plain-Text Version
    
    # E-Mail erstellen
    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
        reply_to=[settings.SUPPORT_EMAIL] if hasattr(settings, 'SUPPORT_EMAIL') else None
    )
    
    # HTML-Version hinzufügen
    email.attach_alternative(html_content, "text/html")
    
    # Optional: Logo als CID-Attachment (für bessere Kompatibilität)
    # email.mixed_subtype = 'related'
    
    email.send(fail_silently=False)
    
    return True


def get_client_ip(request):
    """
    Ermittelt die IP-Adresse des Clients
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def login_view(request):
    # ?next= auslesen – sowohl GET als auch POST (Hidden Field)
    next_url = request.POST.get('next') or request.GET.get('next', '').strip()

    # Sicherheit: nur relative Pfade erlauben (kein Open Redirect)
    if next_url and (not next_url.startswith('/') or next_url.startswith('//')):
        next_url = ''

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        try:
            user = User.objects.get(username=username)
            if user.check_password(password):
                if not user.is_active:
                    # Alte Codes löschen
                    EmailVerificationCode.objects.filter(user=user).delete()

                    # Neuen Code generieren
                    code = secrets.token_hex(3).upper()  # 6-stellig alphanumerisch

                    # Code in Datenbank speichern
                    EmailVerificationCode.objects.create(user=user, code=code)

                    # HTML-E-Mail senden
                    send_verification_email(user, code, request)

                    # Session setzen – next_url für nach der Verifizierung merken
                    request.session['pending_user_id'] = user.id
                    request.session['verification_email'] = user.email
                    if next_url:
                        request.session['next_url_after_verify'] = next_url

                    messages.info(request, 'Ein Verifizierungscode wurde an deine E-Mail gesendet.')
                    redirect_target = 'verify_email'
                    if next_url:
                        redirect_target = f'/verify-email/?next={next_url}'
                    return redirect(redirect_target)

                # User ist aktiv -> normaler Login
                login(request, user)
                is_new_device = record_login_session(user, request)
                if is_new_device:
                    notify_security_event(
                        user,
                        title="Neue Anmeldung erkannt",
                        message=f"Dein Konto wurde von einem neuen Gerät aus angemeldet ({request.META.get('REMOTE_ADDR', 'unbekannte IP')}). Warst du das nicht, ändere sofort dein Passwort.",
                    )
                messages.success(request, f'Willkommen zurück, {user.username}!')
                return redirect(next_url or 'home')
            else:
                form_error = True
        except User.DoesNotExist:
            form_error = True

        # Fehlerbehandlung
        form = AuthenticationForm(request, data=request.POST)
        form.add_error(None, "Ungültiger Benutzername oder Passwort.")

    else:
        form = AuthenticationForm()

    return render(request, 'store/login.html', {'form': form, 'next': next_url})


def verify_email_view(request):
    user_id = request.session.get('pending_user_id')
    email = request.session.get('verification_email', 'deine E-Mail-Adresse')
    
    if not user_id:
        messages.error(request, 'Sitzung abgelaufen. Bitte melde dich erneut an.')
        return redirect('login')
    
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        messages.error(request, 'Benutzer nicht gefunden.')
        return redirect('login')
    
    # Neue E-Mail senden (wenn angefordert)
    if request.method == 'POST' and request.POST.get('action') == 'resend':
        # Rate-Limiting prüfen (z.B. max. alle 60 Sekunden)
        last_sent = request.session.get('last_code_sent')
        if last_sent:
            from datetime import datetime, timedelta
            last_time = datetime.fromisoformat(last_sent)
            if datetime.now() - last_time < timedelta(seconds=60):
                messages.warning(request, 'Bitte warte einen Moment, bevor du einen neuen Code anforderst.')
                return redirect('verify_email')
        
        # Alten Code löschen und neuen generieren
        EmailVerificationCode.objects.filter(user=user).delete()
        code = secrets.token_hex(3).upper()
        EmailVerificationCode.objects.create(user=user, code=code)
        
        # Neue E-Mail senden
        send_verification_email(user, code, request)
        
        # Zeitstempel speichern
        request.session['last_code_sent'] = datetime.now().isoformat()
        request.session['verification_email'] = user.email
        
        messages.success(request, 'Ein neuer Code wurde an deine E-Mail gesendet.')
        return redirect('verify_email')
    
    # Code-Verifizierung
    if request.method == 'POST' and request.POST.get('action') != 'resend':
        code = ''.join([request.POST.get(f'code_{i}', '').upper() for i in range(1, 7)])
        
        try:
            verification = EmailVerificationCode.objects.get(user=user, code=code)
            verification.delete()  # Code löschen nach Verifizierung
            
            # Security-Einstellungen aktualisieren
            securitysettings, _ = UserSecurity.objects.get_or_create(user=user)
            securitysettings.is_deactivated = False
            securitysettings.save()

            user.is_active = True
            user.save()
            
            # Session aufräumen
            del request.session['pending_user_id']
            del request.session['verification_email']
            if 'last_code_sent' in request.session:
                del request.session['last_code_sent']
            
            login(request, user)
            messages.success(request, 'E-Mail verifiziert! Dein Account wurde reaktiviert.')

            # Nach Verifizierung zu ?next= weiterleiten falls gesetzt
            next_url = (
                request.POST.get('next')
                or request.GET.get('next', '')
                or request.session.pop('next_url_after_verify', '')
            ).strip()
            if next_url and next_url.startswith('/') and not next_url.startswith('//'):
                return redirect(next_url)
            return redirect('home')

        except EmailVerificationCode.DoesNotExist:
            messages.error(request, 'Ungültiger oder abgelaufener Code!')

    next_url = (
        request.GET.get('next', '')
        or request.session.get('next_url_after_verify', '')
    ).strip()
    return render(request, 'store/verify_email.html', {
        'email': email,
        'user':  user,
        'next':  next_url,
    })


def change_verification_email(request):
    """
    Erlaubt dem User, eine andere E-Mail für die Verifizierung zu verwenden
    """
    user_id = request.session.get('pending_user_id')
    
    if not user_id:
        return redirect('login')
    
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return redirect('login')
    
    if request.method == 'POST':
        new_email = request.POST.get('new_email', '').strip().lower()
        
        # Validierung
        if not new_email or '@' not in new_email:
            messages.error(request, 'Bitte gib eine gültige E-Mail-Adresse ein.')
            return render(request, 'store/change_email.html')
        
        # Prüfen ob E-Mail bereits vergeben
        if User.objects.filter(email=new_email).exclude(id=user.id).exists():
            messages.error(request, 'Diese E-Mail-Adresse wird bereits verwendet.')
            return render(request, 'store/change_email.html')
        
        # E-Mail aktualisieren
        user.email = new_email
        user.save()
        
        # Neuen Code senden
        EmailVerificationCode.objects.filter(user=user).delete()
        code = secrets.token_hex(3).upper()
        EmailVerificationCode.objects.create(user=user, code=code)
        send_verification_email(user, code, request)
        
        request.session['verification_email'] = new_email
        messages.success(request, f'Verifizierungscode wurde an {new_email} gesendet.')
        return redirect('verify_email')
    
    return render(request, 'store/change_email.html', {'current_email': user.email})

def register_view(request):
    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_active = False  # Deaktivieren, bis E-Mail bestätigt
            user.save()

            # Alte Codes löschen (falls vorhanden)
            EmailVerificationCode.objects.filter(user=user).delete()
            
            # Neuen Code generieren und speichern
            code = secrets.token_hex(3).upper()  # 6-stelliger alphanumerischer Code
            EmailVerificationCode.objects.create(user=user, code=code)

            # HTML-E-Mail senden
            send_verification_email(user, code, request)

            # Session setzen
            request.session['pending_user_id'] = user.id
            request.session['verification_email'] = user.email
            
            messages.success(request, 'Registrierung erfolgreich! Bitte bestätige deine E-Mail-Adresse.')
            return redirect('verify_email')
    else:
        form = CustomUserCreationForm()
    return render(request, 'store/register.html', {'form': form})

def logout_view(request):
    if request.user.is_authenticated:
        try:
            from settings.models import UserProfile
            profile = UserProfile.objects.filter(user=request.user).first()
            if profile and profile.onesignal_player_id:
                # Zuordnung Gerät <-> dieser User entfernen, damit nach dem
                # Logout keine Push-Benachrichtigungen für diesen Account
                # mehr auf diesem Gerät ankommen (siehe OneSignalDevice).
                OneSignalDevice.objects.filter(
                    user=request.user, onesignal_id=profile.onesignal_player_id
                ).delete()
                profile.onesignal_subscribed = False
                profile.save(update_fields=["onesignal_subscribed"])
        except Exception:
            pass
    logout(request)
    return redirect('/?onesignal_logout=1')
    
@login_required
def create_app_view(request):
    developer = request.user.developer
    if request.method == 'POST':
        form = AppWithVersionForm(request.POST, files=request.FILES)
        if form.is_valid():
            app = form.save(developer=developer)
            version = form.save_version(app)

            _upload_version_to_cloud_and_check(request, app, version)

            return redirect('version_app_status_view', version_id=version.id)
    else:
        form = AppWithVersionForm()

    return render(request, 'store/create_app.html', {'form': form})


def version_status_app_view(request, version_id):
    version = get_object_or_404(Version, id=version_id)

    # Rendern einer Status-Seite mit den Infos zur Version und Prüfung
    return render(request, 'store/version_status.html', {'version': version})

def version_status_view(request, version_id):
    version = get_object_or_404(Version, id=version_id)

    return render(request, 'store/version_status.html', {'version': version})


@login_required
def version_status_data(request, version_id):
    """
    Gibt aktuellen Fortschritt und Status zurück (für Live-Update per JS)
    """
    version = get_object_or_404(Version, id=version_id, app__developer__user=request.user)
    data = {
        "status": version.checking_status,
        "progress": version.checking_progress,
        "log": version.checking_log or "",
    }
    return JsonResponse(data)


@csrf_exempt
@login_required
def start_version_check_api(request, version_id):
    """
    Startet die Hintergrundprüfung über AJAX (Button)
    """
    if request.method == "POST":
        version = get_object_or_404(Version, id=version_id, app__developer__user=request.user)

        if version.checking_status not in ['running', 'passed']:
            def run_check():
                start_background_check_version(version.id)

            threading.Thread(target=run_check).start()
            version.checking_status = 'running'
            version.save()
            return JsonResponse({"message": "Prüfung gestartet", "status": "running"})

        return JsonResponse({"message": "Prüfung läuft bereits", "status": version.checking_status})

    return JsonResponse({"error": "Nur POST erlaubt"}, status=405)

@login_required
def version_status_api(request, version_id):
    version = get_object_or_404(Version, id=version_id, app__owner=request.user)
    return JsonResponse({'status': version.checking_status})

@login_required
def developer_dashboard(request):
    try:
        developer = Developer.objects.get(user=request.user)
    except Developer.DoesNotExist:
        return redirect('create_developer')  # Developer-Profil erstellen, falls noch nicht vorhanden

    query = request.GET.get("q", "")
    apps = App.objects.filter(developer=developer).order_by('-created_at')

    if query:
        apps = apps.filter(name__icontains=query)

    apps_with_latest = []
    for app in apps:
        latest_version = app.versions.order_by('-uploaded_at').first()
        apps_with_latest.append({
            'app': app,
            'latest_version': latest_version
        })

    total_downloads = apps.aggregate(total=Sum('download_count'))['total'] or 0

    return render(request, 'store/developer_dashboard.html', {
        'developer': developer,
        'apps_with_latest': apps_with_latest,
        'query': query,
        'total_downloads': total_downloads,
    })

@login_required
def edit_developer_view(request, developer_id):
    developer = get_object_or_404(Developer, id=developer_id, user=request.user)

    if request.method == 'POST':
        form = DeveloperForm(request.POST, request.FILES, instance=developer)
        if form.is_valid():
            form.save()
            messages.success(request, 'Entwicklerprofil erfolgreich aktualisiert.')
            return redirect('developer_dashboard')
    else:
        form = DeveloperForm(instance=developer)

    return render(request, 'store/edit_developer.html', {'form': form, 'developer': developer})

@login_required
def delete_developer_view(request, developer_id):
    developer = get_object_or_404(Developer, id=developer_id, user=request.user)

    if request.method == 'POST':
        # Alle Apps des Entwicklers löschen
        developer.apps.all().delete()
        developer.delete()
        messages.success(request, 'Entwicklerprofil erfolgreich gelöscht.')
        return redirect('home')

    return render(request, 'store/delete_developer.html', {'developer': developer})

@login_required
def app_detail_dev_view(request, app_id):
    app = get_object_or_404(App, id=app_id, developer=request.user.developer)
    versions = app.versions.order_by('-uploaded_at')  # Alle Versionen laden

    return render(request, 'store/app_detail_dev.html', {
        'app': app,
        'versions': versions,
    })

@login_required
def edit_app_view(request, app_id):
    app = get_object_or_404(App, id=app_id, developer=request.user.developer)

    if request.method == 'POST':
        form = AppEditForm(request.POST, request.FILES, instance=app)
        if form.is_valid():
            form.save()
            messages.success(request, 'App erfolgreich aktualisiert.')
            return redirect('developer_dashboard')
    else:
        form = AppEditForm(instance=app)

    return render(request, 'store/edit_app.html', {'form': form, 'app': app})

@login_required
def delete_app_view(request, app_id):
    app = get_object_or_404(App, id=app_id, developer=request.user.developer)
    
    if request.method == 'POST':
        app.delete()
        messages.success(request, 'App erfolgreich gelöscht.')
        return redirect('developer_dashboard')

    return render(request, 'store/delete_app.html', {'app': app})

@login_required
def app_screenshots_view(request, app_id):
    app = get_object_or_404(App, id=app_id, developer=request.user.developer)
    screenshots = app.screenshots.all()

    return render(request, 'store/app_screenshots.html', {
        'app': app,
        'screenshots': screenshots,
    })

@login_required
def upload_screenshots_view(request, app_id):
    app = get_object_or_404(App, id=app_id, developer=request.user.developer)

    if request.method == 'POST':
        files = request.FILES.getlist('screenshots')
        if files:
            for file in files:
                app.screenshots.create(image=file)
            messages.success(request, 'Screenshots erfolgreich hochgeladen.')
            return redirect('app_screenshots', app_id=app.id)
        else:
            messages.error(request, 'Bitte mindestens ein Screenshot hochladen.')

    return render(request, 'store/upload_screenshots.html', {'app': app})

@login_required
def create_developer_view(request):

    if request.method == 'POST':
        form = DeveloperForm(request.POST)
        if form.is_valid():
            developer = form.save(commit=False)
            developer.user = request.user
            developer.save()
            return redirect('developer_dashboard')
    else:
        form = DeveloperForm()

    return render(request, 'store/create_developer.html', {'form': form})

def home(request):
    query = request.GET.get('q', '')
    user = request.user

    if user.is_authenticated:
        notifications = Notification.objects.filter(
            Q(user=user),  # Benachrichtigungen für diesen Nutzer 
            read=False  # Nur ungelesene Benachrichtigungen
        ).order_by('-created_at')
        notifications_count = notifications.count()
    else:
        notifications = Notification.objects.filter(
            user__isnull=True,  # Benachrichtigungen für alle Nutzer
            read=False  # Nur ungelesene Benachrichtigungen
        ).order_by('-created_at')
        notifications_count = Notification.objects.filter(
            user__isnull=True,
            read=False
        ).count()

    # Alle veröffentlichten Apps
    all_apps = App.objects.filter(
        published=True,
        published_at__lte=timezone.now()
    )

    if query:
        all_apps = all_apps.filter(name__icontains=query)

    # Top Downloads
    top_downloads = all_apps.order_by('-download_count')[:9]

    # Trending Apps (letzte 7 Tage)
    seven_days_ago = timezone.now() - timedelta(days=7)
    trending_apps_ids = VersionDownload.objects.filter(
        downloaded_at__gte=seven_days_ago,
        version__app__in=all_apps
    ).values('version__app').annotate(
        downloads_last_week=Count('id')
    ).order_by('-downloads_last_week').values_list('version__app', flat=True)[:10]
    trending_apps = App.objects.filter(id__in=trending_apps_ids)

    # --- Empfehlungen für eingeloggte Nutzer ---
    recommended_apps = []
    if user.is_authenticated:
        # Finde die zuletzt genutzten Apps des Users
        last_downloads = VersionDownload.objects.filter(
            user=user
        ).order_by('-downloaded_at')[:5]

        # Extrahiere Plattform/Kategorie-Präferenzen
        preferred_platforms = set()
        preferred_categories = set()
        for download in last_downloads:
            app = download.version.app
            preferred_platforms.add(app.platform)
            preferred_categories.add(app.category)

        # Finde empfohlene Apps basierend auf diesen Präferenzen
        recommended_apps = App.objects.filter(
            published=True,
            published_at__lte=timezone.now(),
            platform__in=preferred_platforms,
            category__in=preferred_categories
        ).exclude(
            versions__versiondownload__user=user
        ).distinct().order_by('-download_count')[:6]

    context = {
        'query': query,
        'all_apps': all_apps,
        'top_downloads': top_downloads,
        'trending_apps': trending_apps,
        'recommended_apps': recommended_apps,
        'notifications': notifications,
        'notifications_count': notifications_count,
    }
    return render(request, 'store/home.html', context)


def platform_view(request, platform_name):
    query = request.GET.get('q', '')
    apps = App.objects.filter(
        platform__iexact=platform_name,
        published=True,
        published_at__lte=timezone.now()  # Vergangenheit oder jetzt
    )
    if query:
        apps = apps.filter(name__icontains=query)
    context = {'apps': apps, 'platform': platform_name, 'query': query}
    return render(request, 'store/platform.html', context)

@login_required
def notifications_view(request):
    user = request.user
    notifications = Notification.objects.filter(
        Q(user=user) | Q(user__isnull=True)
    ).order_by('-timestamp')
    unread_count = notifications.filter(read=False, user=user).count()

    return render(request, 'store/notifications.html', {
        'notifications': notifications,
        'count': unread_count,
    })

@login_required
def notification_detail(request, pk):
    notification = get_object_or_404(Notification, pk=pk)

    # Falls es eine persönliche Nachricht ist → als gelesen markieren
    if notification.user == request.user and not notification.read:
        notification.read = True
        notification.save()

    return render(request, 'store/notification_detail.html', {
        'notification': notification
    })

@login_required
def mark_all_notifications_read(request):
    if request.method == 'POST':
        Notification.objects.filter(user=request.user, read=False).update(read=True)
    return redirect('notifications_all')  # KORRIGIERT: notifications_all statt notifications

@login_required
def subscribe_notifications(request):
    if request.method == 'POST':
        endpoint = request.POST.get('endpoint')
        if not endpoint:
            messages.error(request, 'Kein Endpoint angegeben.')
            return redirect('notifications_all')

        # Existierende Subscriptions löschen, falls mehrfach vorhanden
        PushSubscription.objects.filter(endpoint=endpoint).delete()

        # Neue Subscription speichern
        PushSubscription.objects.create(
            user=request.user,
            endpoint=endpoint
        )
        messages.success(request, 'Du hast dich erfolgreich für Benachrichtigungen angemeldet.')
    return redirect('notifications_all')

@login_required
def unsubscribe_notifications(request):
    if request.method == 'POST':
        endpoint = request.POST.get('endpoint')
        if not endpoint:
            messages.error(request, 'Kein Endpoint angegeben.')
            return redirect('notifications_all')

        # Subscription löschen
        PushSubscription.objects.filter(user=request.user, endpoint=endpoint).delete()
        messages.success(request, 'Du hast dich erfolgreich von Benachrichtigungen abgemeldet.')
    return redirect('notifications_all')

def developer_detail_view(request, name):
    # Name ggf. entschlüsseln/entschlacken
    developer = get_object_or_404(Developer, name__iexact=name)
    apps = App.objects.filter(developer=developer, published=True)
    total_downloads = apps.aggregate(total=Sum('download_count'))['total'] or 0

    return render(request, 'store/developer_detail.html', {
        'developer': developer,
        'apps': apps,
        'total_downloads': total_downloads,
    })

def _upload_version_to_cloud_and_check(request, app, version):
    """
    Lädt die Versionsdatei sofort zur JDS Cloud hoch und startet die
    Hintergrundprüfung. Gemeinsame Pipeline für Erst-Upload (create_app_view)
    und weitere Versions-Uploads (upload_version).
    """
    from .jds_cloud import upload_file as upload_to_jds_cloud
    import re as _re

    try:
        local_path = version.file.path
        if os.path.isfile(local_path):
            original_name = version.original_filename or os.path.basename(local_path)
            if original_name.lower().endswith('.tar.gz'):
                ext = '.tar.gz'
            else:
                ext = os.path.splitext(original_name)[1].lower()
            upload_name = _re.sub(r'[^A-Za-z0-9._\-]', '_',
                                  f"{app.name}_{version.version_number}{ext}")
            result = upload_to_jds_cloud(local_path, upload_name)
            if result['success']:
                version.jds_cloud_file_id  = result['file_id']
                version.jds_cloud_url      = result['download_url']
                version.jds_cloud_view_url = result['view_url']
                version.save(update_fields=[
                    'jds_cloud_file_id', 'jds_cloud_url', 'jds_cloud_view_url'
                ])
            else:
                messages.warning(request,
                    f"JDS Cloud Upload fehlgeschlagen: {result.get('error', 'Unbekannt')}. "
                    "Prüfung wird trotzdem gestartet."
                )
    except Exception as _upload_err:
        messages.warning(request,
            f"JDS Cloud Upload Fehler: {_upload_err}. Prüfung wird trotzdem gestartet."
        )

    try:
        start_background_check.delay(version.id)
        messages.success(request, 'Neue Version hochgeladen. Prüfung läuft.')
    except KombuOpError:
        messages.warning(request,
            'Version hochgeladen – konnte Hintergrundprüfung nicht starten (Broker nicht erreichbar).'
        )


@login_required
def upload_version(request, app_id):
    app = get_object_or_404(App, id=app_id, developer=request.user.developer)
    if request.method == 'POST':
        form = VersionForm(request.POST, request.FILES)
        if form.is_valid():
            version = form.save(commit=False)
            version.app = app
            version.checking_status = 'pending'
            version.approved = False
            # Originalen Dateinamen speichern (bevor Django ihn ggf. umbenennt)
            uploaded_file = request.FILES.get('file')
            if uploaded_file:
                version.original_filename = uploaded_file.name
            version.save()

            _upload_version_to_cloud_and_check(request, app, version)

            return redirect('version_status', version_id=version.id)
    else:
        form = VersionForm()

    return render(request, 'store/upload_version.html', {'form': form, 'app': app})



def app_detail_view(request, app_id):
    app = get_object_or_404(App, id=app_id, published=True)
    
    latest_version = app.versions.filter(
        approved=True,
        new_version=True
    ).order_by('-uploaded_at').first()

    older_versions = app.versions.filter(approved=True).exclude(
        id=latest_version.id if latest_version else None
    ).order_by('-uploaded_at')

    suggestions = App.objects.filter(
        platform=app.platform,
        published=True,
        published_at__lte=timezone.now()
    ).filter(
        Q(category=app.category) | Q(subcategory=app.subcategory)
    ).exclude(id=app.id).distinct()[:8]

    user_installed_version = None
    if request.user.is_authenticated:
        vd = VersionDownload.objects.filter(
            user=request.user, version__app=app
        ).order_by('-version__uploaded_at').first()
        if vd:
            user_installed_version = vd.version

    reviews = app.reviews.select_related('user').exclude(user=request.user if request.user.is_authenticated else None)
    user_review = None
    if request.user.is_authenticated:
        user_review = app.reviews.filter(user=request.user).first()
    review_form = AppReviewForm(instance=user_review)

    return render(request, 'store/app_detail.html', {
        'app': app,
        'latest_version': latest_version,
        'older_versions': older_versions,
        'suggestions': suggestions,
        'user_installed_version': user_installed_version,
        'reviews': reviews,
        'user_review': user_review,
        'review_form': review_form,
    })


@login_required
def submit_review(request, app_id):
    app = get_object_or_404(App, id=app_id, published=True)
    existing = AppReview.objects.filter(app=app, user=request.user).first()

    if request.method == 'POST':
        form = AppReviewForm(request.POST, instance=existing)
        if form.is_valid():
            review = form.save(commit=False)
            review.app = app
            review.user = request.user
            review.save()
            if not existing:
                from .tasks import notify_new_review
                notify_new_review(review)
            messages.success(request, 'Danke für deine Bewertung!')
        else:
            messages.error(request, 'Bitte gib eine gültige Bewertung ab.')

    return redirect('app_detail', app_id=app.id)

# download_file_view – weiter unten definiert (JDS Cloud + Fallback)

@csrf_exempt
@login_required
def download_complete(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        version_id = data.get('version_id')
        version = get_object_or_404(Version, id=version_id)
        if version.file and os.path.isfile(version.file.path):
            try:
                os.remove(version.file.path)
            except Exception as e:
                print(f"Fehler beim Löschen der Datei: {e}")
        return JsonResponse({'status': 'ok'})
    return JsonResponse({'status': 'error'}, status=400)

@csrf_exempt
@login_required
def api_increment_download(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        version_id = data.get('version_id')
        version = get_object_or_404(Version, id=version_id, approved=True)
        app = version.app
        VersionDownload.objects.get_or_create(user=request.user, version=version)
        App.objects.filter(id=app.id).update(download_count=F('download_count') + 1)
        app.refresh_from_db(fields=['download_count', 'last_download_milestone'])
        from .tasks import notify_download_milestone
        notify_download_milestone(app)
        return JsonResponse({'status': 'ok'})
    return JsonResponse({'status': 'error'}, status=400)










# download old
@login_required
def download_app_view(request, version_id):
    version = get_object_or_404(Version, id=version_id, approved=True)
    
    confirmed = request.session.get(f'download_confirmed_{version_id}', False)
    
    if request.method == 'POST' and not confirmed:
        request.session[f'download_confirmed_{version_id}'] = True
        request.session[f'download_ready_{version_id}'] = True
        return redirect('download_success', version_id=version_id)

    if not confirmed:
        return render(request, 'store/download_confirm.html', {'version': version})
    
    # Wenn schon bestätigt, direkt zur Success-Seite
    return redirect('download_success', version_id=version_id)


@login_required
def download_app_start(request, version_id):
    version = get_object_or_404(Version, id=version_id, approved=True)
    app = version.app

    # Downloadzähler erhöhen
    App.objects.filter(id=app.id).update(download_count=F('download_count') + 1)

    # User-Download-Tracking
    VersionDownload.objects.get_or_create(user=request.user, version=version)

    # User-Agent erkennen
    user_agent = request.META.get('HTTP_USER_AGENT', '').lower()
    is_mobile = any(mobile_str in user_agent for mobile_str in ['android', 'iphone', 'ipad', 'mobile'])

    if is_mobile:
        context = {'version': version, 'is_mobile': True}
        return render(request, 'store/mobile_download.html', context)
    else:
        return redirect('download_file', version_id=version.id)



@login_required
def download_file_view(request, version_id):
    """
    Download einer App-Version.
    - Wenn approved=True:  direkt herunterladen (Cloud → Fallback lokale Datei)
    - Wenn approved=False: Bestätigungsseite zeigen (ungeprüfte Datei)
    """
    version = get_object_or_404(Version, id=version_id)

    # ── Ungeprüfte Version: Bestätigung einholen ──────────────────────────
    if not version.approved:
        confirmed = request.GET.get('confirmed') == '1'
        if not confirmed:
            return render(request, 'store/download_unreviewed_confirm.html', {
                'version': version,
                'app':     version.app,
            })

    # ── 1. Bevorzuge JDS Cloud URL (falls vorhanden) ──────────────────────
    if version.jds_cloud_url:
        from django.http import HttpResponseRedirect
        return HttpResponseRedirect(version.jds_cloud_url)

    # ── 2. Fallback: lokale Mediendatei ───────────────────────────────────
    file_path = None
    try:
        raw = version.file.path
        if os.path.isfile(raw):
            file_path = raw
        else:
            candidate = os.path.join(settings.MEDIA_ROOT, str(version.file))
            if os.path.isfile(candidate):
                file_path = candidate
    except (ValueError, NotImplementedError):
        pass

    if not file_path:
        return HttpResponseNotFound("Datei nicht gefunden / File not found")

    filename = os.path.basename(file_path)
    if filename.endswith('.apk'):
        content_type = 'application/vnd.android.package-archive'
    else:
        content_type = 'application/octet-stream'

    return FileResponse(
        open(file_path, 'rb'),
        as_attachment=True,
        filename=filename,
        content_type=content_type,
    )



@csrf_exempt
@login_required
def download_complete_1(request):
    """
    Wird vom Client nach erfolgreicher Installation aufgerufen.
    Hier kann die APK-Datei gelöscht werden.
    """
    if request.method == 'POST':
        data = json.loads(request.body)
        version_id = data.get('version_id')

        version = get_object_or_404(Version, id=version_id)

        # APK-Datei löschen
        if version.file and os.path.isfile(version.file.path):
            try:
                os.remove(version.file.path)
            except Exception as e:
                print(f"Fehler beim Löschen der APK: {e}")

        return JsonResponse({'status': 'ok'})

    return JsonResponse({'status': 'error'}, status=400)

@login_required
def save_onesignal_id(request):
    """
    Speichert die OneSignal Player-/Subscription-ID des eingeloggten Users.
    Der Versand nutzt sowohl external_id = user.id (via median.onesignal.login())
    als auch diese Zuordnung (OneSignalDevice) als Fallback - letztere merkt sich
    ALLE User, die je auf diesem Gerät eingeloggt waren, nicht nur den zuletzt
    aktiven, damit z.B. ein Entwickler- und ein Privat-Account auf demselben
    Gerät beide ihre Pushes bekommen.
    """
    if request.method != "POST":
        return JsonResponse({"status": "error"}, status=400)

    from settings.models import UserProfile
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "error": "invalid json"}, status=400)

    player_id = (data.get("onesignal_id") or "").strip()
    if not player_id:
        return JsonResponse({"status": "error", "error": "missing onesignal_id"}, status=400)

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    profile.onesignal_player_id = player_id
    profile.onesignal_subscribed = True
    profile.save(update_fields=["onesignal_player_id", "onesignal_subscribed"])

    OneSignalDevice.objects.get_or_create(user=request.user, onesignal_id=player_id)

    return JsonResponse({"status": "ok"})


@csrf_exempt
def save_push_subscription(request):
    if request.method == "POST" and request.user.is_authenticated:
        data = json.loads(request.body)
        endpoint = data["data"]["endpoint"]
        # Update oder erstellen
        subscription, _ = PushSubscription.objects.update_or_create(
            user=request.user,
            endpoint=endpoint,
            defaults={"data": data["data"]}
        )
        return JsonResponse({"status": "ok"})
    return JsonResponse({"status": "error"}, status=400)

@login_required
def my_installed_apps(request):
    latest_installs = VersionDownload.objects.filter(user=request.user)\
        .select_related('version__app')\
        .order_by('version__app', '-version__uploaded_at')

    seen = set()
    installed_latest = []

    for item in latest_installs:
        app_id = item.version.app.id
        if app_id not in seen:
            installed_latest.append(item)
            seen.add(app_id)

    updates = []
    for item in installed_latest:
        app = item.version.app
        latest_version = app.versions.filter(
            approved=True,
            new_version=True
        ).order_by('-uploaded_at').first()

        if latest_version and latest_version.version_number != item.version.version_number:
            updates.append({
                'download': item,
                'latest_version': latest_version,
                'missing_versions': Version.objects.filter(app=app, uploaded_at__gt=item.version.uploaded_at).count(),
                'release_notes': latest_version.release_notes
            })

    return render(request, 'store/my_installed_apps.html', {
        'installed_apps': installed_latest,
        'updates': updates
    })


def jds_appstore_apps(request):
    apps = App.objects.filter(name__icontains="JDS Appstore")
    return render(request, "store/jds_apps.html", {"apps": apps})

@login_required
def developer_list(request):
    developers = Developer.objects.annotate(
        total_downloads=Sum('apps__download_count', filter=Q(apps__published=True))
    ).order_by('name')
    return render(request, 'store/developer_list.html', {'developers': developers})




@login_required
@staff_member_required
def media_view(request):
    rel_path = request.GET.get("path", "")  # z. B. "images/icons"
    abs_path = os.path.join(settings.MEDIA_ROOT, rel_path)

    if not abs_path.startswith(str(settings.MEDIA_ROOT)):
        return render(request, "store/media_view.html", {"error": "Ungültiger Pfad"})

    items = []
    if os.path.exists(abs_path):
        for entry in os.scandir(abs_path):
            items.append({
                "name": entry.name,
                "is_dir": entry.is_dir(),
                "path": os.path.join(rel_path, entry.name).replace("\\", "/"),
            })

    breadcrumbs = rel_path.split("/") if rel_path else []
    return render(request, "store/media_view.html", {
        "items": items,
        "rel_path": rel_path,
        "breadcrumbs": breadcrumbs,
    })

@login_required
def media_file_view(request, path):
    abs_path = os.path.join(settings.MEDIA_ROOT, path)

    if not abs_path.startswith(str(settings.MEDIA_ROOT)):
        return HttpResponseForbidden("Ungültiger Pfad")

    if not os.path.exists(abs_path):
        return HttpResponseNotFound("Datei nicht gefunden")

    content_type, _ = mimetypes.guess_type(abs_path)
    if content_type is None:
        content_type = 'application/octet-stream'

    response = FileResponse(open(abs_path, 'rb'), content_type=content_type)
    response['Content-Disposition'] = f'inline; filename="{os.path.basename(abs_path)}"'
    return response

def info_page(request):
    updates = AppUpdate.objects.order_by('-date')[:10]

    cutoff = timezone.now() - timedelta(weeks=20)
    roadmap = RoadmapItem.objects.filter(
        Q(status='geplant') |
        Q(status='in_arbeit') |
        (Q(status='abgeschlossen') & Q(date__gte=cutoff))
    ).order_by('date')

    return render(request, 'store/infoseite.html', {
        'updates': updates,
        'roadmap': roadmap,
        'all_done': not RoadmapItem.objects.exclude(status='abgeschlossen').exists(),
    })

# views.py
from django.http import JsonResponse

def api_all_apps(request):
    apps = App.objects.all().select_related('developer')[:50]
    return JsonResponse([{
        'id': app.id,
        'name': app.name,
        'icon': app.icon.url,
        'developer': app.developer.name,
        'platform': app.platform,
    } for app in apps], safe=False)

from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from .models import VersionDownload, Version

@require_POST
@login_required
def track_download(request, version_id):
    try:
        version = Version.objects.select_related('app').get(id=version_id)
        # Verhindere doppelte Einträge durch unique_together Constraint
        VersionDownload.objects.get_or_create(
            user=request.user,
            version=version
        )
        # Erhöhe den Download-Counter der App atomar (verhindert Race Conditions
        # bei gleichzeitigen Downloads, die sonst Zählungen verlieren würden)
        App.objects.filter(id=version.app_id).update(download_count=F('download_count') + 1)
        version.app.refresh_from_db(fields=['download_count', 'last_download_milestone'])
        from .tasks import notify_download_milestone
        notify_download_milestone(version.app)
        return JsonResponse({'success': True})
    except Version.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Version nicht gefunden'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


import secrets
from django.shortcuts import redirect
from django.conf import settings
import requests

def sso_connect(request):
    """Startet SSO-Connect zu Joel Digitals"""
    print("\n" + "🟢" * 40)
    print("FUNCTION: sso_connect - AUFTRAGNETZ")
    print("🟢" * 40)
    
    # State generieren
    state = secrets.token_urlsafe(32)
    
    print(f"📝 State generiert: {state}")
    print(f"🌐 Session Key vorher: {request.session.session_key}")
    print(f"🌐 Session Keys vorher: {list(request.session.keys())}")
    
    # !!! WICHTIG: Session muss existieren BEVOR wir speichern !!!
    if not request.session.session_key:
        # Force Django to create a session
        request.session.create()
        print(f"✨ Neue Session erstellt: {request.session.session_key}")
    
    # State speichern
    request.session['sso_state'] = state
    request.session.modified = True
    request.session.save()
    
    print(f"💾 Session Key nachher: {request.session.session_key}")
    print(f"💾 State gespeichert: {request.session.get('sso_state')}")
    print(f"💾 Alle Session Keys: {list(request.session.keys())}")
    
    # Redirect URL
    sso_url = (
        f"{settings.SSO_PROVIDER_URL}/auth/sso/connect/"
        f"?client_id={settings.SSO_CLIENT_ID}"
        f"&redirect_uri={settings.SSO_CALLBACK_URL}"
        f"&state={state}"
    )
    
    print(f"↗️  Redirect zu: {sso_url}")
    print("🟢" * 40 + "\n")
    
    response = redirect(sso_url)
    
    # !!! WICHTIG: Session-Cookie muss gesetzt werden !!!
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=request.session.session_key,
        max_age=settings.SESSION_COOKIE_AGE,
        httponly=True,
        samesite='Lax',
    )
    
    return response

def sso_callback(request):
    """Empfängt SSO Token und erstellt/logged User ein"""
    print("\n" + "=" * 80)
    print("🔙 SSO CALLBACK - START")
    print("=" * 80)
    
    token = request.GET.get('token')
    state = request.GET.get('state')
    
    # State-Validierung
    stored_state = request.session.get('sso_state')
    
    if not stored_state and state:
        print("⚠️  WARNING: Session-State fehlt - akzeptiere State aus URL (DEV ONLY!)")
        request.session['sso_state'] = state
        stored_state = state
    
    if state != stored_state:
        print("❌ FEHLER: State Mismatch!")
        return redirect('/accounts/register/?error=invalid_state')
    
    print("✅ State validiert!")
    
    if not token:
        print("❌ FEHLER: Kein Token")
        return redirect('/accounts/register/?error=no_token')
    
    # Token validieren
    print(f"\n🔍 Validiere Token bei SSO Provider...")
    try:
        response = requests.post(
            f"{settings.SSO_PROVIDER_URL}/api/sso/validate/",
            data={
                'token': token,
                'client_id': settings.SSO_CLIENT_ID,
                'client_secret': settings.SSO_CLIENT_SECRET,
            },
            timeout=10,
        )
        
        print(f"   Response Status: {response.status_code}")
        
        if response.status_code != 200:
            print(f"❌ Token Validation fehlgeschlagen: {response.text}")
            return redirect('/accounts/register/?error=validation_failed')
        
        user_data = response.json()
        print(f"✅ Token validiert, User-Daten erhalten:")
        print(f"   Email: {user_data.get('email')}")
        print(f"   Username: {user_data.get('username')}")
        print(f"   First Name: {user_data.get('first_name')}")
        print(f"   Last Name: {user_data.get('last_name')}")
        
        # Zuerst: Prüfe ob User mit dieser EMAIL bereits existiert
        try:
            user = User.objects.get(email=user_data['email'])
            print(f"✅ Bestehender User gefunden (via Email): {user.email}")
            
            # Update User-Daten falls sich was geändert hat
            user.first_name = user_data.get('first_name', user.first_name)
            user.last_name = user_data.get('last_name', user.last_name)
            user.is_active = True
            user.email_confirmed = True
            user.save()
            print(f"📝 User-Daten aktualisiert")
            
        except User.DoesNotExist:
            # User existiert noch nicht → Erstellen
            print(f"📝 Erstelle neuen User...")
            
            # Generiere eindeutigen Username falls nötig
            base_username = user_data.get('username', user_data['email'].split('@')[0])
            username = base_username
            counter = 1
            
            # Prüfe ob Username bereits existiert
            while User.objects.filter(username=username).exists():
                username = f"{base_username}_{counter}"
                counter += 1
                print(f"   Username '{base_username}' existiert bereits, versuche '{username}'")
            
            user = User.objects.create(
                email=user_data['email'],
                username=username,
                first_name=user_data.get('first_name', ''),
                last_name=user_data.get('last_name', ''),
                is_active=True,
            )
            
            # Setze unbrauchbares Passwort (SSO-User)
            user.set_unusable_password()
            user.save()
            
            print(f"✨ Neuer SSO-User erstellt: {user.email} (Username: {user.username})")

            print(f"\n🔓 Logge User ein...")
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            record_login_session(user, request)
            print(f"✅ User eingeloggt: {user.email}")

            # Session cleanup
            if 'sso_state' in request.session:
                del request.session['sso_state']
            if 'sso_user_data' in request.session:
                del request.session['sso_user_data']

            print("\n" + "=" * 80)
            print("✅ SSO LOGIN - KOMPLETT ERFOLGREICH")
            print("=" * 80 + "\n")

            return redirect('/')  # Zur Startseite oder Dashboard

        # User einloggen
        print(f"\n🔓 Logge User ein...")
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        if record_login_session(user, request):
            notify_security_event(
                user,
                title="Neue Anmeldung erkannt",
                message=f"Dein Konto wurde per Joel Digitals SSO von einem neuen Gerät aus angemeldet ({request.META.get('REMOTE_ADDR', 'unbekannte IP')}). Warst du das nicht, ändere sofort dein Passwort.",
            )
        print(f"✅ User eingeloggt: {user.email}")

        # Session cleanup
        if 'sso_state' in request.session:
            del request.session['sso_state']
        if 'sso_user_data' in request.session:
            del request.session['sso_user_data']
        
        print("\n" + "=" * 80)
        print("✅ SSO LOGIN - KOMPLETT ERFOLGREICH")
        print("=" * 80 + "\n")
        
        return redirect('/')  # Zur Startseite oder Dashboard
        
    except requests.RequestException as e:
        print(f"❌ SSO Request Error: {e}")
        print("=" * 80 + "\n")
        return redirect('/accounts/register/?error=connection_failed')


def sso_login(request):
    """Startet SSO Login Flow"""
    print("\n" + "=" * 80)
    print("🚀 SSO LOGIN FLOW - START")
    print("=" * 80)
    
    # Zeige Request-Info
    print(f"\n🌐 Request Info:")
    print(f"   Path: {request.path}")
    print(f"   Method: {request.method}")
    print(f"   User: {request.user}")
    print(f"   Session Key (vorher): {request.session.session_key}")
    
    # State generieren
    state = secrets.token_urlsafe(32)
    
    print(f"\n📝 State generiert: {state}")
    
    # Session-Status VORHER
    print(f"\n💾 Session VORHER:")
    print(f"   Session Key: {request.session.session_key}")
    print(f"   Session Keys: {list(request.session.keys())}")
    print(f"   Session ist leer: {request.session.is_empty()}")
    
    # State speichern
    request.session['sso_state'] = state
    request.session.modified = True
    request.session.save()
    
    # Session-Status NACHHER
    print(f"\n💾 Session NACHHER:")
    print(f"   Session Key: {request.session.session_key}")
    print(f"   sso_state: {request.session.get('sso_state')}")
    print(f"   Alle Keys: {list(request.session.keys())}")
    print(f"   Session wurde gespeichert: {request.session.get('sso_state') == state}")
    
    # Redirect URL
    sso_url = (
        f"{settings.SSO_PROVIDER_URL}/auth/sso/connect/"
        f"?client_id={settings.SSO_CLIENT_ID}"
        f"&redirect_uri={settings.SSO_CALLBACK_URL}"
        f"&state={state}"
    )
    
    print(f"\n↗️  Redirect zu: {sso_url}")
    print("=" * 80 + "\n")
    
    return redirect(sso_url)

def terms_of_service_view(request):
    """Nutzungsbedingungen anzeigen"""
    return render(request, 'store/terms_of_service.html')

def privacy_policy_view(request):
    """Datenschutzerklärung anzeigen"""
    return render(request, 'store/privacy_policy.html')
