from django.db import models
from django.contrib.auth.models import User
from django.conf import settings
from django.utils import timezone
from django.core.exceptions import ValidationError
from datetime import timedelta
import secrets
import string

# Import Store-Models
from store.models import App, Version


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    bio = models.TextField(blank=True)
    birth_date = models.DateField(blank=True, null=True)
    website = models.URLField(blank=True)
    location = models.CharField(max_length=100, blank=True)
    social_links = models.JSONField(default=dict, blank=True)
    email = models.EmailField(blank=True)
    phone_number = models.CharField(max_length=15, blank=True)

    # OneSignal (userbezogene Push-Benachrichtigungen). Der Versand läuft über
    # external_id = user.id, diese Felder dienen nur der Referenz/Anzeige.
    onesignal_player_id = models.CharField(max_length=64, blank=True, default='')
    onesignal_subscribed = models.BooleanField(default=False)

    # Erscheinungsbild Einstellungen
    THEME_CHOICES = [
        ('dark', 'Dunkel'),
        ('light', 'Hell'),
        ('auto', 'System'),
    ]
    theme = models.CharField(max_length=10, choices=THEME_CHOICES, default='dark')
    accent_color = models.CharField(max_length=20, default='blue')
    font_size = models.CharField(max_length=10, default='medium')
    compact_mode = models.BooleanField(default=False)
    animations_enabled = models.BooleanField(default=True)
    
    # Sprache & Region
    language = models.CharField(max_length=10, default='de')
    timezone = models.CharField(max_length=50, default='Europe/Berlin')
    date_format = models.CharField(max_length=10, default='DMY')
    
    # Datenschutz
    profile_public = models.BooleanField(default=True)
    show_email = models.BooleanField(default=False)
    show_activity = models.BooleanField(default=True)
    allow_analytics = models.BooleanField(default=True)
    allow_personalization = models.BooleanField(default=True)

    def __str__(self):
        return f"Profil von {self.user.username}"

    @property
    def avatar_url(self):
        if self.avatar:
            return self.avatar.url
        return settings.STATIC_URL + 'avatars/default_avatar.jpg'


class NotificationSettings(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='notification_settings')
    
    # Haupt-Toggles
    email_notifications = models.BooleanField(default=True)
    push_notifications = models.BooleanField(default=True)
    sms_notifications = models.BooleanField(default=False)
    
    # E-Mail Optionen
    email_updates = models.BooleanField(default=True)
    email_security = models.BooleanField(default=True)
    email_marketing = models.BooleanField(default=False)
    
    # Push Optionen
    push_downloads = models.BooleanField(default=True)
    push_updates = models.BooleanField(default=True)
    push_messages = models.BooleanField(default=True)
    
    # Ruhezeiten
    quiet_start = models.TimeField(default='22:00')
    quiet_end = models.TimeField(default='08:00')

    def __str__(self):
        return f"Benachrichtigungseinstellungen für {self.user.username}"


class UserSecurity(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='security')
    is_deactivated = models.BooleanField(default=False)
    two_factor_enabled = models.BooleanField(default=False)
    two_factor_secret = models.CharField(max_length=32, blank=True)
    
    def __str__(self):
        return f"Sicherheitseinstellungen für {self.user.username}"


class UserSession(models.Model):
    """Speichert aktive Benutzersessions für Geräteverwaltung"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sessions')
    session_key = models.CharField(max_length=40, unique=True)
    device_name = models.CharField(max_length=200, blank=True)
    device_type = models.CharField(max_length=50, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    location = models.CharField(max_length=200, blank=True)
    user_agent = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    last_activity = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.device_name or 'Unbekannt'} - {self.user.username}"

    def revoke(self):
        """Session beenden"""
        self.is_active = False
        self.save()


def _parse_device_info(user_agent: str):
    """Sehr einfache Geräteerkennung aus dem User-Agent (ohne externe Abhängigkeit)."""
    ua = (user_agent or "").lower()
    if "android" in ua:
        device_type = "Android"
    elif "iphone" in ua or "ipad" in ua:
        device_type = "iOS"
    elif "macintosh" in ua:
        device_type = "macOS"
    elif "windows" in ua:
        device_type = "Windows"
    elif "linux" in ua:
        device_type = "Linux"
    else:
        device_type = "Unbekannt"

    if "edg/" in ua:
        browser = "Edge"
    elif "chrome/" in ua:
        browser = "Chrome"
    elif "firefox/" in ua:
        browser = "Firefox"
    elif "safari/" in ua:
        browser = "Safari"
    else:
        browser = "Browser"

    return f"{browser} auf {device_type}", device_type


def record_login_session(user, request) -> bool:
    """
    Legt für die aktuelle Session einen UserSession-Eintrag an (für die
    Geräteverwaltung) und meldet zurück, ob dieses Gerät für den User neu ist
    (kein bisheriger aktiver Eintrag mit demselben device_name).
    """
    if not request.session.session_key:
        request.session.create()

    user_agent = request.META.get('HTTP_USER_AGENT', '')
    ip_address = request.META.get('REMOTE_ADDR')
    device_name, device_type = _parse_device_info(user_agent)

    is_new_device = not UserSession.objects.filter(
        user=user, device_name=device_name, is_active=True
    ).exists()

    UserSession.objects.update_or_create(
        session_key=request.session.session_key,
        defaults={
            'user': user,
            'device_name': device_name,
            'device_type': device_type,
            'ip_address': ip_address,
            'user_agent': user_agent,
            'expires_at': timezone.now() + timedelta(days=30),
            'is_active': True,
        },
    )
    return is_new_device


class APIToken(models.Model):
    """API-Schlüssel für externen Zugriff"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='api_tokens')
    name = models.CharField(max_length=100)
    key = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_used = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    permissions = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.user.username})"

    @staticmethod
    def generate_key():
        """Generiert einen sicheren API-Key"""
        return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(64))

    def revoke(self):
        """Token deaktivieren"""
        self.is_active = False
        self.save()


class DownloadHistory(models.Model):
    """Verlauf heruntergeladener Apps - VERKNÜPFT MIT STORE MODELS"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='download_history')
    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name='user_downloads')
    version = models.ForeignKey(Version, on_delete=models.CASCADE, related_name='user_downloads')
    downloaded_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    device_info = models.CharField(max_length=200, blank=True)
    successful = models.BooleanField(default=True)

    class Meta:
        ordering = ['-downloaded_at']
        verbose_name_plural = 'Download Histories'

    def __str__(self):
        return f"{self.user.username} - {self.app.name} v{self.version.version_number}"


class UserActivity(models.Model):
    """Detailliertes Aktivitäts-Logging für Benutzer"""
    ACTIVITY_TYPES = [
        ('login', 'Anmeldung'),
        ('logout', 'Abmeldung'),
        ('download', 'Download'),
        ('upload', 'Upload'),
        ('update_profile', 'Profil aktualisiert'),
        ('change_password', 'Passwort geändert'),
        ('settings_changed', 'Einstellungen geändert'),
        ('app_published', 'App veröffentlicht'),
        ('app_updated', 'App aktualisiert'),
        ('api_access', 'API Zugriff'),
        ('security_alert', 'Sicherheitswarnung'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='activities')
    activity_type = models.CharField(max_length=20, choices=ACTIVITY_TYPES)
    description = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    
    # Korrekte ForeignKeys zu Store-Models
    related_app = models.ForeignKey(App, on_delete=models.SET_NULL, null=True, blank=True, related_name='user_activities')
    related_version = models.ForeignKey(Version, on_delete=models.SET_NULL, null=True, blank=True, related_name='user_activities')
    related_object_id = models.PositiveIntegerField(null=True, blank=True)
    related_object_type = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.user.username} - {self.get_activity_type_display()} - {self.timestamp}"

    @classmethod
    def log(cls, user, activity_type, description='', **kwargs):
        """Hilfsmethode zum Erstellen eines Aktivitäts-Logs"""
        return cls.objects.create(
            user=user,
            activity_type=activity_type,
            description=description,
            ip_address=kwargs.get('ip_address'),
            user_agent=kwargs.get('user_agent'),
            metadata=kwargs.get('metadata', {}),
            related_app=kwargs.get('related_app'),
            related_version=kwargs.get('related_version'),
            related_object_id=kwargs.get('related_object_id'),
            related_object_type=kwargs.get('related_object_type')
        )


class PasswordResetToken(models.Model):
    """Sichere Passwort-Reset-Tokens"""
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    token = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)

    def __str__(self):
        return f"Reset-Token für {self.user.username}"

    @staticmethod
    def generate_token():
        return secrets.token_urlsafe(32)

    def is_valid(self):
        return not self.used and self.expires_at > timezone.now()