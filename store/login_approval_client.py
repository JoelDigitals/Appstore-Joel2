"""
store/login_approval_client.py
───────────────────────────────
Client fuer die Push-basierte Zweitfaktor-Bestaetigung ueber die Joel
Digitals App (main.login_approval_views im joel_digitals-Projekt, erreichbar
unter settings.SSO_PROVIDER_URL). Nutzt dieselben client_id/client_secret-
Credentials, mit denen dieses Projekt bereits gegen /api/sso/validate/
spricht (siehe sso_callback in store/views.py) - kein neuer Auth-Mechanismus.

WICHTIG: Dies ist NICHT das AppStore-eigene OneSignal (store/onesignal.py) -
der Push geht an ein komplett anderes Geraet/App (die Joel Digitals App des
Nutzers), verwaltet vom joel_digitals-Projekt.
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def _post(path, **data):
    payload = {
        'client_id': settings.SSO_CLIENT_ID,
        'client_secret': settings.SSO_CLIENT_SECRET,
        **data,
    }
    try:
        resp = requests.post(f"{settings.SSO_PROVIDER_URL}{path}", data=payload, timeout=10)
        try:
            body = resp.json()
        except ValueError:
            body = {'error': 'invalid_response'}
        return resp.status_code, body
    except requests.RequestException as e:
        logger.warning("Login-Approval-Request fehlgeschlagen (%s): %s", path, e)
        return 599, {'error': 'connection_failed'}


def check_account_link_status(email):
    """Gibt (status_code, {'linked': bool, 'reachable': bool}) zurueck."""
    return _post('/api/login-approval/check-link/', email=email)


def create_login_approval_request(email, purpose='login', ip='', context=''):
    """Legt eine Bestaetigungsanfrage an und loest den Push aus. Gibt
    (status_code, body) zurueck - body enthaelt bei Erfolg 'token'/
    'expires_at'/'reused', sonst ein 'error'-Feld (siehe
    main.login_approval_views.create_login_approval fuer moegliche Werte:
    no_joel_digitals_account, device_not_linked, rate_limited, ...)."""
    return _post('/api/login-approval/create/', email=email, purpose=purpose, ip=ip, context=context)


def check_login_approval_status(token):
    """Gibt (status_code, {'status': 'pending'|'approved'|'denied'|'expired'}) zurueck."""
    return _post('/api/login-approval/status/', token=token)


def create_app_link_code():
    """Kontoverknuepfung per Pairing-Code/QR statt SSO-Redirect: legt einen
    kurzen Code an, den der User in seiner bereits eingeloggten Joel
    Digitals App eintippt/scannt (siehe main.models.AppLinkCode). Gibt
    (status_code, {'code': ..., 'expires_at': ...}) zurueck."""
    return _post('/api/app-link/create/')


def check_app_link_status(code):
    """Gibt (status_code, {'status': ..., 'email': ...}) zurueck - 'email'
    ist nur gesetzt, sobald status == 'confirmed'."""
    return _post('/api/app-link/status/', code=code)
