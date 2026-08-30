import sys
import os
import json
import logging
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon
from app import IDCardApp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


_SERVER_URL = "https://printing-workshop-api.onrender.com"


def _frozen_path():
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(__file__)


def _data_dir():
    return os.path.join(_frozen_path(), "data")


def _load_config():
    path = os.path.join(_data_dir(), "app_config.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8-sig") as f:
                return json.load(f)
        except Exception as e:
            logging.warning("فشل قراءة الإعدادات: %s", e)
    return {}


def _icon_path():
    return os.path.join(_frozen_path(), "i1.ico")


def _register_wwk_extension():
    if os.name != "nt":
        return
    try:
        import winreg
        exe = sys.executable if getattr(sys, "frozen", False) else f'"{sys.executable}" "{os.path.join(os.path.dirname(__file__), "main.py")}"'
        icon = _icon_path()
        key_path = r"Software\Classes\.wwk"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "WorshaFile")
        key_path = r"Software\Classes\WorshaFile"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "ملف ورشة طباعة")
        key_path = r"Software\Classes\WorshaFile\DefaultIcon"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, icon)
        key_path = r"Software\Classes\WorshaFile\shell\open\command"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'{exe} "%1"')
        key_path = r"Software\Classes\.wwk\OpenWithProgids"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "WorshaFile", 0, winreg.REG_NONE, b"")
        logging.info("تم تسجيل امتداد .wwk")
    except Exception as e:
        logging.warning("فشل تسجيل امتداد .wwk: %s", e)


APP_VERSION = "2.1.0"
_GITHUB_REPO = "a46625282837-dotcom/printing-workshop-app"
_DOWNLOAD_PAGE = "https://a46625282837-dotcom.github.io/worsha-download/"

# When True the app periodically checks GitHub Releases for a newer build and
# shows an update arrow button in the top bar. Clicking it downloads the new
# exe, closes the app, installs it and relaunches — without touching user data.
ENABLE_UPDATE_CHECK = False


def _update_contexts():
    """Build the updater callback bundle used by the UI (frozen app only).

    The default source is the public GitHub repo. For a *closed* test (so an
    update reaches only your own machine and never other users), you can point
    the app at a private/test repo or a custom download URL by adding optional
    keys to ``data/app_config.json``:

        { "update_repo": "you/test-repo",
          "update_download_page": "https://..." }

    When those keys are absent the app falls back to the production repo.
    """
    if not ENABLE_UPDATE_CHECK or not getattr(sys, "frozen", False):
        return None
    cfg = _load_config()
    return {
        "version": APP_VERSION,
        "repo": cfg.get("update_repo") or _GITHUB_REPO,
        "download_page": cfg.get("update_download_page") or _DOWNLOAD_PAGE,
    }


def main():
    frozen = getattr(sys, "frozen", False)
    if frozen:
        use_api = True
        cfg = _load_config()
        server_url = cfg.get("server_url", _SERVER_URL)
    else:
        cfg = _load_config()
        use_api = cfg.get("api_mode", False) or os.environ.get("IDCARD_API_MODE", "").lower() in ("1", "true", "yes")
        server_url = cfg.get("server_url") or os.environ.get("IDCARD_SERVER_URL", "http://localhost:5000")
    if use_api:
        from core import api_client
        api_client.set_server_url(server_url)
        logging.info("API mode enabled: %s", server_url)
    _register_wwk_extension()
    app = QApplication(sys.argv)
    app.setApplicationName("ورشة طباعة")
    icon = QIcon(_icon_path())
    if not icon.isNull():
        app.setWindowIcon(icon)
    window = IDCardApp(use_api=use_api, update_ctx=_update_contexts())
    window.show()
    file_arg = None
    for arg in sys.argv[1:]:
        if os.path.isfile(arg) and arg.lower().endswith(".wwk"):
            file_arg = arg
            break
    if file_arg:
        window.open_file_arg(file_arg)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
