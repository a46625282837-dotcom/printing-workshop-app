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


def _data_dir():
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            bundled = os.path.join(meipass, "data")
            if os.path.exists(bundled):
                return bundled
        return os.path.join(os.path.dirname(sys.executable), "data")
    return os.path.join(os.path.dirname(__file__), "data")


def _config_path():
    return os.path.join(_data_dir(), "app_config.json")


def _load_config():
    path = _config_path()
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.warning("فشل قراءة الإعدادات: %s", e)
    return {}


def _save_config(cfg):
    path = _config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.warning("فشل حفظ الإعدادات: %s", e)


def _icon_path():
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "i1.ico")
    return os.path.join(os.path.dirname(__file__), "i1.ico")


def main():
    cfg = _load_config()
    use_api = cfg.get("api_mode", False) or os.environ.get("IDCARD_API_MODE", "").lower() in ("1", "true", "yes")
    if use_api:
        from core import api_client
        server_url = cfg.get("server_url") or os.environ.get("IDCARD_SERVER_URL", "http://localhost:5000")
        api_client.set_server_url(server_url)
        logging.info("API mode enabled: %s", server_url)
    app = QApplication(sys.argv)
    app.setApplicationName("ورشة طباعة")
    icon = QIcon(_icon_path())
    if not icon.isNull():
        app.setWindowIcon(icon)
    window = IDCardApp(use_api=use_api)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
