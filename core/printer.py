import logging
import ctypes
import ctypes.wintypes as wt
import subprocess
import os
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
from PySide6.QtGui import QPainter, QPageSize, QPageLayout
from PySide6.QtCore import QRectF, QSettings, QSizeF, Qt
from PySide6.QtWidgets import QGraphicsScene, QWidget, QMessageBox, QInputDialog

PHOTO_PAPER_KEYWORDS = ["ورق صور"]


def _is_photo_paper(paper_type_name):
    if not paper_type_name:
        return False
    return any(kw in paper_type_name for kw in PHOTO_PAPER_KEYWORDS)

logger = logging.getLogger(__name__)

A4_WIDTH_MM = 210
A4_HEIGHT_MM = 297

NO_PRINT_KEY = 1000

_settings = QSettings("ورشة طباعة", "Printer")
_selected_printer_name = _settings.value("default_printer", None)
_last_paper_type = _settings.value("last_paper_type", None)

PAPER_TYPES = {
    "ورق عادي": 1,
    "ورق بوند": 2,
    "ورق مدعم": 8,
    "ورق صور فوتو 260 غ": 256,
    "ورق صور لامع 180 غ": 256,
    "ورق صور لامع 230 غ": 257,
    "ورق صور غير لامع 180 غ": 258,
    "ورق صور غير لامع 230 غ": 259,
    "كرتون": 260,
    "شفاف": 261,
    "ورق معاد تدويره": 262,
    "كرافت": 263,
    "مظروف": 264,
    "ملصقات": 265,
}

PAPER_TYPE_NAMES = list(PAPER_TYPES.keys())

# ثوابت هيكل DEVMODE (قيم SDK، غير متوفرة داخل win32print)
DM_ORIENTATION = 1
DM_PAPERSIZE = 2
DM_PAPERLENGTH = 4
DM_PAPERWIDTH = 8
DM_COPIES = 256
DM_PRINTQUALITY = 1024
DM_COLOR = 2048
DM_DUPLEX = 4096
DMORIENT_PORTRAIT = 1
DMORIENT_LANDSCAPE = 2
DMCOLOR_MONOCHROME = 1
DMCOLOR_COLOR = 2
DMDUP_SIMPLEX = 1
DMDUP_VERTICAL = 2
DMDUP_HORIZONTAL = 3

# آخر DEVMODE مُلتقَط (بعد تحرير المستخدم لتفضيلات الطباعة) لكل طابعة
_captured_devmodes = {}


def capture_printer_preferences(printer_name, parent_hwnd=0):
    """فتح نافذة تفضيلات الطباعة (Document Properties) وعَيْش DEVMODE المُعدَّل.

    - إذا سبق للمستخدم تعديل التفضيلات في نفس الجلسة، تُفتح النافذة وتُعرض آخر
      التعديلات (DM_IN_BUFFER) بدل إعادة التمثيل من إعدادات السائق الافتراضية.
    - عند الضغط OK: يُحفظ DEVMODE المُعدَّل ليُحقن في دورة الطباعة لاحقاً.
    - عند الإلغاء: لا يتغير شيء ويبقى آخر DEVMODE محفوظاً كما هو.
    يعيد كائن PyDEVMODEW جاهزاً للحقن أو None عند الإلغاء/الفشل.
    """
    try:
        import win32print
        key = (printer_name or "").strip()
        prev = _captured_devmodes.get(key)
        h = win32print.OpenPrinter(printer_name)
        try:
            if prev is not None:
                # عرض آخر تعديلات المستخدم وإعادتها كمدخل + إغلاق النافذة بالنتيجة
                dm = prev
                f_mode = 4 | 8 | 1  # DM_PROMPT | DM_IN_BUFFER | DM_OUT_BUFFER
            else:
                # أول فتحة: ابدأ من إعدادات الطابعة الحالية
                dm = win32print.GetPrinter(h, 2)['pDevMode']
                f_mode = 4 | 1      # DM_PROMPT | DM_OUT_BUFFER
            result = win32print.DocumentProperties(parent_hwnd, h, printer_name,
                                                   dm, dm, f_mode)
            if result < 0:
                return None
            _captured_devmodes[key] = dm
            logger.info("DEVMODE مُعدَّل محفوظ لـ %s", printer_name)
            return dm
        finally:
            win32print.ClosePrinter(h)
    except Exception as e:
        logger.warning("capture_printer_preferences فشل: %s", e)
        return None


def get_captured_devmode(printer_name):
    return _captured_devmodes.get((printer_name or "").strip())


def clear_captured_devmode(printer_name):
    _captured_devmodes.pop((printer_name or "").strip(), None)


def _inject_devmode_to_spooler(printer_name, devmode):
    """حقن DEVMODE مؤقتاً كافتراضي لصف الانتظار (SetPrinter level 2 pDevMode)
    قبل بدء الجلسة — كما يُطلب عند تمريره في pDefaults/StartDocPrinter. تُعاد
    القيمة السابقة للاستعادة لاحقاً."""
    import win32print
    restore = None
    try:
        h = win32print.OpenPrinter(printer_name)
        try:
            info = win32print.GetPrinter(h, 2)
            restore = info.get('pDevMode')
            info['pDevMode'] = devmode
            win32print.SetPrinter(h, 2, info, 0)
            logger.info("DEVMODE المُعدَّل حُقن في صف انتظار %s", printer_name)
        finally:
            win32print.ClosePrinter(h)
    except Exception as e:
        logger.warning("حقن DEVMODE في صف الانتظار فشل: %s", e)
    return restore


def _restore_printer_devmode(printer_name, devmode):
    if devmode is None:
        return
    try:
        import win32print
        h = win32print.OpenPrinter(printer_name)
        try:
            info = win32print.GetPrinter(h, 2)
            info['pDevMode'] = devmode
            win32print.SetPrinter(h, 2, info, 0)
        finally:
            win32print.ClosePrinter(h)
    except Exception as e:
        logger.warning("استعادة DEVMODE فشل: %s", e)


def _apply_captured_devmode_to_printer(printer, devmode):
    """ترجمة DEVMODE المُلتقَط إلى إعدادات QPrinter (ورق/وجهين/لون/جودة/اتجاه)
    لتُطبق التعديلات فعلياً على صفقة الطباعة."""
    try:
        w_mm = float(getattr(devmode, 'PaperWidth', 0)) / 10.0
        h_mm = float(getattr(devmode, 'PaperLength', 0)) / 10.0
        orient = getattr(devmode, 'Orientation', DMORIENT_PORTRAIT)
        if w_mm > 1 and h_mm > 1:
            if orient == DMORIENT_LANDSCAPE and w_mm < h_mm:
                w_mm, h_mm = h_mm, w_mm
            printer.setPageSize(QPageSize(QSizeF(w_mm, h_mm), QPageSize.Millimeter))
        dup = getattr(devmode, 'Duplex', DMDUP_SIMPLEX)
        if dup == DMDUP_VERTICAL:
            printer.setDuplex(QPrinter.DuplexMode.DuplexLongSide)
        elif dup == DMDUP_HORIZONTAL:
            printer.setDuplex(QPrinter.DuplexMode.DuplexShortSide)
        else:
            printer.setDuplex(QPrinter.DuplexMode.DuplexNone)
        color = getattr(devmode, 'Color', DMCOLOR_COLOR)
        printer.setColorMode(QPrinter.ColorMode.Color if color == DMCOLOR_COLOR
                             else QPrinter.ColorMode.GrayScale)
        copies = int(getattr(devmode, 'Copies', 1) or 1)
        printer.setCopyCount(max(1, copies))
        q = getattr(devmode, 'PrintQuality', 0) or getattr(devmode, 'YResolution', 0)
        if q and q > 0:
            printer.setResolution(int(q))
        # درج الورق (المقصى الافتراضي) إن اختاره المستخدم في خصائص الطابعة
        src = getattr(devmode, 'DefaultSource', 0)
        if src and hasattr(printer, 'setPaperSource'):
            try:
                printer.setPaperSource(int(src))
            except Exception:
                pass
        if orient == DMORIENT_LANDSCAPE:
            printer.setPageOrientation(QPageLayout.Landscape)
        logger.info("طُبقت تفضيلات DEVMODE على الطابعة: %s", printer.printerName())
        return True
    except Exception as e:
        logger.warning("تطبيق DEVMODE على QPrinter فشل: %s", e)
        return False


def get_selected_printer_name():
    global _selected_printer_name
    if _selected_printer_name is None:
        _selected_printer_name = _settings.value("default_printer", None)
    return _selected_printer_name


def set_printer_name(name):
    global _selected_printer_name
    _selected_printer_name = name
    _settings.setValue("default_printer", name)
    logger.info("تم تعيين الطابعة الافتراضية: %s", name)


def select_printer(parent):
    global _selected_printer_name
    printers = QPrinterInfo.availablePrinters()
    if not printers:
        QMessageBox.warning(parent, "تنبيه", "لم يتم العثور على طابعات متصلة بالجهاز.")
        logger.warning("محاولة طباعة بدون طابعات متصلة")
        return False
    names = [p.printerName() for p in printers]
    logger.info("الطابعات المتصلة: %s", names)
    name, ok = QInputDialog.getItem(parent, "اختيار الطابعة", "الطابعات المتصلة:", names, 0, False)
    if not ok:
        logger.info("تم إلغاء اختيار الطابعة")
        return False
    _selected_printer_name = name
    _settings.setValue("default_printer", name)
    logger.info("تم اختيار الطابعة: %s", name)
    return True


def get_last_paper_type():
    global _last_paper_type
    v = _settings.value("last_paper_type", None)
    if v and v in PAPER_TYPES:
        _last_paper_type = v
    return _last_paper_type


def set_last_paper_type(name):
    global _last_paper_type
    if name in PAPER_TYPES:
        _last_paper_type = name
        _settings.setValue("last_paper_type", name)
        logger.info("تم تعيين نوع الورق: %s", name)


def _open_driver_properties_dialog(printer_name):
    """محاولة فتح نافذة خصائص برنامج تشغيل الطابعة عبر Winspool.

    تعمل مباشرة لأغلب السائقين (مثل Brother) حتى لو كانت الطابعة غير موصولة.
    تُرجع (نجاح، سبب الفشل أو None)."""
    import ctypes
    import ctypes.wintypes as wt
    try:
        winspool = ctypes.WinDLL('winspool.drv')
        winspool.OpenPrinterW.argtypes = [wt.LPCWSTR, ctypes.POINTER(wt.HANDLE), ctypes.c_void_p]
        winspool.OpenPrinterW.restype = wt.BOOL
        h_printer = wt.HANDLE()
        if not winspool.OpenPrinterW(printer_name, ctypes.byref(h_printer), None):
            return False, f"لم يُعثر على طابعة بهذا الاسم (خطأ {ctypes.get_last_error()})"
        try:
            winspool.ClosePrinter.argtypes = [wt.HANDLE]
            winspool.ClosePrinter.restype = wt.BOOL
            winspool.DocumentPropertiesW.argtypes = [
                wt.HWND, wt.HANDLE, wt.LPCWSTR,
                wt.LPBYTE, wt.LPBYTE, wt.DWORD]
            winspool.DocumentPropertiesW.restype = ctypes.c_long
            DM_IN_PROMPT = 4
            try:
                result = winspool.DocumentPropertiesW(
                    0, h_printer, printer_name, None, None, DM_IN_PROMPT)
            except Exception as e:
                logger.warning("DocumentPropertiesW فشل: %s", e)
                result = -1
            if result < 0:
                return False, "سائق الطابعة لا يعرض نافذة خصائص عبر Winspool."
            return True, None
        finally:
            winspool.ClosePrinter(h_printer)
    except Exception as e:
        logger.warning("فشل فتح خصائص السائق: %s", e)
        return False, str(e)


def _open_printui_properties(printer_name):
    """فتح نافذة تفضيلات الطباعة (Print Preferences / Document Properties) عبر printui.

    /e = تفضيلات تعريف الطابعة (إعدادات الورق والجودة والوجهين) — نفس النافذة
    التي تظهر في Word، وليست خصائص النظام (/p التي تتضمن المنافذ والأمان)."""
    try:
        subprocess.Popen(["rundll32.exe", "printui.dll,PrintUIEntry", "/e", "/n", printer_name])
        return True, None
    except Exception as e:
        logger.warning("printui فشل: %s", e)
        return False, str(e)


def show_printer_properties(parent, printer_name):
    """فتح نافذة تفضيلات أي طابعة مثبتة بالاسم المُعطى وحفظ النتيجة.

    1) محاولة أولى: win32print.DocumentProperties مع buffer داخل الكائن
       (نافذة تفضيلات Driver) — تعين DEVMODE المُعدَّل ويُحقن لاحقاً في دورة
       الطباعة عبر print_scene. تعمل حتى لو الطابعة غير موصولة حالياً.
    2) إن تعذّر: فتح نافذة التفضيلات عبر أداة النظام printui /e.
    تُرجع (نجاح، سبب الفشل أو None)."""
    name = (printer_name or "").strip()
    if not name:
        return False, "لم يُكتب اسم طابعة."
    hwnd = 0
    try:
        if parent is not None:
            hwnd = int(parent.winId())
    except Exception:
        hwnd = 0
    dm = capture_printer_preferences(name, hwnd)
    if dm is not None:
        return True, None
    if _open_printui_properties(name)[0]:
        return True, None
    return _open_driver_properties_dialog(name)


def _apply_paper_type(printer_name, paper_type_name):
    media_type = PAPER_TYPES.get(paper_type_name)
    if media_type is None:
        return
    try:
        winspool = ctypes.WinDLL('winspool.drv')
        h_printer = wt.HANDLE()
        if not winspool.OpenPrinterW(printer_name, ctypes.byref(h_printer), None):
            logger.warning("فشل فتح الطابعة لتطبيق نوع الورق")
            return
        try:
            dm_size = winspool.DocumentPropertiesW(0, h_printer, printer_name, None, None, 0)
            if dm_size <= 0:
                return
            dm = (ctypes.c_byte * dm_size)()
            if winspool.DocumentPropertiesW(0, h_printer, printer_name, dm, None, 2) < 0:
                return
            ctypes.memmove(ctypes.addressof(dm) + 148, ctypes.byref(ctypes.c_ulong(media_type)), 4)
            winspool.DocumentPropertiesW(0, h_printer, printer_name, dm, dm, 10)
            logger.info("dmMediaType=%d for %s", media_type, paper_type_name)
        finally:
            winspool.ClosePrinter(h_printer)
    except Exception as e:
        logger.warning("DEVMODE paper type failed: %s", e)


def print_scene(parent: QWidget, scenes, copies: int = 1, page_count: int = 1, duplex: bool = False,
                page_range=None, paper_type: str = None, devmode=None,
                paper_width_mm: int = None, paper_height_mm: int = None,
                page_stride_mm: float = None, back_offset_dx_mm: float = 0.0,
                back_offset_dy_mm: float = 0.0):
    if paper_width_mm and paper_height_mm:
        page_w_mm, page_h_mm = float(paper_width_mm), float(paper_height_mm)
    else:
        page_w_mm, page_h_mm = float(A4_WIDTH_MM), float(A4_HEIGHT_MM)
    if page_stride_mm is None:
        page_stride_mm = page_h_mm
    single_with_pages = isinstance(scenes, QGraphicsScene) and page_count > 1
    if single_with_pages:
        scene = scenes
        num_pages = page_count
    elif isinstance(scenes, QGraphicsScene):
        scene = scenes
        num_pages = 1
    else:
        scene = None
        num_pages = len(scenes)
    if page_range is not None:
        pfrom, pto = page_range
        pfrom = max(1, min(pfrom, num_pages))
        pto = max(pfrom, min(pto, num_pages))
        pages_to_print = list(range(pfrom, pto + 1))
    else:
        pages_to_print = list(range(1, num_pages + 1))
    global _selected_printer_name
    printers_list = QPrinterInfo.availablePrinters()
    if not printers_list:
        QMessageBox.warning(parent, "تنبيه", "لم يتم العثور على طابعات متصلة بالجهاز.")
        logger.warning("محاولة طباعة بدون طابعات متصلة")
        return
    selected = next((p for p in printers_list if p.printerName() == _selected_printer_name), None)
    if not selected:
        QMessageBox.warning(parent, "خطأ",
                            f"الطابعة '{_selected_printer_name}' غير متصلة حالياً. اختر طابعة أخرى.")
        return
    printer_name = selected.printerName()
    # تفضيلات DEVMODE المُلتقَطة (من نافذة خصائص الطابعة) إن وُجدت تطغى على
    # القيم الافتراضية — مع حقنها في صف الانتظار قبل بدء الجلسة واستعادتها بعدها.
    if devmode is None and os.name == 'nt':
        devmode = get_captured_devmode(printer_name)
    printer = QPrinter(selected)
    has_devmode = devmode is not None
    if has_devmode:
        _apply_captured_devmode_to_printer(printer, devmode)
        printer.setDuplex(QPrinter.DuplexMode.DuplexLongSide if duplex else QPrinter.DuplexMode.DuplexNone)
        if page_stride_mm is not None:
            # صفحات قسم وثائق A4 تُطبع دوماً بمقاس الورقة الفعلي للصفحة
            # (الاتجاه العرضي/العمودي) حتى لو التُقطت تفضيلات DEVMODE سابقاً
            # من نافذة خصائص الطابعة — وإلا طُبعت الورقة العرضية على ورق عمودي.
            printer.setPageSize(QPageSize(QSizeF(page_w_mm, page_h_mm), QPageSize.Millimeter))
            printer.setFullPage(False)
        if os.name == 'nt':
            injected_dm = _inject_devmode_to_spooler(printer_name, devmode)
    if not has_devmode:
        is_photo = _is_photo_paper(paper_type)
        if is_photo:
            printer.setResolution(600)
        else:
            printer.setResolution(300)
        printer.setPageSize(QPageSize(QSizeF(page_w_mm, page_h_mm), QPageSize.Millimeter))
        printer.setFullPage(False)
        printer.setCopyCount(max(1, int(copies)))
        printer.setDuplex(QPrinter.DuplexMode.DuplexLongSide if duplex else QPrinter.DuplexMode.DuplexNone)
    else:
        # عدد النسخ يُؤخذ من حوار الطباعة (يعكس رغبة المستخدم عند الضغط)،
        # بينما يبقى الورق/الجودة/اللون/الوجهين من DEVMODE.
        printer.setCopyCount(max(1, int(copies)))
    if paper_type:
        _apply_paper_type(printer.printerName(), paper_type)
    logger.info("طباعة %d نسخ من %d صفحة%s على: %s",
                copies, len(pages_to_print), " (وجهين)" if duplex else "", printer.printerName())
    painter = QPainter(printer)
    if not painter.isActive():
        logger.error("فشل بدء الرسم على الطابعة")
        return
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    print_scenes = [scene] if isinstance(scenes, QGraphicsScene) else [sc for sc in scenes]
    hidden_by_scene = {}
    for sc in print_scenes:
        if sc is None:
            continue
        try:
            sc._printing = True
        except Exception:
            pass
        hidden = []
        try:
            for item in sc.items():
                if item.data(NO_PRINT_KEY) and item.isVisible():
                    item.setVisible(False)
                    hidden.append(item)
        except Exception:
            pass
        hidden_by_scene[sc] = hidden
    try:
        dev_rect = printer.pageRect(QPrinter.DevicePixel)
        target = QRectF(0.0, 0.0, dev_rect.width(), dev_rect.height())
        for idx, page_idx in enumerate(pages_to_print):
            if idx > 0:
                printer.newPage()
            painter.save()
            back_shift = duplex and page_idx % 2 == 0 and (back_offset_dx_mm or back_offset_dy_mm)
            off_dx = -back_offset_dx_mm if back_shift else 0.0
            off_dy = -back_offset_dy_mm if back_shift else 0.0
            if single_with_pages:
                source = QRectF(off_dx, (page_idx - 1) * page_stride_mm + off_dy, page_w_mm, page_h_mm)
                scene.render(painter, target, source, Qt.KeepAspectRatio)
            elif isinstance(scenes, QGraphicsScene):
                sr = scene.sceneRect()
                if back_shift:
                    sr = QRectF(sr.x() + off_dx, sr.y() + off_dy, sr.width(), sr.height())
                scene.render(painter, target, sr, Qt.KeepAspectRatio)
            else:
                sc = scenes[page_idx - 1]
                sr = sc.sceneRect()
                if back_shift:
                    sr = QRectF(sr.x() + off_dx, sr.y() + off_dy, sr.width(), sr.height())
                sc.render(painter, target, sr, Qt.KeepAspectRatio)
            painter.restore()
        logger.info("تمت طباعة %d صفحة على: %s", len(pages_to_print), printer.printerName())
    except Exception as e:
        logger.error("فشل الطباعة", exc_info=True)
    finally:
        for sc, hidden in hidden_by_scene.items():
            try:
                sc._printing = False
            except Exception:
                pass
            for item in hidden:
                try:
                    item.setVisible(True)
                except Exception:
                    pass
        painter.end()
        if injected_dm is not None:
            _restore_printer_devmode(printer_name, injected_dm)
            logger.info("تمت استعادة إعدادات الطابعة الأصلية لـ %s", printer_name)
