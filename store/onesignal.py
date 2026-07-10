"""
store/onesignal.py
───────────────────
OneSignal REST API Helper für userbezogene Push-Benachrichtigungen.

Nutzer werden über ihre Django User-ID als OneSignal "external_id" angesprochen.
Auf Client-Seite wird dazu (in der Median-App) `median.onesignal.login(userId)`
aufgerufen (siehe base.html), wodurch die OneSignal-Subscription mit external_id
= Django User-ID verknüpft wird. Ein Speichern der internen OneSignal-Player-ID
ist für den Versand dadurch nicht nötig, wird aber zu Referenzzwecken im
UserProfile abgelegt (siehe settings.models.UserProfile.onesignal_player_id).
"""

import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

ONESIGNAL_API_URL = "https://onesignal.com/api/v1/notifications"


def _post(payload: dict) -> dict:
    if not settings.ONESIGNAL_ENABLED:
        return {"success": False, "error": "OneSignal ist nicht konfiguriert."}

    payload["app_id"] = settings.ONESIGNAL_APP_ID
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Basic {settings.ONESIGNAL_REST_API_KEY}",
    }
    try:
        resp = requests.post(ONESIGNAL_API_URL, json=payload, headers=headers, timeout=15)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 300:
            logger.warning("OneSignal Fehler (%s): %s", resp.status_code, data)
            return {"success": False, "error": data.get("errors", data), "status": resp.status_code}
        return {"success": True, "data": data}
    except requests.RequestException as e:
        logger.warning("OneSignal Request fehlgeschlagen: %s", e)
        return {"success": False, "error": str(e)}


def send_to_users(user_ids, title: str, message: str, url: str = None, data: dict = None,
                   image_url: str = None) -> dict:
    """
    Sendet eine Push-Benachrichtigung an eine Liste von Django User-IDs.
    Nutzt OneSignal 'include_aliases' mit external_id = str(user.id).

    image_url wird als Bild in der Push-Benachrichtigung angezeigt
    (Android Big Picture, iOS Attachment, Web/Chrome Image).
    """
    user_ids = [str(uid) for uid in user_ids]
    if not user_ids:
        return {"success": False, "error": "Keine Empfänger."}

    payload = {
        "include_aliases": {"external_id": user_ids},
        "target_channel": "push",
        "headings": {"de": title, "en": title},
        "contents": {"de": message, "en": message},
    }
    if url:
        payload["url"] = url
    if data:
        payload["data"] = data
    if image_url:
        payload["big_picture"] = image_url          # Android
        payload["ios_attachments"] = {"media": image_url}  # iOS
        payload["chrome_web_image"] = image_url      # Web Push

    return _post(payload)


def send_to_user(user, title: str, message: str, url: str = None, data: dict = None,
                  image_url: str = None) -> dict:
    """Sendet eine Push-Benachrichtigung an genau einen Django User."""
    return send_to_users([user.id], title, message, url=url, data=data, image_url=image_url)
