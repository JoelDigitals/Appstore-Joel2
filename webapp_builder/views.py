import json
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from .models import WebAppBuild


@login_required
def builder_home(request):
    """List all builds for the current user."""
    builds = WebAppBuild.objects.filter(user=request.user)
    return render(request, 'webapp_builder/home.html', {'builds': builds})


@login_required
def builder_new(request):
    """New build wizard (rendered as React SPA)."""
    from .context import PLATFORM_CHOICES, DISPLAY_TOGGLES, ADV_TOGGLES, PERMISSIONS
    return render(request, 'webapp_builder/builder.html', {
        'platforms':      PLATFORM_CHOICES,
        'display_toggles':DISPLAY_TOGGLES,
        'adv_toggles':    ADV_TOGGLES,
        'permissions':    PERMISSIONS,
    })


@login_required
def builder_detail(request, build_id):
    build = get_object_or_404(WebAppBuild, id=build_id, user=request.user)
    return render(request, 'webapp_builder/detail.html', {'build': build})


@login_required
@csrf_exempt
def builder_create_api(request):
    """
    POST – create a new WebAppBuild from JSON body.
    Returns {id, status}.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    required = ('app_name', 'website_url', 'platform')
    for f in required:
        if not data.get(f):
            return JsonResponse({'error': f'Missing field: {f}'}, status=400)

    build = WebAppBuild.objects.create(
        user                = request.user,
        app_name            = data['app_name'],
        app_id              = data.get('app_id', ''),
        website_url         = data['website_url'],
        platform            = data['platform'],
        version             = data.get('version', '1.0.0'),
        orientation         = data.get('orientation', 'portrait'),
        theme_color         = data.get('theme_color', 'system'),
        status_bar_color    = data.get('status_bar_color', '#6366f1'),
        fullscreen          = bool(data.get('fullscreen', False)),
        allow_zoom          = bool(data.get('allow_zoom', True)),
        perm_camera         = bool(data.get('perm_camera', False)),
        perm_location       = bool(data.get('perm_location', False)),
        perm_microphone     = bool(data.get('perm_microphone', False)),
        perm_notifications  = bool(data.get('perm_notifications', True)),
        perm_storage        = bool(data.get('perm_storage', False)),
        enable_js           = bool(data.get('enable_js', True)),
        enable_cookies      = bool(data.get('enable_cookies', True)),
        enable_local_storage= bool(data.get('enable_local_storage', True)),
        custom_user_agent   = data.get('custom_user_agent', ''),
        offline_page        = bool(data.get('offline_page', False)),
        pull_to_refresh     = bool(data.get('pull_to_refresh', True)),
        loading_spinner     = bool(data.get('loading_spinner', True)),
        nav_bar             = bool(data.get('nav_bar', False)),
        icon_url            = data.get('icon_url', ''),
        splash_url          = data.get('splash_url', ''),
        splash_bg_color     = data.get('splash_bg_color', '#0f172a'),
    )

    # Trigger build task asynchronously
    from .tasks import run_webapp_build
    run_webapp_build.delay(build.id)

    return JsonResponse({'id': build.id, 'status': build.status})


@login_required
def builder_status_api(request, build_id):
    """GET – return live build status."""
    build = get_object_or_404(WebAppBuild, id=build_id, user=request.user)
    return JsonResponse({
        'id':       build.id,
        'status':   build.status,
        'log':      build.build_log,
        'apk_url':  build.apk_url,
        'ipa_url':  build.ipa_url,
        'finished': build.build_finished_at.isoformat() if build.build_finished_at else None,
    })
