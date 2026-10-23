import sys
import os
import json
import logging
import threading
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QIcon
from PySide6.QtCore import QTimer
from app import IDCardApp
from version import APP_VERSION

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


_SERVER_URL = "https://web-production-3ebb.up.railway.app"


def _frozen_path():
    from core.runtime import bundle_root
    return bundle_root()


def _data_dir():
    from core.runtime import data_dir
    return data_dir()


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
    from core.runtime import resource_path
    for cand in ("i1.ico", "i1.jpeg", "img/i1.png", "img/i1.jpeg"):
        p = resource_path(cand)
        if os.path.exists(p):
            return p
    return resource_path("i1.ico")


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


def _start_bg_warmup():
    """تحميل نموذج إزالة الخلفية في الخلفية حتى لا ينتظر المستخدم أول صورة.

    لا يغيّر أي نتيجة — يبني جلسة rembg مبكراً بنفس النموذج والمنطق الذي
    كان يُبنى عند أول قصّ. السقوط صامت عند أي فشل (يبقى السلوك القديم).
    """
    try:
        def run():
            try:
                from core.photo_processor import warmup_rembg
                warmup_rembg()
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()
    except Exception as e:
        logging.warning("فشل تشغيل تدفئة النموذج: %s", e)


def _handle_cli_flags():
    """أوامر صامتة مفيدة للمطور: --selftest، --diag."""
    args = [a for a in sys.argv[1:] if a in ("--selftest", "--diag")]
    if "--selftest" in args:
        from core import selftest
        sys.exit(selftest.run())
    if "--diag" in args:
        from core import selfcheck
        print(selfcheck.build_report()[0])
        sys.exit(0)


def _show_startup_issues(problems):
    """إذا وجد فحص الإقلاع نقصاً حرجاً — رسالة عربية واضحة بدل أقسام ميتة صامتة."""
    if not problems:
        return
    try:
        from core.runtime import diagnostics_path
        short = list(dict.fromkeys(problems))[:4]
        body = "\n".join("• " + p for p in short)
        more = "..." if len(problems) > 4 else ""
        box = QMessageBox()
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("تنبيه - ملف التشخيص")
        box.setText("يوجد نقص في الملفات الأساسية قد يعطّل بعض الأقسام.")
        box.setInformativeText(
            body + more +
            "\n\nأرسل هذا الملف إلى المطور لتشخيص المشكلة:\n" + diagnostics_path())
        box.exec()
    except Exception as e:
        logging.warning("فشل عرض تنبيه التشخيص: %s", e)


def main():
    _handle_cli_flags()
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
    startup_problems = []
    try:
        from core import selfcheck
        startup_problems = selfcheck.write_diagnostics()
        logging.info("تقرير التشخيص مكتوب: %d مشكلة أولية", len(startup_problems))
    except Exception as e:
        logging.warning("فشل فحص الإقلاع: %s", e)
    _register_wwk_extension()
    _start_bg_warmup()
    app = QApplication(sys.argv)
    app.setApplicationName("ورشة طباعة")
    icon = QIcon(_icon_path())
    if not icon.isNull():
        app.setWindowIcon(icon)
    window = IDCardApp(use_api=use_api, update_ctx=_update_contexts())
    window.show()
    if frozen and startup_problems:
        QTimer.singleShot(2000, lambda: _show_startup_issues(startup_problems))
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
