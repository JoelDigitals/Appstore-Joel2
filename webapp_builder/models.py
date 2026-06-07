from django.db import models
from django.contrib.auth.models import User


PLATFORM_CHOICES = [
    ('android', 'Android (APK)'),
    ('ios',     'iOS (IPA)'),
    ('both',    'Android + iOS'),
]

ORIENTATION_CHOICES = [
    ('portrait',  'Hochformat / Portrait'),
    ('landscape', 'Querformat / Landscape'),
    ('auto',      'Automatisch / Auto'),
]

THEME_CHOICES = [
    ('light', 'Hell / Light'),
    ('dark',  'Dunkel / Dark'),
    ('auto',  'Systemthema / System'),
]

STATUS_CHOICES = [
    ('pending',    'Ausstehend / Pending'),
    ('building',   'Wird erstellt / Building'),
    ('done',       'Fertig / Done'),
    ('failed',     'Fehlgeschlagen / Failed'),
]


class WebAppBuild(models.Model):
    user        = models.ForeignKey(User, on_delete=models.CASCADE, related_name='webapp_builds')
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    # ── Basis-Konfiguration ──────────────────────────────────────────────
    app_name        = models.CharField(max_length=100, help_text='App-Name / App name')
    app_id          = models.CharField(
        max_length=100, blank=True,
        help_text='Bundle-ID z. B. com.meinefirma.meinapp / Bundle ID e.g. com.mycompany.myapp'
    )
    website_url     = models.URLField(help_text='URL der Website / Website URL')
    platform        = models.CharField(max_length=10, choices=PLATFORM_CHOICES, default='android')
    version         = models.CharField(max_length=20, default='1.0.0')

    # ── Darstellung ──────────────────────────────────────────────────────
    orientation         = models.CharField(max_length=10, choices=ORIENTATION_CHOICES, default='portrait')
    theme_color         = models.CharField(max_length=10, default='system', choices=THEME_CHOICES)
    status_bar_color    = models.CharField(max_length=7, default='#6366f1', help_text='Hex-Farbe / Hex color')
    fullscreen          = models.BooleanField(default=False)
    allow_zoom          = models.BooleanField(default=True)

    # ── Berechtigungen ───────────────────────────────────────────────────
    perm_camera         = models.BooleanField(default=False)
    perm_location       = models.BooleanField(default=False)
    perm_microphone     = models.BooleanField(default=False)
    perm_notifications  = models.BooleanField(default=True)
    perm_storage        = models.BooleanField(default=False)

    # ── Erweiterte Optionen ──────────────────────────────────────────────
    enable_js           = models.BooleanField(default=True)
    enable_cookies      = models.BooleanField(default=True)
    enable_local_storage= models.BooleanField(default=True)
    custom_user_agent   = models.CharField(max_length=200, blank=True)
    offline_page        = models.BooleanField(default=False)
    pull_to_refresh     = models.BooleanField(default=True)
    loading_spinner     = models.BooleanField(default=True)
    nav_bar             = models.BooleanField(default=False, help_text='Zeige Vor/Zurück-Leiste / Show nav bar')

    # ── Splash / Icon ────────────────────────────────────────────────────
    icon_url        = models.URLField(blank=True, help_text='App-Icon URL (512×512 PNG) / App icon URL')
    splash_url      = models.URLField(blank=True, help_text='Splash-Screen URL / Splash screen URL')
    splash_bg_color = models.CharField(max_length=7, default='#0f172a')

    # ── Build-Ergebnis ───────────────────────────────────────────────────
    status          = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    build_log       = models.TextField(blank=True)
    apk_url         = models.URLField(blank=True, help_text='Download-URL der fertigen APK')
    ipa_url         = models.URLField(blank=True, help_text='Download-URL der fertigen IPA')
    build_started_at= models.DateTimeField(null=True, blank=True)
    build_finished_at= models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.app_name} ({self.get_platform_display()}) – {self.user.username}"

    class Meta:
        ordering = ['-created_at']
