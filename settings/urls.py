from django.urls import path
from . import views

urlpatterns = [
    # Profil
    path('', views.user_profile_view, name='user_profile'),
    path('edit/', views.edit_user_profile_view, name='edit_user_profile'),
    path('delete/', views.delete_user_profile_view, name='delete_user_profile'),
    
    # Einstellungen Übersicht
    path('settings/', views.user_settings_view, name='user_settings'),
    
    # Benachrichtigungen
    path('settings/notifications/', views.notification_settings_view, name='notification_settings'),
    
    # Sicherheit
    path('settings/security/', views.security_settings, name='security_settings'),
    path('settings/security/2fa/link/create/', views.tfa_link_create, name='tfa_link_create'),
    path('settings/security/2fa/link/status/', views.tfa_link_status, name='tfa_link_status'),
    path('settings/security/2fa/check-link/', views.tfa_check_link, name='tfa_check_link'),
    path('settings/security/2fa/pairing-test/', views.tfa_pairing_test, name='tfa_pairing_test'),
    path('settings/security/2fa/pairing-status/', views.tfa_pairing_status, name='tfa_pairing_status'),
    path('settings/security/2fa/enable/', views.tfa_enable, name='tfa_enable'),
    path('settings/security/2fa/disable-status/', views.tfa_disable_status, name='tfa_disable_status'),
    path('settings/security/2fa/disable-confirm/', views.tfa_disable_confirm, name='tfa_disable_confirm'),

    # Passwort ändern
    path('settings/password/', views.change_password_view, name='change_password'),
    
    # Erscheinungsbild
    path('settings/appearance/', views.appearance_settings_view, name='appearance_settings'),
    
    # Sprache
    path('settings/language/', views.language_settings_view, name='language_settings'),
    
    # Datenschutz
    path('settings/privacy/', views.privacy_settings_view, name='privacy_settings'),
    
    # Verlauf & Geräte
    path('downloads/history/', views.download_history_view, name='download_history'),
    path('devices/', views.connected_devices_view, name='connected_devices'),
    path('devices/revoke/<int:session_id>/', views.revoke_device, name='revoke_device'),
    
    # API
    path('api/keys/', views.api_keys_view, name='api_keys'),
    path('api/keys/generate/', views.generate_api_key, name='generate_api_key'),
    path('api/keys/revoke/<int:key_id>/', views.revoke_api_key, name='revoke_api_key'),
    
    # Abrechnung
    path('billing/', views.billing_view, name='billing'),
]