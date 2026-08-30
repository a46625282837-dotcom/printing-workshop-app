import logging
import ctypes
import ctypes.wintypes as wt
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
from PySide6.QtGui import QPainter, QPageSize
from PySide6.QtCore import QRectF, QSettings
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
                page_range=None, paper_type: str = None):
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
    printer = QPrinter(selected)
    is_photo = _is_photo_paper(paper_type)
    if is_photo:
        printer.setResolution(600)
    else:
        printer.setResolution(300)
    printer.setPageSize(QPageSize(QPageSize.A4))
    printer.setFullPage(False)
    printer.setCopyCount(max(1, int(copies)))
    printer.setDuplex(QPrinter.DuplexMode.DuplexLongSide if duplex else QPrinter.DuplexMode.DuplexNone)
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
        page_rect = printer.pageRect(QPrinter.Millimeter)
        scale_x = page_rect.width() / A4_WIDTH_MM
        scale_y = page_rect.height() / A4_HEIGHT_MM
        scale = min(scale_x, scale_y)
        for idx, page_idx in enumerate(pages_to_print):
            if idx > 0:
                printer.newPage()
            painter.save()
            painter.scale(scale, scale)
            if single_with_pages:
                source = QRectF(0, (page_idx - 1) * A4_HEIGHT_MM, A4_WIDTH_MM, A4_HEIGHT_MM)
                scene.render(painter, QRectF(), source)
            elif isinstance(scenes, QGraphicsScene):
                scene.render(painter)
            else:
                scenes[page_idx - 1].render(painter)
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
