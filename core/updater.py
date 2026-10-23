"""Self-update mechanism for the WorshaApp (ID Card Manager).

Only meaningful when the app is frozen (a single PyInstaller ``--onefile``
``WorshaApp.exe`` sitting next to a ``data/`` folder). It:

  * asks GitHub Releases for the latest release/asset,
  * downloads the new ``WorshaApp.exe`` to a temporary file,
  * launches a small hidden helper script that waits for the current process
    to exit, then swaps the files and relaunches the app,
  * never touches the ``data/`` folder, so users keep all their data.

In non-frozen (development) mode every function degrades gracefully and the
auto-update button is simply hidden.
"""

import json
import logging
import os
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import re
import zlib

log = logging.getLogger(__name__)

# بعض أجهزة المستخدمين تعاني من متجر شهادات غير موثوق فيه (خاصة في المناطق
# التي تتدخّل في HTTPS أو أجهزة قديمة). الفحص مجرد استعلام إصدار، لذا نسمح
# بتخطي التحقق من الشهادة لضمان ظهور زر التحديث على كل الأجهزة.
_UNVERIFIED_CTX = ssl._create_unverified_context()

# Repo + asset naming. Keep in sync with main.py.
DEFAULT_REPO = "a46625282837-dotcom/printing-workshop-app"
ASSET_NAME = "WorshaApp.exe"

# Updates are fully disabled: the app must never check, download or apply a
# new build on its own. All entry points below short-circuit when False.
_UPDATES_ENABLED = False


def _debug_log(msg: str) -> None:
    """Write one line to data/update_debug.log for diagnosis (all modes)."""
    try:
        from core.runtime import data_dir
        path = os.path.join(data_dir(), "update_debug.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def is_frozen():
    """True when running from the packaged/onefile executable."""
    return bool(getattr(sys, "frozen", False))


def current_exe_path():
    """Path of the running executable (only valid when frozen)."""
    return sys.executable


def _exe_dir():
    return os.path.dirname(current_exe_path())


def _is_onedir():
    """True when running from a PyInstaller onedir layout (folder with _internal)."""
    try:
        d = os.path.join(_exe_dir(), "_internal")
        return os.path.isdir(d)
    except Exception:
        return False


def _zip_asset_name():
    # Release asset name for the zipped onedir distribution.
    return "WorshaApp_v2.zip"


def _github_api_url(repo):
    return f"https://api.github.com/repos/{repo}/releases/latest"


def _http_json(url, timeout=10):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "WorshaApp-Updater",
    })
    with urllib.request.urlopen(req, timeout=timeout, context=_UNVERIFIED_CTX) as resp:
        return json.loads(resp.read())


def _latest_tag_via_web(repo, timeout=10):
    """Fallback when api.github.com is blocked/slow: follow the github.com
    /releases/latest redirect and read the tag name from the final URL.

    Returns the tag (e.g. "1.4.5") or None on any failure.
    """
    url = f"https://github.com/{repo}/releases/latest"
    try:
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            opener.open(url, timeout=timeout, context=_UNVERIFIED_CTX)
            final = None
        except urllib.error.HTTPError as e:
            final = e.headers.get("Location", "")
        m = re.search(r"/releases/tag/([^/?#]+)", final or "")
        if not m:
            try:
                with urllib.request.urlopen(url, timeout=timeout, context=_UNVERIFIED_CTX) as resp:
                    final = resp.geturl()
                m = re.search(r"/releases/tag/([^/?#]+)", final or "")
            except Exception:
                m = None
        if not m:
            return None
        return str(m.group(1)).lstrip("v")
    except Exception as e:
        log.debug("web fallback failed: %s", e)
        return None


def _asset_browser_url(release, want_zip):
    """Return the browser_download_url for the right asset.

    onedir builds publish a ZIP (``WorshaApp_vX.Y.Z.zip`` or the legacy
    ``WorshaApp_v2.zip``); onefile builds publish the single ``WorshaApp.exe``.
    Returns None if the expected asset is absent.
    """
    tag = str(release.get("tag_name", "")).lstrip("v")
    if want_zip:
        # 1) Exact match that includes the release version.
        # 2) Legacy fixed names from earlier builds.
        # 3) Wildcard: any WorshaApp_v*.zip asset from this release.
        candidates = [f"WorshaApp_v{tag}.zip", "WorshaApp_v2.zip", "WorshaApp_update.zip"]
        for name in candidates:
            for asset in release.get("assets", []):
                if asset.get("name") == name and asset.get("browser_download_url"):
                    return asset["browser_download_url"]
        best = None
        for asset in release.get("assets", []):
            n = asset.get("name") or ""
            if n.startswith("WorshaApp_v") and n.endswith(".zip") and asset.get("browser_download_url"):
                if best is None or len(n) < len(best[0]):
                    best = (n, asset["browser_download_url"])
        if best:
            return best[1]
        return None
    for asset in release.get("assets", []):
        if asset.get("name") == ASSET_NAME and asset.get("browser_download_url"):
            return asset["browser_download_url"]
    return None


def check_for_update(current_version, repo=None):
    """Query GitHub for the latest release.

    Returns a dict {version, notes, download_url, kind} if a newer version
    exists, otherwise None. ``kind`` is ``"zip"`` for onedir installs and
    ``"exe"`` for onefile installs. Robust to any network/API failure.
    """
    if not _UPDATES_ENABLED:
        return None
    repo = repo or DEFAULT_REPO
    release = None
    try:
        release = _http_json(_github_api_url(repo))
    except Exception as e:
        log.debug("update check failed: %s", e)
        _debug_log("api.github.com failed: %r" % (e,))
    tag = None
    notes = ""
    if release is not None:
        tag = str(release.get("tag_name", "")).lstrip("v")
        notes = release.get("body") or ""
    else:
        tag = _latest_tag_via_web(repo)
        _debug_log("web fallback tag: %r" % (tag,))
    if not tag:
        _debug_log("no release tag found")
        return None
    # Compare dotted versions; treat "1.4.1" > "1.4.0".
    try:
        cur = tuple(int(x) for x in str(current_version).split("."))
        new = tuple(int(x) for x in tag.split("."))
    except Exception:
        cur, new = (0,), (0,)
    if new <= cur:
        _debug_log("current %s >= latest %s" % (current_version, tag))
        return None
    want_zip = _is_onedir()
    url = None
    if release is not None:
        url = _asset_browser_url(release, want_zip=want_zip)
        if not url:
            want_zip = not want_zip
            url = _asset_browser_url(release, want_zip=want_zip)
            if not url:
                log.debug("no suitable asset in latest release")
                _debug_log("no suitable asset in release %s" % (tag,))
                return None
    else:
        # Web fallback: build the onedir asset URL directly from the tag.
        if want_zip:
            url = f"https://github.com/{repo}/releases/download/v{tag}/WorshaApp_v{tag}.zip"
        else:
            url = f"https://github.com/{repo}/releases/download/v{tag}/{ASSET_NAME}"
    _debug_log("found update %s -> %s" % (current_version, tag))
    return {
        "version": tag,
        "notes": notes,
        "download_url": url,
        "kind": "zip" if want_zip else "exe",
    }


def _read_crc32(path):
    """Small helper reading a file for crc32 (streaming, chunked)."""
    crc = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            crc = zlib.crc32(chunk, crc)
    return crc


def _exe_signature(path):
    """Return (crc32, size) of an exe for change detection, robust to missing file."""
    try:
        return hex(_read_crc32(path)), os.path.getsize(path)
    except OSError:
        return None


def download_binary(url, dest):
    """Stream ``url`` into ``dest`` returning final size, raising on error."""
    if not _UPDATES_ENABLED:
        raise RuntimeError("updates are disabled")
    req = urllib.request.Request(url, headers={"User-Agent": "WorshaApp-Updater"})
    tmp = dest + ".part"
    try:
        with urllib.request.urlopen(req, timeout=30, context=_UNVERIFIED_CTX) as resp, open(tmp, "wb") as out:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
        os.replace(tmp, dest)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise
    return os.path.getsize(dest)


def _write_helper_script(payload):
    """Write the PowerShell payload to a temp .ps1 file (UTF-8 BOM) and return its path.

    PowerShell يقرأ اليونيكود بشكل صحيح مع BOM — بخلاف cmd الذي يقرأ ملفات
    الباتش بترميز OEM فتُكسر المسارات العربية أثناء التحديث. هذا جذر المشكلة
    التي تكسر التطبيق للمستخدمين الذي ثبتوه في مسار عربي.
    """
    fd, path = tempfile.mkstemp(prefix="worsha_upd_", suffix=".ps1")
    with os.fdopen(fd, "wb") as f:
        f.write(b"\xef\xbb\xbf")  # UTF-8 BOM
        f.write(payload.encode("utf-8"))
    return path


def _ps_quote(path):
    """Escape a path for single-quoted PowerShell string literals."""
    return "'" + str(path).replace("'", "''") + "'"


def _validate_update_zip(zip_path):
    """Verify the staged zip looks like a real WorshaApp package before swapping.

    Returns a human message if invalid, None if acceptable.
    """
    from zipfile import ZipFile
    try:
        with ZipFile(zip_path) as z:
            names = z.namelist()
    except Exception as e:
        return "الملف المُحمَّل ليس ملف ZIP صالحاً (%s)" % e
    if not names:
        return "الملف المُحمَّل فارغ"
    has_exe = any(n.replace("\\", "/").endswith("/WorshaApp.exe") for n in names)
    has_internal = any("/_internal/" in n.replace("\\", "/") for n in names)
    if not (has_exe and has_internal):
        return "المحتوى لا يطابق حزمة ورشة طباعة (WorshaApp.exe + _internal)"
    return None


def _build_onefile_ps(app_dir, current_exe, new_temp):
    """PowerShell helper (Unicode-safe) to swap exe for onefile builds."""
    exe_name = os.path.basename(current_exe).replace(".exe", "")
    return (
        "$ErrorActionPreference = 'Stop'\r\n"
        "$exe = %s\r\n"
        "$new = %s\r\n"
        "$procName = %s\r\n"
        "for ($i = 0; $i -lt 240; $i++) {\r\n"
        "    if (-not (Get-Process -Name $procName -ErrorAction SilentlyContinue)) { break }\r\n"
        "    Start-Sleep -Milliseconds 500\r\n"
        "}\r\n"
        "Start-Sleep -Milliseconds 1200\r\n"
        "$old = $exe + '.old'\r\n"
        "try {\r\n"
        "    if (Test-Path $exe) { Copy-Item $exe $old -Force -ErrorAction SilentlyContinue }\r\n"
        "    Move-Item $new $exe -Force -ErrorAction Stop\r\n"
        "    if (Test-Path $old) { Remove-Item $old -Force -ErrorAction SilentlyContinue }\r\n"
        "    Start-Process -FilePath $exe\r\n"
        "    exit 0\r\n"
        "} catch {\r\n"
        "    if ((Test-Path $old) -and -not (Test-Path $exe)) { Copy-Item $old $exe -Force }\r\n"
        "    exit 1\r\n"
        "}\r\n"
        % (_ps_quote(current_exe), _ps_quote(new_temp), _ps_quote(exe_name))
    )


def _build_onedir_ps(app_dir, zip_path):
    """PowerShell helper (Unicode-safe) to extract zip over the folder with
    data protection + integrity check + automatic rollback on failure."""
    app_q = _ps_quote(app_dir)
    zip_q = _ps_quote(zip_path)
    return (
        "$ErrorActionPreference = 'Stop'\r\n"
        "$app = %s\r\n"
        "$zip = %s\r\n"
        "for ($i = 0; $i -lt 240; $i++) {\r\n"
        "    if (-not (Get-Process -Name 'WorshaApp' -ErrorAction SilentlyContinue)) { break }\r\n"
        "    Start-Sleep -Milliseconds 500\r\n"
        "}\r\n"
        "Start-Sleep -Milliseconds 1200\r\n"
        "$extract = Join-Path $app '_extract'\r\n"
        "$internalOld = Join-Path $app '_internal_old'\r\n"
        "$dataOld = Join-Path $app '_upd_data'\r\n"
        "$dataDir = Join-Path $app 'data'\r\n"
        "$exeTarget = Join-Path $app 'WorshaApp.exe'\r\n"
        "$internalTarget = Join-Path $app '_internal'\r\n"
        "foreach ($p in @($extract, $dataOld, $internalOld)) {\r\n"
        "    if (Test-Path $p) { Remove-Item $p -Recurse -Force -ErrorAction SilentlyContinue }\r\n"
        "}\r\n"
        "# حماية بيانات المستخدم: إبعادها أثناء الاستبدال وإعادتها بعد النجاح\r\n"
        "if (Test-Path $dataDir) { Move-Item $dataDir $dataOld -ErrorAction SilentlyContinue }\r\n"
        "$failed = $true\r\n"
        "try {\r\n"
        "    Expand-Archive -Path $zip -DestinationPath $extract -Force\r\n"
        "    $top = Get-ChildItem -Path $extract -Directory | Select-Object -First 1\r\n"
        "    if (-not $top) { throw 'الحزمة بلا محتوى' }\r\n"
        "    $srcExe = Join-Path $top.FullName 'WorshaApp.exe'\r\n"
        "    $srcInternal = Join-Path $top.FullName '_internal'\r\n"
        "    if (-not (Test-Path $srcExe)) { throw 'WorshaApp.exe غير موجود في الحزمة' }\r\n"
        "    if (-not (Test-Path $srcInternal)) { throw '_internal غير موجود في الحزمة' }\r\n"
        "    if (Test-Path $internalTarget) { Move-Item $internalTarget $internalOld -Force }\r\n"
        "    Copy-Item $srcInternal $internalTarget -Recurse -Force\r\n"
        "    Copy-Item $srcExe $exeTarget -Force\r\n"
        "    $critical = @(\r\n"
        "        $exeTarget,\r\n"
        "        (Join-Path $internalTarget 'models\\id_card.pt'),\r\n"
        "        (Join-Path $internalTarget 'models\\u2netp.onnx'),\r\n"
        "        (Join-Path $internalTarget 'core\\ai_config.json')\r\n"
        "    )\r\n"
        "    $pyDll = Get-ChildItem -Path $internalTarget -Filter 'python3*.dll' -ErrorAction SilentlyContinue | Select-Object -First 1\r\n"
        "    $fail = @($critical | Where-Object { -not (Test-Path $_) })\r\n"
        "    if (-not $pyDll) { $fail += 'python3*.dll' }\r\n"
        "\r\n"
        "    if ($fail.Count -eq 0) {\r\n"
        "        if (Test-Path $dataOld) { Move-Item $dataOld $dataDir -Force }\r\n"
        "        Remove-Item $extract -Recurse -Force -ErrorAction SilentlyContinue\r\n"
        "        Remove-Item $internalOld -Recurse -Force -ErrorAction SilentlyContinue\r\n"
        "        Remove-Item $zip -Force -ErrorAction SilentlyContinue\r\n"
        "        Start-Process -FilePath $exeTarget\r\n"
        "        exit 0\r\n"
        "    }\r\n"
        "    Write-Output ('UPDATE_FAIL ' + ($fail -join '; '))\r\n"
        "} catch {\r\n"
        "    Write-Output ('UPDATE_EXC ' + $_.Exception.Message)\r\n"
        "}\r\n"
        "# تراجع كامل عند أي فشل — القرص يبقى سليماً كما كان\r\n"
        "if (Test-Path $internalTarget) { Remove-Item $internalTarget -Recurse -Force -ErrorAction SilentlyContinue }\r\n"
        "if (Test-Path $internalOld) { Move-Item $internalOld $internalTarget -Force -ErrorAction SilentlyContinue }\r\n"
        "if (Test-Path $dataOld -and -not (Test-Path $dataDir)) { Move-Item $dataOld $dataDir -Force -ErrorAction SilentlyContinue }\r\n"
        "Remove-Item $extract -Recurse -Force -ErrorAction SilentlyContinue\r\n"
        "exit 1\r\n"
        % (app_q, zip_q)
    )


def plan_self_update(update_info):
    """Download the new package and craft the swap+relaunch helper.

    Returns (helper_script_path, staged_file). The caller is responsible for
    closing the app after launching the helper.

    * onefile builds: the new ``WorshaApp.exe`` replaces the current exe.
    * onedir builds: the new ZIP (``WorshaApp_v2.zip``) is extracted over the
      application folder, replacing ``_internal`` + the exe but preserving
      ``data/``.
    """
    if not is_frozen():
        raise RuntimeError("self-update is only available in the packaged app")
    if not _UPDATES_ENABLED:
        raise RuntimeError("updates are disabled")

    kind = update_info.get("kind") or ("zip" if _is_onedir() else "exe")
    exe = current_exe_path()
    app_dir = _exe_dir()
    download_url = update_info["download_url"]

    if kind == "zip":
        return _plan_onedir_update(download_url, exe, app_dir)

    # ── onefile path ─────────────────────────────────────────────────────
    new_temp = os.path.join(app_dir, ASSET_NAME + ".new")
    log.info("downloading %s -> %s", download_url, new_temp)
    download_binary(download_url, new_temp)
    if _exe_signature(new_temp) == _exe_signature(exe):
        log.info("downloaded exe identical to current, no update needed")
        try:
            os.remove(new_temp)
        except OSError:
            pass
        return None, None

    helper = _write_helper_script(_build_onefile_ps(app_dir, exe, new_temp))
    return helper, new_temp


def _plan_onedir_update(download_url, exe, app_dir):
    """Stage a ZIP then build a helper that extracts it over the folder."""
    from zipfile import ZipFile

    zip_path = os.path.join(app_dir, _zip_asset_name() + ".new")
    log.info("downloading %s -> %s", download_url, zip_path)
    download_binary(download_url, zip_path)

    # Verify the downloaded zip before touching anything.
    invalid = _validate_update_zip(zip_path)
    if invalid:
        log.error("update zip invalid: %s", invalid)
        _debug_log("invalid update zip: %r" % (invalid,))
        try:
            os.remove(zip_path)
        except OSError:
            pass
        raise RuntimeError("update zip invalid: %s" % invalid)

    helper = _write_helper_script(_build_onedir_ps(app_dir, zip_path))
    return helper, zip_path


def apply_update(update_info):
    """Full dance: download, hide a helper, exit the app.

    Must be called right before quitting the Qt event loop / calling
    sys.exit(). Returns nothing. In non-frozen mode it just returns False.
    """
    if not _UPDATES_ENABLED:
        log.info("self-update skipped (disabled)")
        return False
    if not is_frozen():
        log.info("self-update skipped (not frozen)")
        return False
    try:
        helper, _new_temp = plan_self_update(update_info)
        if not helper:
            return False
        # Launch the helper hidden, detach from this process so it survives
        # our exit. PowerShell (مع BOM) يتعامل مع المسارات العربية بأمان — لا cmd.
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
             "-File", helper],
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0),
        )
        log.info("update helper launched; app will close now")
        return True
    except Exception as e:
        log.exception("self-update failed: %s", e)
        return False
