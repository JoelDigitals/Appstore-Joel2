from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from .models import UserProfile, NotificationSettings, UserSecurity


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """Erstellt UserProfile bei neuer Registrierung"""
    if created:
        UserProfile.objects.create(user=instance)


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    """Speichert UserProfile bei Änderungen"""
    # Nur speichern wenn Profil existiert (verhindert Fehler bei alten Usern)
    if hasattr(instance, 'profile'):
        instance.profile.save()


@receiver(post_save, sender=User)
def create_notification_settings(sender, instance, created, **kwargs):
    """Erstellt NotificationSettings bei neuer Registrierung"""
    if created:
        NotificationSettings.objects.create(user=instance)


@receiver(post_save, sender=User)
def save_notification_settings(sender, instance, **kwargs):
    """Speichert NotificationSettings bei Änderungen"""
    if hasattr(instance, 'notification_settings'):
        instance.notification_settings.save()


@receiver(post_save, sender=User)
def create_user_security(sender, instance, created, **kwargs):
    """Erstellt UserSecurity bei neuer Registrierung"""
    if created:
        UserSecurity.objects.create(user=instance)


@receiver(post_save, sender=User)
def save_user_security(sender, instance, **kwargs):
    """Speichert UserSecurity bei Änderungen"""
    if hasattr(instance, 'security'):
        instance.security.save()


# Optional: Ein kombinierter Signal-Handler für bessere Performance
@receiver(post_save, sender=User)
def create_all_user_data(sender, instance, created, **kwargs):
    """Erstellt alle benötigten User-Daten auf einmal"""
    if created:
        UserProfile.objects.get_or_create(user=instance)
        NotificationSettings.objects.get_or_create(user=instance)
        UserSecurity.objects.get_or_create(user=instance)