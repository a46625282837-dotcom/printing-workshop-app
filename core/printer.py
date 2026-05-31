import logging
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
from PySide6.QtGui import QPainter, QPageSize
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QGraphicsScene, QWidget, QMessageBox, QInputDialog

logger = logging.getLogger(__name__)

A4_WIDTH_MM = 210
A4_HEIGHT_MM = 297

_selected_printer_name = None


def get_selected_printer_name():
    return _selected_printer_name


def set_printer_name(name):
    global _selected_printer_name
    _selected_printer_name = name
    logger.info("تم تعيين الطابعة: %s", name)


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
    logger.info("تم اختيار الطابعة: %s", name)
    return True


def print_scene(parent: QWidget, scenes, copies: int = 1, page_count: int = 1, duplex: bool = False,
                page_range=None):
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
        _selected_printer_name = None
        return
    printer = QPrinter(selected)
    printer.setPageSize(QPageSize(QPageSize.A4))
    printer.setFullPage(False)
    printer.setCopyCount(max(1, int(copies)))
    printer.setDuplex(QPrinter.DuplexMode.DuplexLongSide if duplex else QPrinter.DuplexMode.DuplexNone)
    logger.info("طباعة %d نسخ من %d صفحة%s على: %s",
                copies, len(pages_to_print), " (وجهين)" if duplex else "", printer.printerName())
    painter = QPainter(printer)
    if not painter.isActive():
        logger.error("فشل بدء الرسم على الطابعة")
        return
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
        painter.end()
