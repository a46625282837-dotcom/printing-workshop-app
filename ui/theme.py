"""نظام السمات (فاتح/ليلي) للتطبيق.

الفكرة: الواجهة مكتوبة بألوان فاتحة inline. بدل تعديل كل سطر، نحتفظ بنسخة
الألوان الفاتحة لكل widget ثم نولّد منها نسخة داكنة عند الحاجة عبر ``darkify``.
هذا يسمح بتبديل السمة فورياً على كامل التطبيق دون إعادة بناء الواجهات.
"""
import json
import re
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget

LIGHT = "light"
DARK = "dark"

_mode = LIGHT
_callbacks = []
_filter = None
_busy = False

_PALETTES = {
    LIGHT: {
        "Window": "#ffffff", "WindowText": "#333333", "Base": "#ffffff",
        "AlternateBase": "#f2f2f2", "Text": "#333333", "Button": "#f0f0f0",
        "ButtonText": "#333333", "ToolTipBase": "#ffffe1", "ToolTipText": "#333333",
        "Highlight": "#1a73e8", "HighlightedText": "#ffffff",
        "PlaceholderText": "#9a9a9a", "Mid": "#c0c0c0", "Dark": "#9a9a9a",
        "Light": "#ffffff", "Midlight": "#e3e3e3", "Shadow": "#767676",
    },
    DARK: {
        "Window": "#1f1f1f", "WindowText": "#e8e8e8", "Base": "#2b2b2b",
        "AlternateBase": "#323232", "Text": "#e8e8e8", "Button": "#3a3a3a",
        "ButtonText": "#e8e8e8", "ToolTipBase": "#3a3a3a", "ToolTipText": "#e8e8e8",
        "Highlight": "#1a73e8", "HighlightedText": "#ffffff",
        "PlaceholderText": "#8a8a8a", "Mid": "#555555", "Dark": "#1a1a1a",
        "Light": "#4a4a4a", "Midlight": "#3a3a3a", "Shadow": "#0d0d0d",
    },
}

# ألوان الخلفيات/الحدود الفاتحة -> مكافئاتها الداكنة
_BG_L2D = {
    "#ffffff": "#2b2b2b", "#fff": "#2b2b2b", "#fefefe": "#2b2b2b",
    "white": "#2b2b2b",
    "#fbfbfb": "#262626", "#fafafa": "#262626", "#f8f9fa": "#262626",
    "#f5f6fa": "#262626", "#f5f5f5": "#262626", "#f4f4f4": "#262626",
    "#f2f2f2": "#2c2c2c", "#f0f0f0": "#2f2f2f", "#eeeeee": "#2c2c2c",
    "#eee": "#2c2c2c", "#e9eef3": "#232323", "#e8e8e8": "#333333",
    "#e7e6e6": "#2c2c2c", "#e6f2fb": "#232323", "#e3f2fd": "#232323",
    "#cceeff": "#1f1f1f", "#e0e0e0": "#3a3a3a", "#dddddd": "#3a3a3a",
    "#ddd": "#3a3a3a", "#cccccc": "#474747", "#ccc": "#474747",
    "#c8c8c8": "#4a4a4a", "#c0c0c0": "#4a4a4a", "#bdbdbd": "#4a4a4a",
}

# ألوان النصوص الداكنة -> مكافئاتها الفاتحة
_FG_D2L = {
    "#000000": "#f0f0f0", "#000": "#f0f0f0", "black": "#f0f0f0",
    "#111111": "#ececec", "#111": "#ececec", "#1a1a1a": "#e6e6e6",
    "#222222": "#e8e8e8", "#222": "#e8e8e8", "#2b2b2b": "#e8e8e8",
    "#333333": "#e8e8e8", "#333": "#e8e8e8", "#444444": "#dcdcdc",
    "#444": "#dcdcdc", "#4a4a4a": "#d8d8d8", "#555555": "#cfcfcf",
    "#555": "#cfcfcf", "#5a5a5a": "#c8c8c8", "#666666": "#c4c4c4",
    "#666": "#c4c4c4", "#777777": "#b8b8b8", "#777": "#b8b8b8",
    "#888888": "#acacac", "#888": "#acacac",     "#999999": "#a0a0a0",
    "#999": "#a0a0a0", "#cccccc": "#8a8a8a", "#ccc": "#8a8a8a",
    "#aaaaaa": "#949494", "#aaa": "#949494",
    "#808080": "#9a9a9a", "gray": "#9a9a9a", "grey": "#9a9a9a",
    "dimgray": "#a8a8a8", "dimgrey": "#a8a8a8",
}

_BG_EXTRA = {"gridline-color", "border-color", "outline-color"}
_HEX_RE = re.compile(r'#[0-9a-fA-F]{3,8}\b')
_NAME_RE = re.compile(r'\b([A-Za-z]+)\b')
_DECL_RE = re.compile(r'([A-Za-z][-\w]*)\s*:\s*([^;{}]+)')


def _prefs_path():
    from core.runtime import data_dir
    return Path(data_dir()) / "ui_prefs.json"


def current_mode():
    return _mode


def is_dark():
    return _mode == DARK


def canvas_color():
    """لون خلفية مساحات العمل حول الورقة (يميّز الورقة البيضاء)."""
    return "#2a2a2a" if _mode == DARK else "#aab2bd"


def load_mode():
    try:
        data = json.loads(_prefs_path().read_text(encoding="utf-8"))
        m = data.get("theme")
        if m in (LIGHT, DARK):
            return m
    except Exception:
        pass
    return LIGHT


def save_mode(mode):
    try:
        p = _prefs_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data["theme"] = mode
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def on_change(cb):
    _callbacks.append(cb)


def _map_value(value, mapping):
    def repl_hex(m):
        return mapping.get(m.group(0).lower(), m.group(0))

    def repl_name(m):
        key = m.group(0).lower()
        return mapping.get(key, m.group(0))

    value = _HEX_RE.sub(repl_hex, value)
    if any(not k.startswith("#") for k in mapping):
        value = _NAME_RE.sub(repl_name, value)
    return value


def darkify(qss):
    """يرجع نسخة داكنة من stylesheet مكتوب بألوان فاتحة."""
    if not qss:
        return qss

    def repl(m):
        rest = m.string[m.end():]
        if rest.lstrip()[:1] == "{":
            return m.group(0)
        prop = m.group(1)
        low = prop.lower()
        val = m.group(2)
        if "background" in low or low in _BG_EXTRA:
            val = _map_value(val, _BG_L2D)
        elif low.endswith("color"):
            val = _map_value(val, _FG_D2L)
        elif low.startswith(("border", "outline")):
            val = _map_value(val, _BG_L2D)
        return "%s: %s" % (prop, val)

    return _DECL_RE.sub(repl, qss)


def style_widget(widget):
    global _busy
    if _busy or not isinstance(widget, QWidget):
        return
    try:
        qss = widget.styleSheet()
    except Exception:
        return
    if not qss:
        return
    stored = widget.property("_lightQss")
    if not isinstance(stored, str):
        stored = qss
        widget.setProperty("_lightQss", stored)
    dark = darkify(stored)
    if qss not in (stored, dark):
        stored = qss
        widget.setProperty("_lightQss", stored)
        dark = darkify(stored)
    desired = stored if _mode == LIGHT else dark
    if qss != desired:
        _busy = True
        try:
            widget.setStyleSheet(desired)
        finally:
            _busy = False


def transform_all():
    app = QApplication.instance()
    if app is None:
        return
    for w in app.allWidgets():
        style_widget(w)


def _build_palette(mode):
    pal = QPalette()
    name_by_role = {
        "Window": QPalette.Window, "WindowText": QPalette.WindowText,
        "Base": QPalette.Base, "AlternateBase": QPalette.AlternateBase,
        "Text": QPalette.Text, "Button": QPalette.Button,
        "ButtonText": QPalette.ButtonText, "ToolTipBase": QPalette.ToolTipBase,
        "ToolTipText": QPalette.ToolTipText, "Highlight": QPalette.Highlight,
        "HighlightedText": QPalette.HighlightedText,
        "PlaceholderText": QPalette.PlaceholderText, "Mid": QPalette.Mid,
        "Dark": QPalette.Dark, "Light": QPalette.Light,
        "Midlight": QPalette.Midlight, "Shadow": QPalette.Shadow,
    }
    for key, role in name_by_role.items():
        val = _PALETTES[mode].get(key)
        if val:
            pal.setColor(role, QColor(val))
    return pal


class _ThemeFilter(QObject):
    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Show and isinstance(obj, QWidget):
            style_widget(obj)
        return False


def install(app):
    global _filter
    if _filter is None:
        _filter = _ThemeFilter()
        app.installEventFilter(_filter)


def apply(app, mode=None):
    global _mode
    if mode is None:
        mode = _mode
    if mode not in (LIGHT, DARK):
        mode = LIGHT
    _mode = mode
    try:
        app.styleHints().setColorScheme(
            Qt.ColorScheme.Dark if mode == DARK else Qt.ColorScheme.Light)
    except Exception:
        pass
    try:
        app.setPalette(_build_palette(mode))
    except Exception:
        pass
    transform_all()
    for cb in list(_callbacks):
        try:
            cb(mode)
        except Exception:
            pass
