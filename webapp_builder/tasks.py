"""
WebApp Builder – Celery Build Task

This task wraps the popular open-source tool Capacitor (via npx) to
package a website URL into a native Android APK and/or iOS IPA.

Requirements (on the server):
  - Node.js >= 18
  - npx / npm
  - Android SDK (for APK builds)
  - Xcode / xcrun (for IPA builds, macOS only)
  - The JDS Cloud upload API (same token as store)

The build steps:
  1. Create a temp Capacitor project
  2. Set capacitor.config.json from the build object
  3. npx cap add android / ios
  4. Patch AndroidManifest / Info.plist for permissions, colors, orientation
  5. gradle assembleRelease  /  xcodebuild archive + export
  6. Upload resulting APK / IPA to JDS Cloud
  7. Update WebAppBuild with download URLs
"""

import os
import json
import shutil
import subprocess
import tempfile
import logging
from celery import shared_task
from django.utils import timezone
from store.jds_cloud import upload_file as _jds_upload

logger = logging.getLogger(__name__)


def _upload(file_path: str, filename: str) -> dict:
    """Wrapper around the shared JDS Cloud upload module."""
    result = _jds_upload(file_path, filename)
    if result["success"]:
        return {"success": True, "download_url": result["download_url"]}
    return {"success": False, "error": result["error"]}


def _run(cmd, cwd=None, env=None):
    result = subprocess.run(
        cmd, cwd=cwd, env=env,
        capture_output=True, text=True, timeout=600
    )
    return result.returncode, result.stdout + result.stderr


@shared_task(bind=True, max_retries=0)
def run_webapp_build(self, build_id: int):
    from .models import WebAppBuild
    try:
        build = WebAppBuild.objects.get(id=build_id)
    except WebAppBuild.DoesNotExist:
        return

    log: list[str] = []
    build.status           = 'building'
    build.build_started_at = timezone.now()
    build.build_log        = ''
    build.save()

    def step(msg: str):
        log.append(msg)
        build.build_log = "\n".join(log)
        build.save(update_fields=["build_log"])

    def fail(msg: str):
        log.append(f"✖ {msg}")
        build.status            = 'failed'
        build.build_log         = "\n".join(log)
        build.build_finished_at = timezone.now()
        build.save()

    workdir = tempfile.mkdtemp(prefix="jds_build_")
    try:
        app_id = build.app_id or f"com.jds.{build.app_name.lower().replace(' ','')}"

        # ── Step 1: Init npm / Capacitor project ────────────────────────
        step("▶ 1/6 – Initialisiere Capacitor-Projekt / Initialising Capacitor project")
        rc, out = _run(["npm", "init", "-y"], cwd=workdir)
        log.append(out[:500])
        if rc != 0:
            return fail(f"npm init fehlgeschlagen / failed: {out[:300]}")

        rc, out = _run(["npm", "install", "@capacitor/core", "@capacitor/cli",
                        "@capacitor/android", "@capacitor/ios"], cwd=workdir)
        log.append(out[-500:])
        if rc != 0:
            return fail(f"npm install fehlgeschlagen / failed")

        # ── Step 2: capacitor.config.json ───────────────────────────────
        step("▶ 2/6 – Schreibe Konfiguration / Writing configuration")
        cap_cfg = {
            "appId":    app_id,
            "appName":  build.app_name,
            "webDir":   "www",
            "server": {"url": build.website_url, "cleartext": True},
            "android":  {
                "allowMixedContent": True,
                "backgroundColor":   build.status_bar_color,
            },
            "ios": {
                "backgroundColor": build.status_bar_color,
            },
            "plugins": {
                "SplashScreen": {
                    "launchAutoHide": True,
                    "backgroundColor": build.splash_bg_color,
                }
            }
        }
        os.makedirs(os.path.join(workdir, "www"), exist_ok=True)
        with open(os.path.join(workdir, "www", "index.html"), "w") as f:
            f.write(f'<!DOCTYPE html><html><head><meta http-equiv="refresh" content="0;url={build.website_url}"></head></html>')
        with open(os.path.join(workdir, "capacitor.config.json"), "w") as f:
            json.dump(cap_cfg, f, indent=2)

        # ── Step 3: Add platforms ────────────────────────────────────────
        step("▶ 3/6 – Füge Plattformen hinzu / Adding platforms")
        platforms = []
        if build.platform in ("android", "both"):
            platforms.append("android")
        if build.platform in ("ios", "both"):
            platforms.append("ios")

        for plat in platforms:
            rc, out = _run(["npx", "cap", "add", plat], cwd=workdir)
            log.append(out[-300:])
            if rc != 0:
                return fail(f"cap add {plat} fehlgeschlagen / failed")
            step(f"  ✓ {plat} hinzugefügt / added")

        # ── Step 4: Patch manifests ──────────────────────────────────────
        step("▶ 4/6 – Passe Manifest/Berechtigungen an / Patching manifests")

        if "android" in platforms:
            manifest_path = os.path.join(workdir, "android", "app", "src", "main", "AndroidManifest.xml")
            if os.path.exists(manifest_path):
                perms = []
                if build.perm_camera:        perms.append('android.permission.CAMERA')
                if build.perm_location:      perms.append('android.permission.ACCESS_FINE_LOCATION')
                if build.perm_microphone:    perms.append('android.permission.RECORD_AUDIO')
                if build.perm_notifications: perms.append('android.permission.POST_NOTIFICATIONS')
                if build.perm_storage:
                    perms.append('android.permission.READ_EXTERNAL_STORAGE')
                    perms.append('android.permission.WRITE_EXTERNAL_STORAGE')

                perm_xml = "\n".join(
                    f'    <uses-permission android:name="{p}"/>' for p in perms
                )
                with open(manifest_path, "r") as f:
                    mf = f.read()
                mf = mf.replace("</manifest>", f"{perm_xml}\n</manifest>")
                with open(manifest_path, "w") as f:
                    f.write(mf)
                step("  ✓ AndroidManifest.xml gepatcht / patched")

        # ── Step 5: Build ────────────────────────────────────────────────
        step("▶ 5/6 – Erstelle App-Pakete / Building app packages")
        built_files = {}

        if "android" in platforms:
            android_dir = os.path.join(workdir, "android")
            rc, out = _run(["./gradlew", "assembleRelease"], cwd=android_dir)
            log.append(out[-800:])
            apk_search = os.path.join(android_dir, "app", "build", "outputs",
                                       "apk", "release", "app-release-unsigned.apk")
            if os.path.exists(apk_search):
                built_files["apk"] = apk_search
                step("  ✓ APK erstellt / APK built")
            else:
                return fail("APK-Ausgabedatei nicht gefunden / APK output not found")

        if "ios" in platforms:
            ios_dir = os.path.join(workdir, "ios", "App")
            rc, out = _run([
                "xcodebuild", "-workspace", "App.xcworkspace",
                "-scheme", "App", "-configuration", "Release",
                "-archivePath", os.path.join(workdir, "App.xcarchive"),
                "archive"
            ], cwd=ios_dir)
            log.append(out[-500:])
            ipa_path = os.path.join(workdir, f"{build.app_name}.ipa")
            rc2, out2 = _run([
                "xcodebuild", "-exportArchive",
                "-archivePath", os.path.join(workdir, "App.xcarchive"),
                "-exportPath", os.path.join(workdir, "ipa_export"),
                "-exportOptionsPlist", os.path.join(workdir, "ExportOptions.plist"),
            ], cwd=workdir)
            ipa_candidates = [
                f for f in os.listdir(os.path.join(workdir, "ipa_export"))
                if f.endswith(".ipa")
            ] if os.path.exists(os.path.join(workdir, "ipa_export")) else []
            if ipa_candidates:
                built_files["ipa"] = os.path.join(workdir, "ipa_export", ipa_candidates[0])
                step("  ✓ IPA erstellt / IPA built")
            else:
                return fail("IPA-Ausgabedatei nicht gefunden / IPA output not found")

        # ── Step 6: Upload to JDS Cloud ──────────────────────────────────
        step("▶ 6/6 – Upload zur JDS Cloud / Uploading to JDS Cloud")

        if "apk" in built_files:
            r = _upload(built_files["apk"], f"{build.app_name.replace(' ','_')}_v{build.version}.apk")
            if r["success"]:
                build.apk_url = r["download_url"]
                step(f"  ✓ APK URL: {build.apk_url}")
            else:
                return fail(f"APK-Upload fehlgeschlagen / failed: {r.get('error')}")

        if "ipa" in built_files:
            r = _upload(built_files["ipa"], f"{build.app_name.replace(' ','_')}_v{build.version}.ipa")
            if r["success"]:
                build.ipa_url = r["download_url"]
                step(f"  ✓ IPA URL: {build.ipa_url}")
            else:
                return fail(f"IPA-Upload fehlgeschlagen / failed: {r.get('error')}")

        build.status            = 'done'
        build.build_finished_at = timezone.now()
        build.build_log         = "\n".join(log)
        build.save()
        step("🎉 Build abgeschlossen / Build complete!")

    except subprocess.TimeoutExpired:
        fail("Build-Timeout (max. 10 Min.) / Build timed out (max 10 min)")
    except Exception as exc:
        fail(f"Unerwarteter Fehler / Unexpected error: {exc}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
