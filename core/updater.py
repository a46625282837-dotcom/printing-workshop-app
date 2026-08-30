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
import subprocess
import sys
import tempfile
import urllib.request
import zlib

log = logging.getLogger(__name__)

# Repo + asset naming. Keep in sync with main.py.
DEFAULT_REPO = "a46625282837-dotcom/printing-workshop-app"
ASSET_NAME = "WorshaApp.exe"


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


def _http_json(url, timeout=8):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "WorshaApp-Updater",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _asset_browser_url(release, want_zip):
    """Return the browser_download_url for the right asset.

    onedir builds publish a ZIP (``WorshaApp_v2.zip``); onefile builds publish
    the single ``WorshaApp.exe``. Returns None if the expected asset is absent.
    """
    wanted = _zip_asset_name() if want_zip else ASSET_NAME
    for asset in release.get("assets", []):
        if asset.get("name") == wanted and asset.get("browser_download_url"):
            return asset["browser_download_url"]
    return None


def check_for_update(current_version, repo=None):
    """Query GitHub for the latest release.

    Returns a dict {version, notes, download_url, kind} if a newer version
    exists, otherwise None. ``kind`` is ``"zip"`` for onedir installs and
    ``"exe"`` for onefile installs. Robust to any network/API failure.
    """
    repo = repo or DEFAULT_REPO
    try:
        release = _http_json(_github_api_url(repo))
    except Exception as e:
        log.debug("update check failed: %s", e)
        return None
    tag = str(release.get("tag_name", "")).lstrip("v")
    if not tag:
        return None
    # Compare dotted versions; treat "1.4.1" > "1.4.0".
    try:
        cur = tuple(int(x) for x in str(current_version).split("."))
        new = tuple(int(x) for x in tag.split("."))
    except Exception:
        cur, new = (0,), (0,)
    if new <= cur:
        return None
    want_zip = _is_onedir()
    url = _asset_browser_url(release, want_zip=want_zip)
    if not url:
        want_zip = not want_zip
        url = _asset_browser_url(release, want_zip=want_zip)
        if not url:
            log.debug("no suitable asset in latest release")
            return None
    return {
        "version": tag,
        "notes": release.get("body") or "",
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
    req = urllib.request.Request(url, headers={"User-Agent": "WorshaApp-Updater"})
    tmp = dest + ".part"
    try:
        with urllib.request.urlopen(req, timeout=30) as resp, open(tmp, "wb") as out:
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
    """Write the batch payload to a temp file and return its path."""
    fd, path = tempfile.mkstemp(prefix="worsha_upd_", suffix=".bat")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(payload)
    return path


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

    exe_quoted = '"' + exe + '"'
    new_quoted = '"' + new_temp + '"'
    target_quoted = '"' + os.path.join(app_dir, ASSET_NAME) + '"'
    payload = (
        "@echo off\r\n"
        "setlocal\r\n"
        "set NEW=%s\r\n"
        "set TARGET=%s\r\n"
        ":wait\r\n"
        "tasklist /FI \"IMAGENAME eq WorshaApp.exe\" | find /I \"WorshaApp.exe\" >nul\r\n"
        "if not errorlevel 1 (\r\n"
        "  timeout /t 2 /nobreak >nul\r\n"
        "  goto wait\r\n"
        ")\r\n"
        "move /Y \"%NEW%\" \"%TARGET%\" >nul 2>&1\r\n"
        "start \"\" \"%TARGET%\" \r\n"
        "exit /b 0\r\n"
        % (new_quoted, target_quoted)
    )
    helper = _write_helper_script(payload)
    return helper, new_temp


def _plan_onedir_update(download_url, exe, app_dir):
    """Stage a ZIP then build a helper that extracts it over the folder."""
    from zipfile import ZipFile

    zip_path = os.path.join(app_dir, _zip_asset_name() + ".new")
    log.info("downloading %s -> %s", download_url, zip_path)
    download_binary(download_url, zip_path)

    # Detect the top-level folder inside the zip (e.g. "WorshaApp/...").
    top = None
    try:
        with ZipFile(zip_path) as z:
            names = z.namelist()
            if names:
                first = names[0]
                top = first.split('/')[0] if '/' in first else None
    except Exception as e:
        log.warning("could not inspect zip: %s", e)

    app_dir_q = '"' + app_dir + '"'
    zip_q = '"' + zip_path + '"'
    data_q = '"' + os.path.join(app_dir, "data") + '"'
    internal_q = '"' + os.path.join(app_dir, "_internal") + '"'
    exe_q = '"' + exe + '"'
    top_dir = os.path.join(app_dir, top) if top else app_dir
    top_q = '"' + top_dir + '"'

    payload = (
        "@echo off\r\n"
        "setlocal enabledelayedexpansion\r\n"
        "set APP=%s\r\n"
        "set ZIP=%s\r\n"
        "set TOP=%s\r\n"
        "set NEWDATA=%APP%\\_upd_data\r\n"
        ":wait\r\n"
        "tasklist /FI \"IMAGENAME eq WorshaApp.exe\" | find /I \"WorshaApp.exe\" >nul\r\n"
        "if not errorlevel 1 (\r\n"
        "  timeout /t 2 /nobreak >nul\r\n"
        "  goto wait\r\n"
        ")\r\n"
        "if exist \"%APP%\\data\" move /Y \"%APP%\\data\" \"%NEWDATA%\" >nul 2>&1\r\n"
        "if exist \"%APP%\\_internal\" rd /s /q \"%APP%\\_internal\" >nul 2>&1\r\n"
        "rmdir /s /q \"%APP%\\_extract\" >nul 2>&1\r\n"
        "mkdir \"%APP%\\_extract\" >nul 2>&1\r\n"
        "tar -xf \"%ZIP%\" -C \"%APP%\\_extract\" >nul 2>&1\r\n"
        "if exist \"%TOP%\\WorshaApp.exe\" (\r\n"
        "  copy /Y \"%TOP%\\WorshaApp.exe\" \"%APP%\\WorshaApp.exe\" >nul 2>&1\r\n"
        "  xcopy /E /I /Y \"%TOP%\\_internal\" \"%APP%\\_internal\" >nul 2>&1\r\n"
        ") else (\r\n"
        "  copy /Y \"%APP%\\_extract\\WorshaApp.exe\" \"%APP%\\WorshaApp.exe\" >nul 2>&1\r\n"
        "  xcopy /E /I /Y \"%APP%\\_extract\\_internal\" \"%APP%\\_internal\" >nul 2>&1\r\n"
        ")\r\n"
        "rmdir /s /q \"%APP%\\_extract\" >nul 2>&1\r\n"
        "del /F /Q \"%ZIP%\" >nul 2>&1\r\n"
        "if exist \"%NEWDATA%\" move /Y \"%NEWDATA%\" \"%APP%\\data\" >nul 2>&1\r\n"
        "start \"\" \"%APP%\\WorshaApp.exe\" \r\n"
        "exit /b 0\r\n"
        % (app_dir_q, zip_q, top_q)
    )
    helper = _write_helper_script(payload)
    return helper, zip_path


def apply_update(update_info):
    """Full dance: download, hide a helper, exit the app.

    Must be called right before quitting the Qt event loop / calling
    sys.exit(). Returns nothing. In non-frozen mode it just returns False.
    """
    if not is_frozen():
        log.info("self-update skipped (not frozen)")
        return False
    try:
        helper, _new_temp = plan_self_update(update_info)
        if not helper:
            return False
        # Launch the helper hidden, detach from this process so it survives
        # our exit.
        subprocess.Popen(
            ["cmd.exe", "/c", helper],
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0),
        )
        log.info("update helper launched; app will close now")
        return True
    except Exception as e:
        log.exception("self-update failed: %s", e)
        return False
