# Patch: add release_tag + jds_cloud_url to Version model
# Add after the 'new_version' field:
#
#     release_tag = models.CharField(max_length=50, blank=True, default='',
#         help_text="z.B. stable, beta, alpha, v1.0.0-rc1")
#     jds_cloud_url = models.URLField(blank=True, help_text="URL der Datei in der JDS Cloud")
#     jds_cloud_view_url = models.URLField(blank=True)
#     jds_cloud_file_id = models.CharField(max_length=100, blank=True)
