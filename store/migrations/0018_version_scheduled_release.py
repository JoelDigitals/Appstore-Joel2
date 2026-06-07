from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0017_version_release_tag_jds_cloud'),
    ]

    operations = [
        migrations.AddField(
            model_name='version',
            name='scheduled_release_at',
            field=models.DateTimeField(
                blank=True, null=True,
                help_text='Veröffentlichung zu diesem Zeitpunkt nach bestandener Prüfung. Leer = sofort.'
            ),
        ),
        migrations.AddField(
            model_name='version',
            name='release_held',
            field=models.BooleanField(
                default=False,
                help_text='True wenn Prüfung bestanden aber auf Datum gewartet wird.'
            ),
        ),
    ]
