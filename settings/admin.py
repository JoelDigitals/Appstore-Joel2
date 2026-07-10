from django.contrib import admin
from .models import UserProfile, NotificationSettings

admin.site.register(NotificationSettings)

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'birth_date', 'onesignal_subscribed', 'onesignal_player_id')
    list_filter = ('onesignal_subscribed',)
    search_fields = ('user__username', 'user__email', 'onesignal_player_id')
