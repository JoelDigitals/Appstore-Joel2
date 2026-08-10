from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib.auth import logout, authenticate, update_session_auth_hash
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from datetime import timedelta

from .models import (
    UserProfile, 
    NotificationSettings, 
    UserSecurity,
    UserActivity,
    APIToken,
    DownloadHistory,
    UserSession
)


@login_required
def user_profile_view(request): 
    profile = request.user.profile
    # Zusätzliche Statistiken
    download_count = DownloadHistory.objects.filter(user=request.user).count()
    favorite_count = getattr(request.user, 'favorites', []).count() if hasattr(request.user, 'favorites') else 0
    app_count = getattr(request.user, 'apps', []).count() if hasattr(request.user, 'apps') else 0
    
    context = {
        'profile': profile,
        'download_count': download_count,
        'favorite_count': favorite_count,
        'app_count': app_count,
    }
    return render(request, 'settings/user_profile.html', context)


@login_required
def edit_user_profile_view(request):
    profile = request.user.profile
    if request.method == 'POST':
        profile.avatar = request.FILES.get('avatar', profile.avatar)
        profile.bio = request.POST.get('bio', profile.bio)
        profile.birth_date = request.POST.get('birth_date', profile.birth_date) or None
        profile.website = request.POST.get('website', profile.website)
        profile.location = request.POST.get('location', profile.location)
        profile.social_links = request.POST.get('social_links', profile.social_links)
        profile.email = request.POST.get('email', profile.email)
        profile.phone_number = request.POST.get('phone_number', profile.phone_number)
        profile.save()
        messages.success(request, "Profil erfolgreich aktualisiert.")
        return redirect('user_profile')
    return render(request, 'settings/edit_user_profile.html', {'profile': profile})


@login_required
def delete_user_profile_view(request):
    if request.method == 'POST':
        password = request.POST.get('password')
        user = authenticate(username=request.user.username, password=password)
        if user:
            user.delete()
            messages.success(request, "Dein Profil wurde gelöscht.")
            return redirect('home')
        else:
            messages.error(request, "Falsches Passwort.")
    return render(request, 'settings/delete_user_profile.html', {'profile': request.user.profile})


@login_required
def user_settings_view(request):
    return render(request, 'settings/user_settings.html')


@login_required
def notification_settings_view(request):
    settings, created = NotificationSettings.objects.get_or_create(user=request.user)
    
    if request.method == 'POST':
        settings.email_notifications = request.POST.get('email_notifications') == 'on'
        settings.push_notifications = request.POST.get('push_notifications') == 'on'
        settings.sms_notifications = request.POST.get('sms_notifications') == 'on'
        settings.email_updates = request.POST.get('email_updates') == 'on'
        settings.email_security = request.POST.get('email_security') == 'on'
        settings.email_marketing = request.POST.get('email_marketing') == 'on'
        settings.push_downloads = request.POST.get('push_downloads') == 'on'
        settings.push_updates = request.POST.get('push_updates') == 'on'
        settings.push_messages = request.POST.get('push_messages') == 'on'
        settings.quiet_start = request.POST.get('quiet_start', '22:00')
        settings.quiet_end = request.POST.get('quiet_end', '08:00')
        settings.save()
        messages.success(request, "Benachrichtigungseinstellungen gespeichert.")
        return redirect('user_settings')
    
    return render(request, 'settings/notification_settings.html', {'settings': settings})


@login_required
def security_settings(request):
    security, _ = UserSecurity.objects.get_or_create(user=request.user)
    
    # Letzte Anmeldungen abrufen
    recent_sessions = UserSession.objects.filter(
        user=request.user,
        created_at__gte=timezone.now() - timedelta(days=30)
    ).order_by('-created_at')[:5]

    if request.method == 'POST':
        action = request.POST.get('action')
        password = request.POST.get('password')
        user = authenticate(username=request.user.username, password=password)

        if not user:
            messages.error(request, "Falsches Passwort.")
            return redirect('security_settings')

        if action == 'deactivate':
            user.is_active = False
            user.save()
            security.is_deactivated = True
            security.save()
            logout(request)
            messages.success(request, "Konto deaktiviert.")
            return redirect('login')

        elif action == 'delete':
            user.delete()
            messages.success(request, "Konto gelöscht.")
            return redirect('home')

        elif action == 'disable_2fa':
            security.two_factor_enabled = False
            security.save()
            messages.success(request, "Zwei-Faktor-Authentifizierung deaktiviert.")
            return redirect('security_settings')

    return render(request, 'settings/security_settings.html', {
        'security': security,
        'recent_sessions': recent_sessions
    })


@login_required
@require_POST
def tfa_check_link(request):
    """Prueft, OHNE etwas anzulegen, ob das VERKNUEPFTE Joel-Digitals-Konto
    (security.joel_digitals_email, bestaetigt via tfa_link_start - NICHT
    request.user.email) per Push erreichbar ist - Voraussetzung, bevor 2FA
    ueberhaupt aktivierbar gemacht wird (verhindert Selbstaussperrung)."""
    security, _ = UserSecurity.objects.get_or_create(user=request.user)
    if not security.joel_digitals_email:
        return JsonResponse({'linked': False, 'reachable': False})

    from store.login_approval_client import check_account_link_status
    status_code, body = check_account_link_status(security.joel_digitals_email)
    if status_code != 200:
        return JsonResponse({'error': 'connection_failed'}, status=502)
    return JsonResponse(body)


@login_required
@require_POST
def tfa_pairing_test(request):
    """Loest einen echten Push-Bestaetigungs-Roundtrip aus (purpose=
    pairing_test) an das verknuepfte Konto, bevor 2FA scharf geschaltet
    wird."""
    security, _ = UserSecurity.objects.get_or_create(user=request.user)
    if not security.joel_digitals_email:
        return JsonResponse({'error': 'not_linked'}, status=409)

    from store.login_approval_client import create_login_approval_request
    from store.views import get_client_ip
    status_code, body = create_login_approval_request(
        email=security.joel_digitals_email, purpose='pairing_test',
        ip=get_client_ip(request) or '', context='JDS AppStore Geräte-Verknüpfung',
    )
    if status_code != 200:
        return JsonResponse(body, status=status_code if status_code < 500 else 502)
    request.session['pending_pairing_token'] = body['token']
    return JsonResponse(body)


@login_required
def tfa_pairing_status(request):
    from store.login_approval_client import check_login_approval_status
    token = request.session.get('pending_pairing_token')
    if not token:
        return JsonResponse({'error': 'no_pending_request'}, status=400)
    status_code, body = check_login_approval_status(token)
    if status_code != 200:
        return JsonResponse({'status': 'error'})
    return JsonResponse(body)


@login_required
@require_POST
def tfa_enable(request):
    """Aktiviert 2FA erst, nachdem der Pairing-Test-Token server-seitig
    erneut als 'approved' bestaetigt wurde - kein Vertrauen in den
    Client-Status."""
    from store.login_approval_client import check_login_approval_status
    token = request.session.get('pending_pairing_token')
    if not token:
        return JsonResponse({'error': 'no_pending_request'}, status=400)

    status_code, body = check_login_approval_status(token)
    if status_code != 200 or body.get('status') != 'approved':
        return JsonResponse({'error': 'not_approved'}, status=409)

    security, _ = UserSecurity.objects.get_or_create(user=request.user)
    security.two_factor_enabled = True
    security.save(update_fields=['two_factor_enabled'])
    request.session.pop('pending_pairing_token', None)
    return JsonResponse({'enabled': True})


@login_required
def change_password_view(request):
    if request.method == 'POST':
        current_password = request.POST.get('current_password')
        new_password = request.POST.get('new_password')
        confirm_password = request.POST.get('confirm_password')
        
        # Validierung
        if not request.user.check_password(current_password):
            messages.error(request, "Aktuelles Passwort ist falsch.")
            return redirect('change_password')
        
        if new_password != confirm_password:
            messages.error(request, "Die neuen Passwörter stimmen nicht überein.")
            return redirect('change_password')
        
        if len(new_password) < 8:
            messages.error(request, "Passwort muss mindestens 8 Zeichen lang sein.")
            return redirect('change_password')
        
        request.user.set_password(new_password)
        request.user.save()
        update_session_auth_hash(request, request.user)  # Session beibehalten

        from store.tasks import notify_security_event
        notify_security_event(
            request.user,
            title="Passwort geändert",
            message="Das Passwort für dein Konto wurde soeben geändert. Warst du das nicht, kontaktiere umgehend den Support.",
        )

        messages.success(request, "Passwort erfolgreich geändert.")
        return redirect('user_settings')
    
    return render(request, 'settings/change_password.html')


@login_required
def appearance_settings_view(request):
    profile = request.user.profile
    
    if request.method == 'POST':
        profile.theme = request.POST.get('theme', 'dark')
        profile.accent_color = request.POST.get('accent_color', 'blue')
        profile.font_size = request.POST.get('font_size', 'medium')
        profile.compact_mode = request.POST.get('compact_mode') == 'on'
        profile.animations_enabled = request.POST.get('animations_enabled') != 'off'
        profile.save()
        messages.success(request, "Erscheinungsbild aktualisiert.")
        return redirect('user_settings')
    
    return render(request, 'settings/appearance_settings.html', {'profile': profile})


@login_required
def language_settings_view(request):
    profile = request.user.profile
    
    if request.method == 'POST':
        profile.language = request.POST.get('language', 'de')
        profile.timezone = request.POST.get('timezone', 'Europe/Berlin')
        profile.date_format = request.POST.get('date_format', 'DMY')
        profile.save()
        
        # Sprache in Session speichern für Middleware
        request.session['language'] = profile.language
        
        messages.success(request, "Spracheinstellungen gespeichert.")
        return redirect('user_settings')
    
    # Verfügbare Sprachen und Zeitzonen
    languages = [
        ('de', 'Deutsch'),
        ('en', 'English'),
        ('fr', 'Français'),
        ('es', 'Español'),
    ]
    
    timezones = [
        ('Europe/Berlin', 'Berlin (UTC+1)'),
        ('Europe/London', 'London (UTC+0)'),
        ('America/New_York', 'New York (UTC-5)'),
        ('America/Los_Angeles', 'Los Angeles (UTC-8)'),
        ('Asia/Tokyo', 'Tokyo (UTC+9)'),
    ]
    
    return render(request, 'settings/language_settings.html', {
        'profile': profile,
        'languages': languages,
        'timezones': timezones
    })


@login_required
def privacy_settings_view(request):
    profile = request.user.profile
    
    if request.method == 'POST':
        profile.profile_public = request.POST.get('profile_public') == 'on'
        profile.show_email = request.POST.get('show_email') == 'on'
        profile.show_activity = request.POST.get('show_activity') == 'on'
        profile.allow_analytics = request.POST.get('allow_analytics') != 'off'
        profile.allow_personalization = request.POST.get('allow_personalization') == 'on'
        profile.save()
        messages.success(request, "Datenschutzeinstellungen gespeichert.")
        return redirect('user_settings')
    
    return render(request, 'settings/privacy_settings.html', {'profile': profile})


@login_required
def download_history_view(request):
    downloads = DownloadHistory.objects.filter(
        user=request.user
    ).select_related('app', 'version').order_by('-downloaded_at')[:50]
    
    return render(request, 'settings/download_history.html', {'downloads': downloads})


@login_required
def connected_devices_view(request):
    sessions = UserSession.objects.filter(
        user=request.user,
        expires_at__gt=timezone.now()
    ).order_by('-created_at')
    
    # Aktuelle Session markieren
    current_session_key = request.session.session_key
    for session in sessions:
        session.is_current = session.session_key == current_session_key
    
    return render(request, 'settings/connected_devices.html', {'sessions': sessions})


@login_required
def revoke_device(request, session_id):
    session = get_object_or_404(UserSession, id=session_id, user=request.user)
    
    # Eigene Session nicht beenden
    if session.session_key == request.session.session_key:
        messages.error(request, "Du kannst deine aktuelle Session nicht beenden.")
        return redirect('connected_devices')
    
    session.delete()
    messages.success(request, "Gerät erfolgreich abgemeldet.")
    return redirect('connected_devices')


@login_required
def api_keys_view(request):
    tokens = APIToken.objects.filter(user=request.user, is_active=True).order_by('-created_at')
    return render(request, 'settings/api_keys.html', {'tokens': tokens})


@login_required
def generate_api_key(request):
    if request.method == 'POST':
        name = request.POST.get('name', 'API Key')
        token = APIToken.objects.create(
            user=request.user,
            name=name,
            key=APIToken.generate_key(),
            expires_at=timezone.now() + timedelta(days=365)
        )
        messages.success(request, f"API-Schlüssel '{name}' erstellt.")
        # Key nur einmal anzeigen
        return render(request, 'settings/api_key_created.html', {'token': token})
    
    return redirect('api_keys')


@login_required
def revoke_api_key(request, key_id):
    token = get_object_or_404(APIToken, id=key_id, user=request.user)
    token.is_active = False
    token.save()
    messages.success(request, "API-Schlüssel widerrufen.")
    return redirect('api_keys')


@login_required
def billing_view(request):
    # Platzhalter für zukünftige Abrechnungsfunktion
    return render(request, 'settings/billing.html')