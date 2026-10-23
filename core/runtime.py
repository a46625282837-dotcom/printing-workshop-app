"""طبقة الموارد والمسارات الموحّدة (العمود الفقري الصامت).

كل مسار في التطبيق يجب أن يمرّ من هنا حصراً. الهدف: ألا يتكسر التطبيق
مهما تغيّر مسار التثبيت (عربي / إنجليزي / فواصل / Program Files محمي /
أسماء مستخدمين غير لاتينية / مسارات طويلة).

القواعد الثابتة:
  * الموارد القابلة للقراءة (img، models، core، ui) ← ``bundle_root()``
    (المجمّع: مجلد ``_internal``؛ المصدر: جذر المشروع).
  * البيانات القابلة للكتابة (قاعدة البيانات، الثيم، الملابس، السجلات)
    ← ``data_dir()`` دائماً بجانب شغّال التطبيق في المجمّع (لا يُمَسّ عند
    التحديث)، وإلا سبق تلقائياً إلى ``%LOCALAPPDATA%\\WorshaApp``.

لا تعدّل قواعد المسارات في هذا الملف إلا هنا. (يُفحص الالتزام تلقائياً
بواسطة ``core.selfcheck`` عند كل تشغيل حتى لا تكسره تعديلات مستقبلية.)
"""

import logging
import os
import sys

logger = logging.getLogger(__name__)


def is_frozen():
    """المجمّع (PyInstaller)؟"""
    return bool(getattr(sys, "frozen", False))


def _source_root():
    """جذر شيفرة المصدر (core/ و ui/ داخل هذا الملف)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _meipass():
    """مجلد فك الحزمة المؤقت/الداخلي إن وُجد (بالنسختين onefile و onedir)."""
    try:
        v = getattr(sys, "_MEIPASS", None)
        if isinstance(v, str) and v:
            return v
    except Exception:
        pass
    return None


def bundle_root():
    """جذر الموارد المشتركة (قراءة): _internal في المجمّع، جذر المشروع في المصدر."""
    mp = _meipass()
    if mp and os.path.isdir(mp):
        return mp
    if is_frozen():
        d = os.path.dirname(os.path.abspath(sys.executable))
        cand = os.path.join(d, "_internal")
        if os.path.isdir(cand):
            return cand
        return d
    return _source_root()


def resource_path(rel):
    """مسار مورد للقراءة من داخل الحزمة. يقبل '/', '\\\\' أو os.path.join.

    مثال: resource_path('models/id_card.pt')
    """
    rel = str(rel).replace("/", os.sep).replace("\\", os.sep)
    while rel.startswith(os.sep):
        rel = rel[len(os.sep):]
    return os.path.join(bundle_root(), rel)


def exe_dir():
    """دليل التشغيل الثابت: بجانب WorshaApp.exe في المجمّع، جذر المشروع في المصدر."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return _source_root()


def data_dir():
    """مجلد البيانات القابل للكتابة (ثابت — لا يُمسح عند التحديث أبداً).

    في المجمّع: ``<exe>/data``. إن لم يكن قابلاً للكتابة (مثل Program Files)
    ينتقل تلقائياً إلى ``%LOCALAPPDATA%\\WorshaApp`` ويُعاد استخدامه دائماً.
    """
    primary = os.path.join(exe_dir(), "data")
    try:
        os.makedirs(primary, exist_ok=True)
        _probe_writable(primary)
        return primary
    except Exception as e:
        logger.warning("data_dir غير قابل للكتابة (%s): %s — تحويل تلقائي", primary, e)
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    alt = os.path.join(base, "WorshaApp")
    try:
        os.makedirs(alt, exist_ok=True)
        _probe_writable(alt)
    except Exception as e:
        logger.warning("مجلد البيانات الاحتياطي تعذّر (%s): %s", alt, e)
        alt = os.path.expanduser("~")
    return alt


def _probe_writable(d):
    probe = os.path.join(d, ".write_probe")
    with open(probe, "w", encoding="utf-8") as f:
        f.write("ok")
    try:
        os.remove(probe)
    except OSError:
        pass


def update_log_path():
    """سجل التحديث التشخيصي (يُكتب للمستخدمين المصابين فقط)."""
    return os.path.join(data_dir(), "update_debug.log")


def diagnostics_path():
    """ملف تشخيص الإقلاع — يُرسله المستخدم عند أي مشكلة."""
    return os.path.join(data_dir(), "diagnostics.txt")


def path_is_ascii(p):
    try:
        str(p).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False