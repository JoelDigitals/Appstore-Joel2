from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.exceptions import ValidationError
import requests
from django.conf import settings
import base64
from io import BytesIO

class EmailVerificationCode(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)

AGE_RATINGS = [
    ('0', 'Keine Altersbeschränkung'),
    ('6', 'Ab 6 Jahren'),
    ('12', 'Ab 12 Jahren'),
    ('16', 'Ab 16 Jahren'),
    ('18', 'Ab 18 Jahren'),
]

PLATFORM_CHOICES = [
    ('android', 'Android'),
    ('ios', 'iOS'),
    ('windows', 'Windows'),
    ('macos', 'macOS'),
    ('linux', 'Linux'),
]

LANGUAGES = [
    ('de', 'Deutsch'),
    ('en', 'Englisch'),
    ('fr', 'Französisch'),
]

CHECKING_STATUS = [
    ('pending', 'Ausstehend'),
    ('running', 'In Bearbeitung'),
    ('passed', 'Bestanden'),
    ('failed', 'Fehlgeschlagen'),
]

CATEGORY_CHOICES = [
    ('games', 'Spiele'),
    ('productivity', 'Produktivität'),
    ('education', 'Bildung'),
    ('entertainment', 'Unterhaltung'),
    ('utilities', 'Dienstprogramme'),
    ('social', 'Soziale Netzwerke'),
    ('health', 'Gesundheit'),
    ('lifestyle', 'Lebensstil'),
    ('finance', 'Finanzen'),
    ('travel', 'Reisen'),
    ('news', 'Nachrichten'),
    ('music', 'Musik'),
    ('photo_video', 'Foto & Video'),
    ('books', 'Bücher'),
    ('shopping', 'Einkaufen'),
    ('food_drink', 'Essen & Trinken'),
    ('sports', 'Sport'),
    ('weather', 'Wetter'),
    ('navigation', 'Navigation'),
    ('communication', 'Kommunikation'),
    ('other', 'Andere'),
]

SUB_CATEGORY_CHOICES = [
    ('none', 'Keine Unterkategorie'),       
    ('action', 'Action'),
    ('adventure', 'Abenteuer'),
    ('puzzle', 'Puzzle'),
    ('strategy', 'Strategie'),
    ('simulation', 'Simulation'),
    ('arcade', 'Arcade'),
    ('racing', 'Rennspiele'),
    ('role_playing', 'Rollenspiele'),
    ('sports_games', 'Sportspiele'),
    ('card_games', 'Kartenspiele'),
    ('board_games', 'Brettspiele'),
    ('casual_games', 'Casual Spiele'),
]

class Developer(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    website = models.URLField(blank=True)
    email = models.EmailField(blank=True)
    # Lokale Datei (legacy oder wenn ImgBB deaktiviert)
    logo = models.ImageField(upload_to='developer_logos/', blank=True, null=True)
    # ImgBB/externe URL (priorisiert)
    logo_url = models.URLField(blank=True, help_text="ImgBB oder andere externe Bild-URL")
    youtube = models.URLField(blank=True)
    twitter = models.URLField(blank=True)
    github = models.URLField(blank=True)

    def __str__(self):
        return self.name

    def get_logo_url(self):
        """
        Gibt die Logo-URL zurück.
        Priorität: 1. logo_url (ImgBB/extern), 2. lokale Datei
        """
        if self.logo_url:
            return self.logo_url
        if self.logo:
            return self.logo.url
        return None


class App(models.Model):
    developer = models.ForeignKey(Developer, on_delete=models.CASCADE, related_name='apps')
    name = models.CharField(max_length=255)
    description = models.TextField()
    language = models.CharField(max_length=5, choices=LANGUAGES, default='de')
    platform = models.CharField(max_length=10, choices=PLATFORM_CHOICES)
    age_rating = models.CharField(max_length=2, choices=AGE_RATINGS, default='0')
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='other')
    subcategory = models.CharField(max_length=50, choices=SUB_CATEGORY_CHOICES, blank=True)
    # Lokale Datei (legacy oder Fallback)
    icon = models.ImageField(upload_to='app_icons/', blank=True, null=True)
    # ImgBB/externe URL (priorisiert)
    icon_url = models.URLField(blank=True, help_text="ImgBB oder andere externe Bild-URL")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    download_count = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.name} ({self.developer.name}) - {self.platform}"

    def get_icon_url(self):
        """
        Gibt die Icon-URL zurück.
        Priorität: 1. icon_url (ImgBB/extern), 2. lokale Datei
        """
        if self.icon_url:
            return self.icon_url
        if self.icon:
            return self.icon.url
        return '/static/images/default_app_icon.png'


class VersionDownload(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    version = models.ForeignKey('Version', on_delete=models.CASCADE)
    downloaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'version')

    def __str__(self):
        return f"{self.user.username} downloaded {self.version.app.name} v{self.version.version_number}"


class AppScreenshot(models.Model):
    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name='screenshots')
    # Lokale Datei (legacy oder Fallback)
    image = models.ImageField(upload_to='app_screenshots/', blank=True, null=True)
    # ImgBB/externe URL (priorisiert)
    image_url = models.URLField(blank=True, help_text="ImgBB oder andere externe Bild-URL")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Screenshot für {self.app.name}"

    def get_image_url(self):
        """
        Gibt die Bild-URL zurück.
        Priorität: 1. image_url (ImgBB/extern), 2. lokale Datei
        """
        if self.image_url:
            return self.image_url
        if self.image:
            return self.image.url
        return '/static/images/default_screenshot.png'

    class Meta:
        ordering = ['-uploaded_at']


class Version(models.Model):
    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name='versions')
    version_number = models.CharField(max_length=50)
    file = models.FileField(upload_to='app_files/')
    release_notes = models.TextField(blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    checking_status = models.CharField(max_length=10, choices=CHECKING_STATUS, default='pending')
    checking_progress = models.PositiveSmallIntegerField(default=0)
    checking_log = models.TextField(blank=True)
    approved = models.BooleanField(default=False)
    new_version = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.app.name} v{self.version.version_number}"


WARNING_TYPES = [
    ('violence', 'Gewalt'),
    ('sex', 'Sexuelle Inhalte'),
    ('ads', 'Werbung'),
    ('drugs', 'Drogen'),
    ('none', 'Keine Warnung'),
]

class AppWarning(models.Model):
    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name='warnings')
    warning_type = models.CharField(max_length=20, choices=WARNING_TYPES)
    description = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.get_warning_type_display()} bei {self.app.name}"


class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    title = models.CharField(max_length=200)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    read = models.BooleanField(default=False)
    push_sent = models.BooleanField(default=False)
    app = models.ForeignKey('App', on_delete=models.CASCADE, null=True, blank=True)
    version = models.ForeignKey('Version', on_delete=models.CASCADE, null=True, blank=True)
    level = models.CharField(max_length=20, default='info')
    timestamp = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f'{self.title} -> {"Alle" if not self.user else self.user.username}'


class PushSubscription(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    endpoint = models.TextField(unique=True)
    data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.endpoint[:40]


class AppInfo(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField()

    def __str__(self):
        return f"{self.name}"


class AppUpdate(models.Model):
    UPDATE_TYPES = [
        ("info", "Information"),
        ("warnung", "Warnung"),
        ("kritisch", "Kritisch"),
    ]

    title = models.CharField(max_length=200)
    message = models.TextField()
    update_type = models.CharField(max_length=10, choices=UPDATE_TYPES)
    date = models.DateField(auto_now_add=True)
    link = models.URLField(blank=True)

    def __str__(self):
        return f"[{self.get_update_type_display()}] {self.title}"


class RoadmapItem(models.Model):
    STATUS_CHOICES = [
        ("geplant", "Geplant"),
        ("in_arbeit", "In Arbeit"),
        ("abgeschlossen", "Abgeschlossen"),
    ]

    title = models.CharField(max_length=200)
    description = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    date = models.DateField()

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"


# ImgBB Upload Helper
class ImgBBUploader:
    """Hilfsklasse für ImgBB Uploads"""
    
    @staticmethod
    def upload_image(image_file, api_key=None):
        """
        Lädt ein Bild zu ImgBB hoch
        
        Args:
            image_file: Django UploadedFile oder Dateipfad
            api_key: ImgBB API Key (optional, nutzt settings.IMGBB_API_KEY)
        
        Returns:
            dict: {'success': bool, 'url': str, 'delete_url': str, 'error': str}
        """
        api_key = api_key or getattr(settings, 'IMGBB_API_KEY', None)
        if not api_key:
            return {'success': False, 'error': 'Kein API Key konfiguriert', 'url': None}
        
        try:
            if hasattr(image_file, 'read'):
                # Django UploadedFile
                image_data = base64.b64encode(image_file.read()).decode()
                image_file.seek(0)  # Reset für mögliches späteres Speichern
            else:
                # Dateipfad
                with open(image_file, 'rb') as f:
                    image_data = base64.b64encode(f.read()).decode()
            
            response = requests.post(
                "https://api.imgbb.com/1/upload",
                data={
                    "key": api_key,
                    "image": image_data,
                },
                timeout=30
            )
            data = response.json()
            
            if data.get('success'):
                return {
                    'success': True,
                    'url': data['data']['url'],
                    'delete_url': data['data']['delete_url'],
                    'thumb_url': data['data'].get('thumb', {}).get('url'),
                    'medium_url': data['data'].get('medium', {}).get('url'),
                }
            else:
                return {
                    'success': False,
                    'error': data.get('error', {}).get('message', 'Unbekannter Fehler'),
                    'url': None
                }
                
        except Exception as e:
            return {'success': False, 'error': str(e), 'url': None}
    
    @staticmethod
    def upload_from_url(image_url, api_key=None):
        """Lädt ein Bild von einer URL zu ImgBB hoch"""
        api_key = api_key or getattr(settings, 'IMGBB_API_KEY', None)
        if not api_key:
            return {'success': False, 'error': 'Kein API Key konfiguriert', 'url': None}
        
        try:
            response = requests.post(
                "https://api.imgbb.com/1/upload",
                data={
                    "key": api_key,
                    "image": image_url,
                },
                timeout=30
            )
            data = response.json()
            
            if data.get('success'):
                return {
                    'success': True,
                    'url': data['data']['url'],
                    'delete_url': data['data']['delete_url'],
                }
            else:
                return {
                    'success': False,
                    'error': data.get('error', {}).get('message', 'Unbekannter Fehler'),
                    'url': None
                }
                
        except Exception as e:
            return {'success': False, 'error': str(e), 'url': None}