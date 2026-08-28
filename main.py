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


APP_VERSION = "1.4.0"
_GITHUB_REPO = "a46625282837-dotcom/printing-workshop-app"
_DOWNLOAD_PAGE = "https://a46625282837-dotcom.github.io/worsha-download/"

# Update popup is disabled: the developer shares new releases directly with
# users by link. Set to True to re-enable the "update available" popup.
ENABLE_UPDATE_CHECK = False


def _check_for_update():
    try:
        import urllib.request
        url = f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest"
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github.v3+json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            tag = data.get("tag_name", "").lstrip("v")
            if tag and tag != APP_VERSION:
                from PySide6.QtCore import QSettings
                settings = QSettings("ورشة طباعة", "App")
                last_shown = settings.value("update_shown_version", "", type=str)
                if last_shown == tag:
                    return None
                return {"version": tag, "notes": data.get("body", "")}
    except Exception as e:
        logging.debug("Update check failed: %s", e)
    return None


def _notify_update(update_info):
    from PySide6.QtWidgets import QMessageBox
    from PySide6.QtCore import QSettings
    msg = QMessageBox()
    msg.setWindowTitle("تحديث جديد متاح")
    msg.setIcon(QMessageBox.Information)
    msg.setText(f"إصدار {update_info['version']} متاح!")
    msg.setInformativeText(f"الإصدار الحالي: {APP_VERSION}\nالإصدار الجديد: {update_info['version']}\n\nهل تريد التحميل؟")
    msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    msg.setDefaultButton(QMessageBox.Yes)
    result = msg.exec()
    settings = QSettings("ورشة طباعة", "App")
    settings.setValue("update_shown_version", update_info["version"])
    if result == QMessageBox.Yes:
        import webbrowser
        webbrowser.open(_DOWNLOAD_PAGE)


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
    window = IDCardApp(use_api=use_api)
    window.show()
    if frozen and ENABLE_UPDATE_CHECK:
        update = _check_for_update()
        if update:
            _notify_update(update)
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
