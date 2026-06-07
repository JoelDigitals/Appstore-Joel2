from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0016_alter_appscreenshot_options_app_icon_url_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='version',
            name='release_tag',
            field=models.CharField(
                blank=True,
                choices=[
                    ('stable',  'Stable'),
                    ('beta',    'Beta'),
                    ('alpha',   'Alpha'),
                    ('rc',      'Release Candidate'),
                    ('hotfix',  'Hotfix'),
                    ('nightly', 'Nightly'),
                    ('custom',  'Benutzerdefiniert'),
                ],
                default='stable',
                help_text='Kanal-Label für diese Version (z. B. stable, beta, alpha)',
                max_length=50,
            ),
        ),
        migrations.AddField(
            model_name='version',
            name='release_tag_custom',
            field=models.CharField(
                blank=True,
                default='',
                help_text="Wird genutzt wenn release_tag='custom'",
                max_length=50,
            ),
        ),
        migrations.AddField(
            model_name='version',
            name='jds_cloud_file_id',
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name='version',
            name='jds_cloud_url',
            field=models.URLField(blank=True, help_text='Download-URL in der JDS Cloud'),
        ),
        migrations.AddField(
            model_name='version',
            name='jds_cloud_view_url',
            field=models.URLField(blank=True, help_text='View-URL in der JDS Cloud'),
        ),
    ]
