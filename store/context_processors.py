from .models import Notification


def notifications(request):
    """
    Stellt in ALLEN Templates bereit:
      - notifications        – die 10 neuesten ungelesenen Benachrichtigungen
      - notifications_count  – Anzahl ungelesener Benachrichtigungen (für Badge)
    """
    if request.user.is_authenticated:
        qs = Notification.objects.filter(
            user=request.user, read=False
        ).order_by('-created_at')
        count = qs.count()
        notifs = qs[:10]
    else:
        notifs = []
        count = 0

    return {
        'user_notifications':  notifs,
        'notifications':       notifs,
        'notifications_count': count,
    }
