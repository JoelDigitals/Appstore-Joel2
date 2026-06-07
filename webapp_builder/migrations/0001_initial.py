from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='WebAppBuild',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('app_name', models.CharField(max_length=100)),
                ('app_id', models.CharField(blank=True, max_length=100)),
                ('website_url', models.URLField()),
                ('platform', models.CharField(choices=[('android','Android (APK)'),('ios','iOS (IPA)'),('both','Android + iOS')], default='android', max_length=10)),
                ('version', models.CharField(default='1.0.0', max_length=20)),
                ('orientation', models.CharField(choices=[('portrait','Hochformat / Portrait'),('landscape','Querformat / Landscape'),('auto','Automatisch / Auto')], default='portrait', max_length=10)),
                ('theme_color', models.CharField(choices=[('light','Hell / Light'),('dark','Dunkel / Dark'),('auto','Systemthema / System')], default='system', max_length=10)),
                ('status_bar_color', models.CharField(default='#6366f1', max_length=7)),
                ('fullscreen', models.BooleanField(default=False)),
                ('allow_zoom', models.BooleanField(default=True)),
                ('perm_camera', models.BooleanField(default=False)),
                ('perm_location', models.BooleanField(default=False)),
                ('perm_microphone', models.BooleanField(default=False)),
                ('perm_notifications', models.BooleanField(default=True)),
                ('perm_storage', models.BooleanField(default=False)),
                ('enable_js', models.BooleanField(default=True)),
                ('enable_cookies', models.BooleanField(default=True)),
                ('enable_local_storage', models.BooleanField(default=True)),
                ('custom_user_agent', models.CharField(blank=True, max_length=200)),
                ('offline_page', models.BooleanField(default=False)),
                ('pull_to_refresh', models.BooleanField(default=True)),
                ('loading_spinner', models.BooleanField(default=True)),
                ('nav_bar', models.BooleanField(default=False)),
                ('icon_url', models.URLField(blank=True)),
                ('splash_url', models.URLField(blank=True)),
                ('splash_bg_color', models.CharField(default='#0f172a', max_length=7)),
                ('status', models.CharField(choices=[('pending','Ausstehend / Pending'),('building','Wird erstellt / Building'),('done','Fertig / Done'),('failed','Fehlgeschlagen / Failed')], default='pending', max_length=10)),
                ('build_log', models.TextField(blank=True)),
                ('apk_url', models.URLField(blank=True)),
                ('ipa_url', models.URLField(blank=True)),
                ('build_started_at', models.DateTimeField(blank=True, null=True)),
                ('build_finished_at', models.DateTimeField(blank=True, null=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='webapp_builds', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
