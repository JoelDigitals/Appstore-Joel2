"""
store/jds_cloud.py
──────────────────
Gemeinsames JDS-Cloud-Upload-Modul.
Wird von store/tasks.py UND webapp_builder/tasks.py verwendet.

API-Spec:
  POST https://ppkinjwiewiynqgofdcz.supabase.co/functions/v1/upload
  Header:  Authorization: Bearer <token>
  Body:    multipart/form-data  →  field name "file"

Erfolgreiche Antwort:
  {
    "success": true,
    "file": {
      "id": "uuid",
      "name": "datei.pdf",
      "size": 1234567,
      "content_type": "application/pdf",
      "view_url":     "https://.../functions/v1/file-access?token=...",
      "download_url": "https://.../functions/v1/file-access?token=...",
      "created_at":   "2026-04-10T..."
    }
  }
"""

import os
import re
import mimetypes
import logging
import requests

logger = logging.getLogger(__name__)

# ── Konstanten ────────────────────────────────────────────────────────────────
JDS_UPLOAD_URL = "https://ppkinjwiewiynqgofdcz.supabase.co/functions/v1/upload"
JDS_TOKEN      = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InBwa2luandpZXdpeW5xZ29mZGN6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzU4NDY2MTksImV4cCI6MjA5MTQyMjYxOX0"
    ".hSU2FfTONOrOZrbBOASoAXlx7W3ePg3tsHj-VaCg-Jo"
)

# Extra MIME-Typen die mimetypes.guess_type nicht kennt
_EXTRA_MIMES = {
    ".apk":      "application/vnd.android.package-archive",
    ".aab":      "application/x-authorware-bin",
    ".ipa":      "application/octet-stream",
    ".appimage": "application/octet-stream",
    ".deb":      "application/vnd.debian.binary-package",
    ".rpm":      "application/x-rpm",
    ".dmg":      "application/x-apple-diskimage",
    ".pkg":      "application/octet-stream",
}


def _safe_filename(name: str) -> str:
    """
    Bereinigt einen Dateinamen:
    - Leerzeichen → Unterstrich
    - Nur alphanumerisch + . _ - erlaubt
    - Kein führender Punkt
    """
    name = name.strip()
    name = re.sub(r"\s+", "_", name)
    # Erlaube: A-Z a-z 0-9  .  _  -
    name = re.sub(r"[^A-Za-z0-9._\-]", "_", name)
    # Führenden Punkt entfernen
    name = name.lstrip(".")
    return name or "upload"


def _get_mime(filename: str) -> str:
    """Gibt den MIME-Typ anhand der Dateiendung zurück."""
    ext = os.path.splitext(filename)[1].lower()
    if ext in _EXTRA_MIMES:
        return _EXTRA_MIMES[ext]
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"


def upload_file(file_path: str, filename: str = None, max_retries: int = 2) -> dict:
    """
    Lädt eine Datei zur JDS Cloud hoch.

    Args:
        file_path:   Absoluter Pfad zur Datei auf dem Server.
        filename:    Gewünschter Dateiname in der Cloud (optional, Standard: Basename).
        max_retries: Anzahl Wiederholungsversuche bei Netzwerkfehler (Standard: 2).

    Returns:
        {
            "success":      True,
            "file_id":      "uuid",
            "download_url": "https://...",
            "view_url":     "https://...",
            "name":         "datei.apk",
            "size":         1234567,
        }
        oder:
        {
            "success": False,
            "error":   "Fehlerbeschreibung",
            "status":  403,          # HTTP-Statuscode (falls vorhanden)
            "body":    "...",        # Rohantowrt für Debugging
        }
    """
    # ── Datei prüfen ─────────────────────────────────────────────────────────
    if not os.path.isfile(file_path):
        return {"success": False, "error": f"Datei nicht gefunden: {file_path}"}

    raw_name  = filename or os.path.basename(file_path)
    safe_name = _safe_filename(raw_name)
    mime_type = _get_mime(safe_name)

    logger.info("JDS Cloud Upload: %s → %s (%s)", file_path, safe_name, mime_type)

    # ── WICHTIG: Kein Content-Type im Header-Dict! ────────────────────────────
    # requests setzt Content-Type: multipart/form-data; boundary=... automatisch
    # wenn files= übergeben wird. Manuelles Setzen würde die boundary zerstören.
    headers = {
        "Authorization": f"Bearer {JDS_TOKEN}",
        # Content-Type: NICHT setzen – wird von requests automatisch gesetzt
    }

    last_error = None
    for attempt in range(1, max_retries + 2):  # max_retries+1 Versuche gesamt
        try:
            with open(file_path, "rb") as fh:
                # 4-Tupel: (filename, file-object, content-type, extra-headers)
                # Das stellt sicher, dass der korrekte MIME-Typ im Multipart-Part
                # übertragen wird – manche Server (Supabase Edge Functions) lesen ihn.
                resp = requests.post(
                    JDS_UPLOAD_URL,
                    headers=headers,
                    files={
                        "file": (safe_name, fh, mime_type, {}),
                    },
                    timeout=600,  # 10 Minuten für große Dateien
                )

            # ── Antwort parsen ────────────────────────────────────────────────
            raw_body = resp.text  # für Debugging immer speichern

            try:
                data = resp.json()
            except ValueError:
                # Kein JSON – wahrscheinlich HTML-Fehlerseite
                err = f"Keine JSON-Antwort (HTTP {resp.status_code}): {raw_body[:300]}"
                logger.error("JDS Cloud Upload Fehler: %s", err)
                if resp.status_code < 500 and attempt > 1:
                    # 4xx Fehler: kein Retry sinnvoll
                    return {"success": False, "error": err,
                            "status": resp.status_code, "body": raw_body[:1000]}
                last_error = err
                continue

            # ── Erfolg ───────────────────────────────────────────────────────
            if resp.status_code == 200 and data.get("success"):
                f = data["file"]
                result = {
                    "success":      True,
                    "file_id":      f.get("id", ""),
                    "download_url": f.get("download_url", ""),
                    "view_url":     f.get("view_url", ""),
                    "name":         f.get("name", safe_name),
                    "size":         f.get("size", 0),
                    "content_type": f.get("content_type", mime_type),
                    "created_at":   f.get("created_at", ""),
                }
                logger.info(
                    "JDS Cloud Upload erfolgreich: id=%s url=%s",
                    result["file_id"], result["download_url"]
                )
                return result

            # ── API-Fehler (z. B. 400, 401, 403, 500) ────────────────────────
            api_error = (
                data.get("error")
                or data.get("message")
                or f"HTTP {resp.status_code}"
            )
            logger.error(
                "JDS Cloud Upload API-Fehler (Versuch %d/%d): %s | Body: %s",
                attempt, max_retries + 1, api_error, raw_body[:500]
            )

            # Bei 4xx nicht wiederholen
            if 400 <= resp.status_code < 500:
                return {
                    "success": False,
                    "error":   api_error,
                    "status":  resp.status_code,
                    "body":    raw_body[:1000],
                }

            last_error = api_error

        except requests.exceptions.Timeout:
            last_error = f"Timeout nach 600s (Versuch {attempt})"
            logger.warning("JDS Cloud Upload Timeout (Versuch %d)", attempt)

        except requests.exceptions.ConnectionError as exc:
            last_error = f"Verbindungsfehler: {exc}"
            logger.warning("JDS Cloud Upload Verbindungsfehler (Versuch %d): %s", attempt, exc)

        except Exception as exc:
            last_error = f"Unerwarteter Fehler: {exc}"
            logger.exception("JDS Cloud Upload unerwarteter Fehler (Versuch %d)", attempt)
            # Sofort abbrechen bei unerwarteten Fehlern
            return {"success": False, "error": last_error}

    # Alle Versuche fehlgeschlagen
    return {
        "success": False,
        "error":   f"Upload nach {max_retries + 1} Versuchen fehlgeschlagen: {last_error}",
    }
