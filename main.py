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
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.warning("فشل قراءة الإعدادات: %s", e)
    return {}


def _icon_path():
    return os.path.join(_frozen_path(), "i1.ico")


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
