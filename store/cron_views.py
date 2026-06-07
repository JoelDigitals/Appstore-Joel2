"""
store/cron_views.py
────────────────────
HTTP-Endpoints die von einem externen Cron-Dienst aufgerufen werden.

Empfohlene Dienste (kostenlos):
  • cron-job.org  – https://cron-job.org
  • EasyCron       – https://www.easycron.com
  • Railway Cron   – in railway.app direkt konfigurierbar
  • render.com     – Cron Jobs in der Dashboard-Oberfläche

Endpoint:
  POST  /api/cron/scheduled-releases/
  Header:  X-Cron-Secret: <CRON_SECRET aus settings>

Alle 5 Minuten aufrufen. Der Endpoint veröffentlicht automatisch alle
Versionen, deren scheduled_release_at jetzt ≤ now() ist.
"""

import json
import logging
from django.http  import JsonResponse
from django.views.decorators.csrf  import csrf_exempt
from django.views.decorators.http  import require_POST
from django.conf  import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def cron_scheduled_releases(request):
    """
    Called every 5 minutes by an external cron service.

    Security: checks the X-Cron-Secret header against
    settings.CRON_SECRET  (add to your .env / environment variables).

    Returns JSON with a summary of what was published.
    """
    # ── Auth ──────────────────────────────────────────────────────────────
    expected_secret = getattr(settings, "CRON_SECRET", None)
    if expected_secret:
        provided = request.headers.get("X-Cron-Secret", "").strip()
        if provided != expected_secret:
            logger.warning(
                "cron_scheduled_releases: invalid secret from %s",
                request.META.get("REMOTE_ADDR"),
            )
            return JsonResponse({"error": "Unauthorized"}, status=401)

    # ── Run ───────────────────────────────────────────────────────────────
    try:
        from .tasks import publish_due_scheduled_releases
        result = publish_due_scheduled_releases()
        logger.info(
            "cron_scheduled_releases: published=%d errors=%d",
            result["published_count"],
            result["error_count"],
        )
        return JsonResponse(result)
    except Exception as exc:
        logger.exception("cron_scheduled_releases: unexpected error")
        return JsonResponse({"error": str(exc)}, status=500)


@csrf_exempt
def cron_health(request):
    """
    Health-check endpoint – GET oder POST erlaubt.
    GET /api/cron/health/  → {"status": "ok", "time": "..."}
    """
    return JsonResponse({
        "status": "ok",
        "time":   timezone.now().isoformat(),
    })
