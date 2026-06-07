from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('store', '0018_version_scheduled_release'),
    ]
    operations = [
        migrations.AddField(
            model_name='version',
            name='original_filename',
            field=models.CharField(
                blank=True, default='',
                help_text='Originaler Dateiname beim Upload (z.B. MyApp-v2.0.apk)',
                max_length=255,
            ),
        ),
    ]
