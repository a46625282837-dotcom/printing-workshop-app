"""فحص النزاهة عند التشغيل + مولّد تقرير التشخيص.

يقوم عند كل إقلاع بفحص:
  * سلامة الموارد الأساسية (إصدار، نماذج، أيقونات، إعدادات).
  * توفر المكتبات لكل قسم (هويات / صور / طباعة ملفات).
  * سلامة مسار التثبيت (أحرف غير لاتينية، طول، قابلية كتابة data).
  * عدم تجاوز أي شيفرة مستقبلية لطبقة ``runtime`` (حماية التعديلات).

يكتب النتيجة إلى ``data/diagnostics.txt`` — ملف واحد يرسله المستخدم
المتضرر فيعرف السبب الجذري فوراً بدون تخمين.
"""

import importlib.util
import logging
import os
import platform
import re
import sys

logger = logging.getLogger(__name__)

from core import runtime

# ملفات حرجة يجب أن تكون داخل الحزمة بالضرورة.
CRITICAL_BUNDLE = [
    "models/card_detector.onnx",
    "models/id_card.pt",
    "models/id_card.onnx",
    "models/u2netp.onnx",
    "models/isnet-general-use.onnx",
    "core/ai_config.json",
]
ICON_CANDIDATES = ["img/i1.jpeg", "img/i1.png", "i1.jpeg", "i1.ico"]

# المكتبات وراء كل قسم — تظهر في التشخيص حتى يعرف المطور أي قسم سيتعطل.
LIBS = [
    ("torch", "الهويات (كشف المحتوى)"),
    ("ultralytics", "الهويات (نموذج .pt)"),
    ("onnxruntime", "الهويات/الصور (وقص الخلفية)"),
    ("fitz", "طباعة الملفات (PDF عبر pymupdf)"),
    ("PIL", "صور"),
    ("cv2", "معالجة صور"),
    ("PySide6.QtWidgets", "الواجهة"),
    ("PySide6.QtPrintSupport", "الطباعة"),
    ("scipy", "تعبئة ثقوب القناع (اختياري)"),
    ("rembg", "إزالة خلفية (اختياري — لها بديل مباشر)"),
]

# أنماط تُكشف في ui/core خارج قائمة الاستثناءات = تجاوز لطبقة المسارات.
_BYPASS_PATTERNS = [
    r"sys\._MEIPASS",
    r"sys\s*\.\s*frozen",
    r"os\.path\.join\([^)]*sys\.executable",
    r"os\.path\.dirname\(os\.path\.dirname\(__file__\)\)",
    r"os\.path\.dirname\(os\.path\.dirname\(os\.path\.abspath\(__file__\)\)\)",
    r"getattr\(sys,\s*['\"]_MEIPASS['\"]",
]
# استثناءات شرعية (الطبقة نفسها والموكَّلة لها).
_ALLOWED_MODULES = {"runtime.py", "selfcheck.py", "selftest.py", "local_yolo.py"}


def module_found(name):
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def resource_manifest():
    """قائمة (مسار, حالة) للموارد الحرجة."""
    out = []
    for rel in CRITICAL_BUNDLE:
        p = runtime.resource_path(rel)
        out.append((p, os.path.exists(p), os.path.getsize(p) if os.path.exists(p) else 0))
    return out


def scan_source_for_bypass():
    """يبحث في ui/ core عن أي شيفرة تجاوزت طبقة المسارات (حماية المستقبل)."""
    root = runtime._source_root()
    found = []
    for base in ("core", "ui"):
        d = os.path.join(root, base)
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for f in names:
            if not f.endswith(".py") or f in _ALLOWED_MODULES:
                continue
            p = os.path.join(d, f)
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                    txt = fh.read()
            except OSError:
                continue
            for pat in _BYPASS_PATTERNS:
                if re.search(pat, txt):
                    found.append(os.path.join(base, f))
                    break
    return found


def build_report():
    """يجمع كل بيانات التشخيص ويعيد (نص التقرير، قائمة المشاكل)."""
    lines = []
    problems = []

    import version as _ver
    lines.append("=== WorshaApp Diagnostics ===")
    lines.append("version=%s" % _ver.APP_VERSION)
    lines.append("mode=%s" % ("frozen" if runtime.is_frozen() else "source"))
    lines.append("_MEIPASS=%s" % runtime._meipass())
    lines.append("bundle_root=%s" % runtime.bundle_root())
    lines.append("exe_dir=%s" % runtime.exe_dir())
    lines.append("python=%s (%s)" % (platform.python_version(), sys.executable))
    lines.append("os=%s %s" % (platform.system(), platform.version()))
    lines.append("machine=%s" % platform.machine())

    ddir = runtime.data_dir()
    ascii_ok = runtime.path_is_ascii(runtime.exe_dir())
    lines.append("data_dir=%s" % ddir)
    lines.append("data_writable=%s" % (os.access(ddir, os.W_OK) if os.path.isdir(ddir) else False))
    lines.append("install_path_ascii=%s" % ascii_ok)
    lines.append("install_path_len=%d" % len(runtime.exe_dir()))
    lines.append("long_paths_reg_attempt=%s" % _try_enable_long_paths())

    lines.append("--- critical resources ---")
    for p, exists, size in resource_manifest():
        lines.append("%s => %s (%d bytes)" % (p, "OK" if exists else "MISSING", size))
        if not exists:
            problems.append("مورد حاسم مفقود: %s" % p)

    icon_found = any(os.path.exists(runtime.resource_path(c)) for c in ICON_CANDIDATES)
    lines.append("icons=%s" % ("OK" if icon_found else "MISSING"))
    if not icon_found:
        problems.append("أيقونات التطبيق مفقودة")

    legs = os.path.join(runtime._meipass() or runtime.bundle_root(),
                        "PySide6", "plugins", "imageformats")
    gi_ok = os.path.isdir(legs)
    lines.append("qt_imageformats=%s (%s)" % ("OK" if gi_ok else "MISSING", legs))
    if runtime.is_frozen() and not gi_ok:
        problems.append("إضافات صيغ الصور في Qt مفقودة (الصور قد لا تظهر)")

    lines.append("--- libraries ---")
    libs = []
    for name, purpose in LIBS:
        ok = module_found(name)
        libs.append(ok)
        lines.append("%s=%s (%s)" % (name, "OK" if ok else "MISSING", purpose))
    if runtime.is_frozen() and not libs[0] and not libs[2]:
        problems.append("مكتبتا الهويات (torch/onnxruntime) مفقودتان — قسم الهويات سيتعطل")
    if runtime.is_frozen() and not libs[3]:
        problems.append("pymupdf مفقود — طباعة ملفات PDF ستفشل")

    bypass = scan_source_for_bypass()
    if bypass:
        lines.append("--- bypass warnings (طبقة المسارات) ---")
        lines.extend("BYPASS: %s" % b for b in bypass)
    else:
        lines.append("bypass_scan: clean (لا توجد شيفرة تتجاوز core.runtime)")

    return "\n".join(lines) + "\n", problems


def _try_enable_long_paths():
    """محاولة لطيفة لتفعيل المسارات الطويلة على الجهاز (تُسجَّل فقط)."""
    try:
        import winreg
        key = r"SYSTEM\CurrentControlSet\Control\FileSystem"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "LongPathsEnabled", 0, winreg.REG_DWORD, 1)
        return "ok"
    except Exception:
        return "no-permission"


def write_diagnostics():
    """يكتب data/diagnostics.txt ويعيد (نص، مشاكل). سريع — آمن لأي إقلاع."""
    try:
        report, problems = build_report()
        with open(runtime.diagnostics_path(), "w", encoding="utf-8") as f:
            f.write(report)
        return problems
    except Exception as e:
        logger.warning("فشل كتابة التشخيص: %s", e)
        return []


def startup_problems():
    """مُحكّمة الإقلاع: تعيد قائمة مشاكل أساسية (أقل كلاماً، أسرع)."""
    try:
        return build_report()[1]
    except Exception as e:
        logger.warning("startup check failed: %s", e)
        return []