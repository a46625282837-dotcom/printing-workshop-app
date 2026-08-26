import logging



import io



import os



from concurrent.futures import ThreadPoolExecutor, as_completed



from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QVBoxLayout,



                               QPushButton, QWidget, QHBoxLayout, QFileDialog,



                               QMessageBox, QDialog, QComboBox, QDialogButtonBox,



                               QLabel, QSpinBox, QAbstractSpinBox, QCheckBox,



                                QInputDialog, QMenu, QScrollArea)



from PySide6.QtCore import Qt, Signal, QSettings, QThread, QTimer, QRectF



from PySide6.QtGui import QPixmap, QPen, QColor, QBrush, QPainter, QShortcut, QKeySequence, QTransform



from PySide6.QtGui import QPageSize



from PySide6.QtPrintSupport import QPrinter, QPrinterInfo



from PIL import Image

import numpy as np
import cv2

from core.printer import print_scene, get_selected_printer_name, set_printer_name, PAPER_TYPES, PAPER_TYPE_NAMES, get_last_paper_type, set_last_paper_type



from ui.id_card_item import IDCardItem, CARD_W, CARD_H







logger = logging.getLogger(__name__)







A4_W = 210



A4_H = 297



MARGIN = 5



CARD_GAP = 10



CARDS_PER_ROW = 2



MAX_ROWS = (A4_H - 2 * MARGIN + CARD_GAP) // (CARD_H + CARD_GAP)



MAX_CARDS = MAX_ROWS * CARDS_PER_ROW











class A4GraphicsView(QGraphicsView):



    image_dropped = Signal(list)







    def __init__(self, scene, parent=None):



        super().__init__(scene, parent)



        self.setAcceptDrops(True)



        self.setRenderHint(QPainter.RenderHint.Antialiasing)



        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)



        self.setDragMode(QGraphicsView.RubberBandDrag)



        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)







    def dragEnterEvent(self, event):



        if event.mimeData().hasUrls():



            event.acceptProposedAction()







    def dragMoveEvent(self, event):



        if event.mimeData().hasUrls():



            event.acceptProposedAction()







    def dropEvent(self, event):



        paths = []



        for url in event.mimeData().urls():



            path = url.toLocalFile()



            ext = path.lower().rsplit('.', 1)[-1] if '.' in path else ''



            if ext in ('png', 'jpg', 'jpeg', 'bmp', 'tiff'):



                paths.append(path)



        if paths:



            self.image_dropped.emit(paths)



        event.acceptProposedAction()







    def contextMenuEvent(self, event):



        item = self.itemAt(event.pos())



        if not item or not isinstance(item, IDCardItem):



            super().contextMenuEvent(event)



            return



        menu = QMenu()



        act_del = menu.addAction("حذف")



        act_rot = menu.addAction("تدوير 90°")



        act_dup = menu.addAction("تكرار...")



        menu.addSeparator()



        act_zin = menu.addAction("تكبير")



        act_zout = menu.addAction("تصغير")



        act_zres = menu.addAction("حجم أصلي")



        self.scene().clearSelection()



        item.setSelected(True)



        action = menu.exec(event.globalPos())



        if action == act_del:



            self.parent()._delete_selected()



        elif action == act_rot:



            item.set_item_rotation((item.item_rotation() + 90) % 360)



        elif action == act_dup:



            count, ok = QInputDialog.getInt(self, "تكرار البطاقة", "عدد مرات التكرار:", 2, 1, 999)



            if ok:



                for _ in range(count):



                    self.parent()._place_card(QPixmap(item.pixmap()))



        elif action == act_zin:



            item.set_item_scale(item.item_scale() * 1.05)



        elif action == act_zout:



            item.set_item_scale(item.item_scale() / 1.05)



        elif action == act_zres:



            item.set_item_scale(1.0)











class PrintSetupDialog(QDialog):



    def __init__(self, parent=None, page_count=1, default_paper_type=None):



        super().__init__(parent)



        self.setWindowTitle("إعداداطھ اظ„طباعة")



        self.setLayoutDirection(Qt.RightToLeft)



        self._page_count = page_count



        layout = QVBoxLayout(self)







        printer_row = QHBoxLayout()



        printer_row.addWidget(QLabel("الطابعة:"))



        self.printer_combo = QComboBox()



        for p in QPrinterInfo.availablePrinters():



            self.printer_combo.addItem(p.printerName())



        current = get_selected_printer_name()



        if current:



            idx = self.printer_combo.findText(current)



            if idx >= 0:



                self.printer_combo.setCurrentIndex(idx)



        printer_row.addWidget(self.printer_combo)



        layout.addLayout(printer_row)

        paper_row = QHBoxLayout()

        paper_row.addWidget(QLabel("نوع الورق:"))

        self.paper_combo = QComboBox()

        self.paper_combo.addItems(PAPER_TYPE_NAMES)

        default_pt = default_paper_type or get_last_paper_type()

        if default_pt and default_pt in PAPER_TYPE_NAMES:

            self.paper_combo.setCurrentText(default_pt)

        paper_row.addWidget(self.paper_combo)

        layout.addLayout(paper_row)







        copies_row = QHBoxLayout()



        copies_row.addWidget(QLabel("عدد النسخ:"))



        self.copies_spin = QSpinBox()



        self.copies_spin.setRange(1, 99)



        self.copies_spin.setValue(1)



        self.copies_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)



        copies_row.addWidget(self.copies_spin)



        layout.addLayout(copies_row)







        self.duplex_cb = QCheckBox("طباعة على وجهين (Duplex)")



        layout.addWidget(self.duplex_cb)







        # Page range



        range_group = QHBoxLayout()



        self.all_pages_cb = QCheckBox("طباعة الكل")



        self.all_pages_cb.setChecked(True)



        self.all_pages_cb.toggled.connect(self._on_all_pages_toggled)



        range_group.addWidget(self.all_pages_cb)



        range_group.addWidget(QLabel("من:"))



        self.from_spin = QSpinBox()



        self.from_spin.setRange(1, page_count)



        self.from_spin.setValue(1)



        self.from_spin.setEnabled(False)



        range_group.addWidget(self.from_spin)



        range_group.addWidget(QLabel("إلى:"))



        self.to_spin = QSpinBox()



        self.to_spin.setRange(1, page_count)



        self.to_spin.setValue(page_count)



        self.to_spin.setEnabled(False)



        range_group.addWidget(self.to_spin)



        layout.addLayout(range_group)







        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)



        buttons.button(QDialogButtonBox.Ok).setText("طباعة")



        buttons.button(QDialogButtonBox.Cancel).setText("إلغاء")



        buttons.accepted.connect(self.accept)



        buttons.rejected.connect(self.reject)



        layout.addWidget(buttons)







    def _on_all_pages_toggled(self, checked):



        self.from_spin.setEnabled(not checked)



        self.to_spin.setEnabled(not checked)







    def selected_printer(self):



        return self.printer_combo.currentText()







    def copies(self):



        return self.copies_spin.value()







    def duplex(self):



        return self.duplex_cb.isChecked()







    def page_range(self):

        if self.all_pages_cb.isChecked():

            return None

        return (self.from_spin.value(), self.to_spin.value())

    def paper_type(self):

        return self.paper_combo.currentText()











class CardProcessingThread(QThread):



    card_ready = Signal(bytes)

    original_ready = Signal(bytes)



    processing_done = Signal()



    _SENTINEL = object()







    def __init__(self, paths_or_bytes, no_crop=False):



        super().__init__()



        self._items = paths_or_bytes



        self._no_crop = no_crop







    def run(self):



        try:



            n = len(self._items)







            def process_one(item):



                import numpy as np



                from core.id_extractor import extract_card



                from core.image_utils import resize_to_card



                from core.photo_processor import auto_crop_subject



                if isinstance(item, str):



                    pil = Image.open(item)



                else:



                    pil = Image.open(io.BytesIO(item))



                orig_buf = io.BytesIO()
                try:
                    pil.convert("RGB").save(orig_buf, format="PNG")
                    original_bytes = orig_buf.getvalue()
                except Exception:
                    original_bytes = b""



                if self._no_crop:



                    pil = pil.convert("RGB")



                    extracted = pil



                elif pil.mode == "RGBA":



                    alpha = np.array(pil.split()[-1])



                    transparent_ratio = np.sum(alpha < 128) / alpha.size



                    if transparent_ratio > 0.05:



                        pil = pil.convert("RGB")



                        extracted = pil



                    else:



                        pil = pil.convert("RGB")



                        extracted = extract_card(pil)



                        extracted = auto_crop_subject(extracted)



                else:



                    pil = pil.convert("RGB")



                    extracted = extract_card(pil)



                    extracted = auto_crop_subject(extracted)



                resized = resize_to_card(extracted)



                buf = io.BytesIO()



                resized.save(buf, format="PNG")



                return buf.getvalue(), original_bytes







            workers = min(4, os.cpu_count() or 4, n)



            results = [None] * n



            next_emit = 0



            with ThreadPoolExecutor(max_workers=workers) as pool:



                futs = {pool.submit(process_one, item): i for i, item in enumerate(self._items)}



                for fut in as_completed(futs):



                    idx = futs[fut]



                    try:



                        data = fut.result()



                    except Exception as e:



                        logger.error("ظظ¾شظâ€‍ ظâ€¦عاظâ€‍جة اظâ€‍صظث†رة %d/%d: %s", idx + 1, n, e)



                        data = self._SENTINEL



                    results[idx] = data



                    while next_emit < n and results[next_emit] is not None:



                        val = results[next_emit]



                        results[next_emit] = None



                        next_emit += 1



                        if val is not self._SENTINEL:



                            self.card_ready.emit(val[0])
                            self.original_ready.emit(val[1])



        except Exception as e:



            logger.critical("خطأ طط›ظظ¹ر ظâ€¦طع¾ظث†ظâ€ڑع ظظ¾ظظ¹ خظظ¹ط اظâ€‍ظâ€¦عاظâ€‍جة", exc_info=True)



        finally:



            self.processing_done.emit()











class LoadingSpinner(QWidget):



    def __init__(self, parent=None):



        super().__init__(parent)



        self._angle = 0



        self._timer = QTimer(self)



        self._timer.timeout.connect(self._tick)



        self._timer.setInterval(50)



        self.setFixedSize(40, 40)



        self.setAttribute(Qt.WA_TransparentForMouseEvents)



        self.hide()







    def _tick(self):



        self._angle = (self._angle + 30) % 360



        self.update()







    def showEvent(self, event):



        self._angle = 0



        self._timer.start()



        super().showEvent(event)







    def hideEvent(self, event):



        self._timer.stop()



        super().hideEvent(event)







    def paintEvent(self, event):



        try:



            p = QPainter(self)



            p.setRenderHint(QPainter.Antialiasing)



            p.translate(20, 20)



            p.rotate(self._angle)



            pen = QPen(QColor(0, 120, 215), 4, Qt.SolidLine, Qt.RoundCap)



            p.setPen(pen)



            p.drawArc(QRectF(-14, -14, 28, 28), 0, 270 * 16)



        except RuntimeError:



            pass











class _CropOverlay(QWidget):
    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self._original_pixmap = pixmap
        self._points = []
        self._max_points = 4
        self._scale = 1.0
        self._offset_x = 0
        self._offset_y = 0
        self._drawing_pixmap = None
        self.setMinimumSize(200, 200)
        self.setCursor(Qt.CrossCursor)

    def set_display(self, pixmap, scale, offset_x, offset_y):
        self._drawing_pixmap = pixmap
        self._scale = scale
        self._offset_x = offset_x
        self._offset_y = offset_y
        self.update()

    def clear_points(self):
        self._points.clear()
        self.update()

    def get_original_points(self):
        result = []
        for pt in self._points:
            ox = (pt.x() - self._offset_x) / self._scale
            oy = (pt.y() - self._offset_y) / self._scale
            result.append((ox, oy))
        return result

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and len(self._points) < self._max_points:
            from PySide6.QtCore import QPointF
            self._points.append(QPointF(event.position().x(), event.position().y()))
            self.update()
            event.accept()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._drawing_pixmap and not self._drawing_pixmap.isNull():
            scaled = self._drawing_pixmap.scaled(
                self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            sx = self.width() - scaled.width()
            sy = self.height() - scaled.height()
            self._offset_x = sx // 2
            self._offset_y = sy // 2
            self._scale = scaled.width() / self._drawing_pixmap.width() if self._drawing_pixmap.width() else 1.0
            p.drawPixmap(self._offset_x, self._offset_y, scaled)
        else:
            p.fillRect(self.rect(), QColor("#1a1a1a"))
            p.setPen(QColor("#888"))
            p.drawText(self.rect(), Qt.AlignCenter, "جاري التحميل...")
        if len(self._points) >= 2:
            pen = QPen(QColor("#00ff00"))
            pen.setWidth(2)
            p.setPen(pen)
            for i in range(1, len(self._points)):
                p.drawLine(self._points[i - 1], self._points[i])
            if len(self._points) == 4:
                p.drawLine(self._points[3], self._points[0])
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#00ff00"))
        for pt in self._points:
            p.drawEllipse(pt, 6, 6)
        p.end()


class A4Editor(QWidget):



    go_back = Signal()







    def __init__(self, parent=None):



        super().__init__(parent)



        self.setWindowTitle("ظ…حرر بطاظ‚اطھ اظ„ظ‡ظˆظٹة - A4")



        self.resize(900, 700)



        self.setLayoutDirection(Qt.RightToLeft)



        self.cards = []



        self._num_pages = 1



        self._copied_pixmap = None



        self._copied_rotation = 0



        self._processing_thread = None



        self._build_ui()



        self._add_shortcuts()







    def _build_ui(self):



        layout = QVBoxLayout(self)







        btn_bar = QHBoxLayout()



        btn_back = QPushButton("↩ رجوع")



        btn_back.setToolTip("العودة إلى الشاشة الرئيسية")



        btn_back.clicked.connect(self.go_back.emit)



        btn_add = QPushButton("+ إضافة صورة")



        btn_add.clicked.connect(self.add_image_dialog)



        btn_print = QPushButton("طباعة")



        btn_print.clicked.connect(self.print_page)



        btn_save_pdf = QPushButton("💾 حفظ PDF")



        btn_save_pdf.clicked.connect(self.save_pdf)



        btn_clear = QPushButton("تفريغ الكل")



        btn_clear.clicked.connect(self.clear_all)



        self.btn_nocrop = QPushButton("بدون قص")



        self.btn_nocrop.setCheckable(True)



        self.btn_nocrop.setToolTip("إذا كان مضغوطاً، تُضاف الصور بدون قص")



        self.btn_nocrop.toggled.connect(



            lambda checked: self.btn_nocrop.setStyleSheet(



                "background-color: #0078d4; color: white;" if checked else ""))



        btn_zoom_in = QPushButton("🔍+")



        btn_zoom_in.setToolTip("تكبير")



        btn_zoom_in.clicked.connect(lambda: self._zoom_selected(1.05))



        btn_zoom_out = QPushButton("🔍-")



        btn_zoom_out.setToolTip("تصغير")



        btn_zoom_out.clicked.connect(lambda: self._zoom_selected(1 / 1.05))



        btn_zoom_fit = QPushButton("⌖")



        btn_zoom_fit.setToolTip("ملاءمة الشاشة")



        btn_zoom_fit.clicked.connect(lambda: self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio))



        btn_add_page = QPushButton("➕ إضافة صفحة")



        btn_add_page.setToolTip("أضف صفحة A4 فارغة جديدة")



        btn_add_page.clicked.connect(self._add_blank_page)



        btn_del_page = QPushButton("➖ حذف صفحة")



        btn_del_page.setToolTip("احذف آخر صفحة فارغة")



        btn_del_page.clicked.connect(self._delete_last_page)



        btn_bar.addWidget(btn_back)



        btn_bar.addWidget(btn_add)



        btn_bar.addWidget(btn_print)



        btn_bar.addWidget(btn_save_pdf)



        btn_bar.addWidget(self.btn_nocrop)



        btn_bar.addWidget(btn_clear)



        btn_bar.addStretch()



        btn_bar.addWidget(btn_del_page)



        btn_bar.addWidget(btn_add_page)



        btn_bar.addWidget(btn_zoom_fit)



        btn_bar.addWidget(btn_zoom_out)



        btn_bar.addWidget(btn_zoom_in)



        layout.addLayout(btn_bar)







        self.scene = QGraphicsScene(self)



        self.scene.setSceneRect(0, 0, A4_W, A4_H)



        self._draw_page_area(0)



        self.view = A4GraphicsView(self.scene, self)



        self.view.image_dropped.connect(self._on_drop_batch)



        layout.addWidget(self.view)







        self._spinner = LoadingSpinner(self.view)



        self._spinner.move(



            (self.view.width() - self._spinner.width()) // 2,



            (self.view.height() - self._spinner.height()) // 2



        )







        self._apply_default_zoom()







    def resizeEvent(self, event):



        super().resizeEvent(event)



        if hasattr(self, '_spinner') and self.view:



            self._spinner.move(



                (self.view.width() - self._spinner.width()) // 2,



                (self.view.height() - self._spinner.height()) // 2



            )







    def closeEvent(self, event):



        if hasattr(self, '_processing_thread') and self._processing_thread and self._processing_thread.isRunning():



            self._processing_thread.quit()



            self._processing_thread.wait(2000)



        super().closeEvent(event)







    def _draw_page_area(self, page_index):



        y0 = page_index * A4_H



        self.scene.addRect(0, y0, A4_W, A4_H, QPen(Qt.black), QBrush(Qt.white)).setZValue(-1)



        pen = QPen(QColor(200, 200, 200, 60), 0.3, Qt.DashLine)



        x = MARGIN



        for col in range(CARDS_PER_ROW):



            y = y0 + MARGIN



            while y + CARD_H <= y0 + A4_H - MARGIN:



                self.scene.addRect(x, y, CARD_W, CARD_H, pen)



                y += CARD_H + CARD_GAP



            x += CARD_W + CARD_GAP







    def _ensure_page(self, page_index):



        while page_index >= self._num_pages:



            self._draw_page_area(self._num_pages)



            self._num_pages += 1



            h = self._num_pages * A4_H



            self.scene.setSceneRect(0, 0, A4_W, h)



        return page_index * A4_H







    def _apply_default_zoom(self):



        settings = QSettings("ورقة طباعة", "A4Editor")



        zoom = settings.value("defaultZoom", 2.2, type=float)



        self.view.scale(zoom, zoom)



        logger.info("طع¾ظâ€¦ طع¾طبظظ¹ظâ€ڑ اظâ€‍تكبير اظâ€‍اظظ¾طع¾راضظظ¹: %.2f", zoom)







    def _zoom_selected(self, factor):



        old = self.view.transformationAnchor()



        self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)



        items = self.scene.selectedItems()



        if items:



            rect = items[0].sceneBoundingRect()



            for item in items[1:]:



                rect = rect.united(item.sceneBoundingRect())



            self.view.centerOn(rect.center())



        self.view.scale(factor, factor)



        self.view.setTransformationAnchor(old)







    def _add_shortcuts(self):



        QShortcut(QKeySequence("Delete"), self, self._delete_selected)



        QShortcut(QKeySequence("Backspace"), self, self._delete_selected)



        QShortcut(QKeySequence("Ctrl+C"), self, self._copy_selected)



        QShortcut(QKeySequence("Ctrl+V"), self, self._paste_copied)



        QShortcut(QKeySequence("Ctrl+R"), self, self._rotate_selected)



        QShortcut(QKeySequence("Ctrl+P"), self, self.print_page)



        QShortcut(QKeySequence("Ctrl+D"), self, self._duplicate_selected)







    def _delete_selected(self):



        for item in list(self.cards):



            try:



                if item.isSelected():



                    self.scene.removeItem(item)



                    self.cards.remove(item)



                    logger.info("طع¾ظâ€¦ حذظظ¾ اظâ€‍بطاظâ€ڑة")



            except RuntimeError:



                pass







    def _copy_selected(self):



        items = self.scene.selectedItems()



        for item in items:



            if isinstance(item, IDCardItem):



                self._copied_pixmap = item.pixmap()



                self._copied_rotation = item.item_rotation()



                logger.info("طع¾ظâ€¦ ظâ€ سخ اظâ€‍بطاظâ€ڑة (طع¾دظث†ظظ¹ر %.0fآآ°)", self._copied_rotation)



                return







    def _paste_copied(self):



        if self._copied_pixmap is None or self._copied_pixmap.isNull():



            return



        self._place_card(QPixmap(self._copied_pixmap))



        new = self.cards[-1]



        if self._copied_rotation:



            new.set_item_rotation(self._copied_rotation)



        self.scene.clearSelection()



        new.setSelected(True)



        self.view.centerOn(new)



        logger.info("طع¾ظâ€¦ ظâ€‍صظâ€ڑ اظâ€‍بطاظâ€ڑة")







    def _duplicate_selected(self):



        items = self.scene.selectedItems()



        for item in items:



            if isinstance(item, IDCardItem):



                count, ok = QInputDialog.getInt(self, "تكرار البطاقة", "عدد مرات التكرار:", 2, 1, 999)



                if ok:



                    for _ in range(count):



                        self._place_card(QPixmap(item.pixmap()))



                return







    def _rotate_selected(self):



        items = self.scene.selectedItems()



        for item in items:



            if isinstance(item, IDCardItem):



                item.set_item_rotation((item.item_rotation() + 90) % 360)



                logger.info("طع¾ظâ€¦ طع¾دظث†ظظ¹ر اظâ€‍بطاظâ€ڑة إظâ€‍ظâ€° %.0fآآ°", item.item_rotation())







    def _add_blank_page(self):



        self._ensure_page(self._num_pages)



        logger.info("طع¾ظâ€¦طع¾ إضاظظ¾ة صظظ¾حة ظظ¾ارطط›ة %d", self._num_pages)







    def _delete_last_page(self):



        if self._num_pages <= 1:



            QMessageBox.information(self, "تنبيه", "لا توجد صور للحذف.")



            return



        last_page = self._num_pages - 1



        for i, c in enumerate(self.cards):



            if i // MAX_CARDS == last_page:



                QMessageBox.warning(self, "تنبيه",



                    "لا توجد صور للحذف في هذه الصفحة أو انها تحتوي على بطاقات..")



                return



        saved = [(c.pixmap(), c.item_rotation()) for c in self.cards]



        self.scene.clear()



        self.cards.clear()



        self._num_pages -= 1



        h = self._num_pages * A4_H



        self.scene.setSceneRect(0, 0, A4_W, h)



        for p in range(self._num_pages):



            self._draw_page_area(p)



        for pixmap, rotation in saved:



            self._place_card(QPixmap(pixmap))



            if rotation:



                self.cards[-1].set_item_rotation(rotation)



        logger.info("طع¾ظâ€¦ حذظظ¾ اظâ€‍صظظ¾حة %d", last_page + 1)







    def _ask_crop_choice(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("اختيار طريقة الإضافة")
        dlg.setFixedSize(350, 160)
        dlg.setLayoutDirection(Qt.RightToLeft)
        dlg.setStyleSheet("QDialog{background:#f5f5f5;}")
        lay = QVBoxLayout(dlg)
        lay.setSpacing(15)
        lay.setContentsMargins(20, 20, 20, 20)
        lbl = QLabel("هل تريد قص الهويات قبل الإضافة؟")
        lbl.setStyleSheet("font-size:14px;font-weight:bold;color:#000000;")
        lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(lbl)
        sub = QLabel("إذا كانت الهويات مقصوصة اختر بدون قص")
        sub.setStyleSheet("font-size:12px;color:#555555;")
        sub.setAlignment(Qt.AlignCenter)
        lay.addWidget(sub)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(15)
        btn_crop = QPushButton("✂ قص")
        btn_crop.setFixedHeight(40)
        btn_crop.setStyleSheet(
            "QPushButton{background:#0078d4;color:white;font-size:13px;font-weight:bold;border:none;border-radius:6px;padding:0 20px}"
            "QPushButton:hover{background:#005fa3}"
        )
        btn_nocrop = QPushButton("📄 بدون قص")
        btn_nocrop.setFixedHeight(40)
        btn_nocrop.setStyleSheet(
            "QPushButton{background:#4caf50;color:white;font-size:13px;font-weight:bold;border:none;border-radius:6px;padding:0 20px}"
            "QPushButton:hover{background:#388e3c}"
        )
        result = {"no_crop": None}
        def on_crop():
            result["no_crop"] = False
            dlg.accept()
        def on_nocrop():
            result["no_crop"] = True
            dlg.accept()
        btn_crop.clicked.connect(on_crop)
        btn_nocrop.clicked.connect(on_nocrop)
        btn_row.addWidget(btn_crop)
        btn_row.addWidget(btn_nocrop)
        lay.addLayout(btn_row)
        dlg.exec()
        return result["no_crop"]

    def _start_card_processing(self, paths, no_crop):
        if not paths:
            return
        thread = CardProcessingThread(paths, no_crop)
        thread.card_ready.connect(self._on_card_buffer_ready)
        thread.original_ready.connect(self._on_original_buffer_ready)
        thread.finished.connect(self._on_processing_done)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        self._processing_thread = thread
        self._spinner.show()

    def _on_drop_batch(self, paths):



        if not paths:



            return

        no_crop = self._ask_crop_choice()
        if no_crop is None:
            return
        self._start_card_processing(paths, no_crop)



        logger.info("بدأطع¾ ظâ€¦عاظâ€‍جة %d صظث†رة ظظ¾ظظ¹ اظâ€‍خظâ€‍ظظ¾ظظ¹ة", len(paths))







    def add_image_dialog(self):



        paths, _ = QFileDialog.getOpenFileNames(



            self, "اختر صورة الهوية", "",



            "Images (*.png *.jpg *.jpeg *.bmp *.tiff)")



        if not paths:



            return

        no_crop = self._ask_crop_choice()
        if no_crop is None:
            return
        self._start_card_processing(paths, no_crop)







    def _on_card_buffer_ready(self, buf):



        qpix = QPixmap()



        if qpix.loadFromData(buf):



            self._place_card(qpix)




    def _on_original_buffer_ready(self, buf):
        qpix = QPixmap()
        if qpix.loadFromData(buf) and self.cards:
            self.cards[-1]._original_pixmap = qpix

    def _show_original_image(self, card_item):
        if card_item._original_pixmap is None or card_item._original_pixmap.isNull():
            return
        original_pix = card_item._original_pixmap
        card_idx = card_item.index
        dlg = QDialog(self)
        dlg.setWindowTitle("قص يدوي")
        dlg.setMinimumSize(800, 600)
        dlg.setLayoutDirection(Qt.RightToLeft)
        dlg.setStyleSheet("QDialog{background:#2b2b2b;}")
        v_layout = QVBoxLayout(dlg)
        v_layout.setContentsMargins(10, 10, 10, 10)

        overlay = _CropOverlay(original_pix, dlg)
        preview_label = QLabel()
        preview_label.setAlignment(Qt.AlignCenter)
        preview_label.setStyleSheet("background:#1a1a1a;border-radius:8px;")
        preview_label.hide()
        v_layout.addWidget(overlay, 1)
        v_layout.addWidget(preview_label, 1)

        preview_pixmap = [None]
        preview_transforms = [0]
        preview_hflip = [False]

        def _do_crop(pts):
            cropped = self._perspective_crop(original_pix, pts)
            if cropped.isNull():
                return None
            return cropped

        def _apply_transforms(pix):
            p = pix
            if preview_hflip[0]:
                t = QTransform()
                t.scale(-1, 1)
                p = p.transformed(t, Qt.SmoothTransformation)
            if preview_transforms[0]:
                t = QTransform()
                t.rotate(preview_transforms[0])
                p = p.transformed(t, Qt.SmoothTransformation)
            return p

        def _show_preview(cropped):
            preview_pixmap[0] = cropped
            overlay.hide()
            preview_label.show()
            transformed = _apply_transforms(cropped)
            scaled = transformed.scaled(preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            preview_label.setPixmap(scaled)
            _update_buttons(True)

        def _show_overlay():
            preview_pixmap[0] = None
            preview_label.hide()
            overlay.show()
            overlay.clear_points()
            _update_buttons(False)

        btn_row = QHBoxLayout()
        btn_clear = QPushButton("مسح النقاط")
        btn_clear.setFixedHeight(36)
        btn_clear.setStyleSheet(
            "QPushButton{background:#e67e22;color:white;font-weight:bold;padding:0 14px;border:none;border-radius:4px;}"
            "QPushButton:hover{background:#f39c12;}"
        )
        btn_clear.clicked.connect(overlay.clear_points)
        btn_row.addWidget(btn_clear)

        btn_crop = QPushButton("قص")
        btn_crop.setFixedHeight(36)
        btn_crop.setStyleSheet(
            "QPushButton{background:#2196F3;color:white;font-weight:bold;padding:0 14px;border:none;border-radius:4px;}"
            "QPushButton:hover{background:#1976D2;}"
        )
        btn_row.addWidget(btn_crop)

        btn_hflip = QPushButton("↔ قلب أفقي")
        btn_hflip.setFixedHeight(36)
        btn_hflip.setStyleSheet(
            "QPushButton{background:#9C27B0;color:white;font-weight:bold;padding:0 14px;border:none;border-radius:4px;}"
            "QPushButton:hover{background:#7B1FA2;}"
        )
        btn_row.addWidget(btn_hflip)

        btn_vflip = QPushButton("↕ قلب عمودي")
        btn_vflip.setFixedHeight(36)
        btn_vflip.setStyleSheet(
            "QPushButton{background:#9C27B0;color:white;font-weight:bold;padding:0 14px;border:none;border-radius:4px;}"
            "QPushButton:hover{background:#7B1FA2;}"
        )
        btn_row.addWidget(btn_vflip)

        btn_rotate = QPushButton("↻ دوران")
        btn_rotate.setFixedHeight(36)
        btn_rotate.setStyleSheet(
            "QPushButton{background:#FF9800;color:white;font-weight:bold;padding:0 14px;border:none;border-radius:4px;}"
            "QPushButton:hover{background:#F57C00;}"
        )
        btn_row.addWidget(btn_rotate)

        btn_row.addStretch()

        btn_add = QPushButton("إضافة")
        btn_add.setFixedHeight(36)
        btn_add.setStyleSheet(
            "QPushButton{background:#4CAF50;color:white;font-weight:bold;padding:0 20px;border:none;border-radius:4px;}"
            "QPushButton:hover{background:#45a049;}"
        )
        btn_row.addWidget(btn_add)

        btn_close = QPushButton("إغلاق")
        btn_close.setFixedHeight(36)
        btn_close.setStyleSheet(
            "QPushButton{background:#555;color:white;border:none;"
            "border-radius:4px;padding:0 20px;font-weight:bold;}"
            "QPushButton:hover{background:#777;}"
        )
        btn_close.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_close)
        v_layout.addLayout(btn_row)

        def _update_buttons(in_preview):
            btn_clear.setEnabled(not in_preview)
            btn_crop.setEnabled(not in_preview)
            btn_hflip.setVisible(in_preview)
            btn_vflip.setVisible(in_preview)
            btn_rotate.setVisible(in_preview)
            btn_add.setEnabled(in_preview)

        def on_crop():
            try:
                pts = overlay.get_original_points()
                if len(pts) != 4:
                    from PySide6.QtWidgets import QMessageBox
                    QMessageBox.warning(dlg, "خطأ", "حدد 4 نقاط أولاً")
                    return
                cropped = _do_crop(pts)
                if cropped is None:
                    from PySide6.QtWidgets import QMessageBox
                    QMessageBox.warning(dlg, "خطأ", "فشل في القص")
                    return
                _show_preview(cropped)
            except Exception as e:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.critical(dlg, "خطأ", str(e))

        def on_hflip():
            preview_hflip[0] = not preview_hflip[0]
            if preview_pixmap[0]:
                transformed = _apply_transforms(preview_pixmap[0])
                scaled = transformed.scaled(preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                preview_label.setPixmap(scaled)

        def on_vflip():
            if preview_pixmap[0]:
                t = QTransform()
                t.scale(1, -1)
                flipped = preview_pixmap[0].transformed(t, Qt.SmoothTransformation)
                preview_pixmap[0] = flipped
                preview_hflip[0] = False
                preview_transforms[0] = 0
                transformed = _apply_transforms(preview_pixmap[0])
                scaled = transformed.scaled(preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                preview_label.setPixmap(scaled)

        def on_rotate():
            preview_transforms[0] = (preview_transforms[0] + 90) % 360
            if preview_pixmap[0]:
                transformed = _apply_transforms(preview_pixmap[0])
                scaled = transformed.scaled(preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                preview_label.setPixmap(scaled)

        def on_add():
            if preview_pixmap[0]:
                final = _apply_transforms(preview_pixmap[0])
                self._replace_card(card_idx, final)
                dlg.accept()

        btn_crop.clicked.connect(on_crop)
        btn_hflip.clicked.connect(on_hflip)
        btn_vflip.clicked.connect(on_vflip)
        btn_rotate.clicked.connect(on_rotate)
        btn_add.clicked.connect(on_add)

        _update_buttons(False)
        btn_hflip.hide()
        btn_vflip.hide()
        btn_rotate.hide()
        btn_add.setEnabled(False)

        def on_resize(event):
            if preview_pixmap[0]:
                transformed = _apply_transforms(preview_pixmap[0])
                scaled = transformed.scaled(preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                preview_label.setPixmap(scaled)
            elif not original_pix.isNull():
                overlay.set_display(original_pix, 1.0, 0, 0)
                scaled = original_pix.scaled(overlay.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                sx = overlay.width() - scaled.width()
                sy = overlay.height() - scaled.height()
                overlay.set_display(original_pix, scaled.width() / original_pix.width(), sx // 2, sy // 2)

        dlg.resizeEvent = on_resize
        QTimer.singleShot(0, lambda: on_resize(None))
        btn_close.setFocus()
        dlg.exec()

    def _perspective_crop(self, pixmap, points):
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        buf = QBuffer()
        buf.open(QIODevice.ReadWrite)
        pixmap.save(buf, "PNG")
        buf.seek(0)
        raw = buf.data().data()
        buf.close()
        arr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        h, w = arr.shape[:2]
        src = np.array(points, dtype=np.float32)
        s = src.sum(axis=1)
        d = np.diff(src, axis=1).flatten()
        ordered = np.array([
            src[np.argmin(s)],
            src[np.argmin(d)],
            src[np.argmax(s)],
            src[np.argmax(d)],
        ], dtype=np.float32)
        w1 = np.linalg.norm(ordered[0] - ordered[1])
        w2 = np.linalg.norm(ordered[2] - ordered[3])
        h1 = np.linalg.norm(ordered[0] - ordered[3])
        h2 = np.linalg.norm(ordered[1] - ordered[2])
        dst_w = int(max(w1, w2))
        dst_h = int(max(h1, h2))
        if dst_w < 10 or dst_h < 10:
            return pixmap
        dst = np.array([[0, 0], [dst_w - 1, 0], [dst_w - 1, dst_h - 1], [0, dst_h - 1]], dtype=np.float32)
        M = cv2.getPerspectiveTransform(ordered, dst)
        cropped_arr = cv2.warpPerspective(arr, M, (dst_w, dst_h))
        cropped_arr = cv2.cvtColor(cropped_arr, cv2.COLOR_BGR2RGB)
        ch = 3
        h2, w2 = cropped_arr.shape[:2]
        bytes_per_line = ch * w2
        from PySide6.QtGui import QImage as QImg
        qimg = QImg(cropped_arr.data.tobytes(), w2, h2, bytes_per_line, QImg.Format.Format_RGB888)
        return QPixmap.fromImage(qimg)

    def _replace_card(self, card_idx, new_pixmap):
        if card_idx < 0 or card_idx >= len(self.cards):
            return
        item = self.cards[card_idx]
        old_pos = item.pos()
        old_scale = item._scale
        old_rotation = item._rotation
        old_snapped = item._snapped
        old_pan_x = item._pan_x
        old_pan_y = item._pan_y
        old_original = item._original_pixmap
        self.scene.removeItem(item)
        new_item = IDCardItem(new_pixmap, index=card_idx)
        new_item.on_dropped = self._on_card_dropped
        new_item.on_double_clicked = self._show_original_image
        new_item._original_pixmap = old_original
        new_item.setPos(old_pos)
        new_item._rotation = old_rotation
        new_item._scale = old_scale
        new_item._snapped = old_snapped
        new_item._pan_x = old_pan_x
        new_item._pan_y = old_pan_y
        self.scene.addItem(new_item)
        self.cards[card_idx] = new_item







    def _on_processing_done(self):



        self._spinner.hide()



        logger.info("اظâ€ طع¾ظâ€،طع¾ ظâ€¦عاظâ€‍جة جظâ€¦ظظ¹ع اظâ€‍صظث†ر")







    def add_image(self, path_or_bytes):



        import numpy as np



        from core.id_extractor import extract_card



        from core.image_utils import resize_to_card



        from core.photo_processor import auto_crop_subject



        try:



            if isinstance(path_or_bytes, str):



                pil = Image.open(path_or_bytes)



            else:



                pil = Image.open(io.BytesIO(path_or_bytes))



            if self.btn_nocrop.isChecked():



                pil = pil.convert("RGB")



                extracted = pil



            elif pil.mode == "RGBA":



                alpha = np.array(pil.split()[-1])



                transparent_ratio = np.sum(alpha < 128) / alpha.size



                if transparent_ratio > 0.05:



                    pil = pil.convert("RGB")



                    extracted = pil



                else:



                    pil = pil.convert("RGB")



                    extracted = extract_card(pil)



                    extracted = auto_crop_subject(extracted)



            else:



                pil = pil.convert("RGB")



                extracted = extract_card(pil)



                extracted = auto_crop_subject(extracted)



            resized = resize_to_card(extracted)



            buf = io.BytesIO()



            resized.save(buf, format="PNG")



            buf.seek(0)



            qpix = QPixmap()



            if not qpix.loadFromData(buf.getvalue()):



                raise ValueError("ظظ¾شظâ€‍ طع¾حظâ€¦ظظ¹ظâ€‍ اظâ€‍صظث†رة اظâ€‍ظâ€¦عاظâ€‍جة")



            self._place_card(qpix)



        except Exception as e:



            logger.error("ظظ¾شظâ€‍ إضاظظ¾ة اظâ€‍صظث†رة", exc_info=True)



            QMessageBox.warning(self, "خطأ", f"فشل تحميل الصورة: {e}")







    def _grid_pos(self, idx):



        page_idx = idx // MAX_CARDS



        local_idx = idx % MAX_CARDS



        y_offset = page_idx * A4_H



        col = local_idx % CARDS_PER_ROW



        row = local_idx // CARDS_PER_ROW



        x = MARGIN + col * (CARD_W + CARD_GAP)



        y = y_offset + MARGIN + row * (CARD_H + CARD_GAP)



        return x, y







    def _place_card(self, pixmap: QPixmap):



        idx = len(self.cards)



        self._ensure_page(idx // MAX_CARDS)



        item = IDCardItem(pixmap, index=idx)



        item.on_dropped = self._on_card_dropped
        item.on_double_clicked = self._show_original_image



        item.setPos(*self._grid_pos(idx))



        item.set_item_scale(1.0, snap=True)



        self.scene.addItem(item)



        self.cards.append(item)



        logger.info("طع¾ظâ€¦طع¾ إضاظظ¾ة اظâ€‍بطاظâ€ڑة %d (صظظ¾حة %d)", len(self.cards), idx // MAX_CARDS + 1)







    def _on_card_dropped(self, item):



        center = item.sceneBoundingRect().center()



        best = None



        best_dist = float('inf')



        for other in self.cards:



            if other is item:



                continue



            if other.sceneBoundingRect().contains(center):



                d = (center - other.sceneBoundingRect().center()).manhattanLength()



                if d < best_dist:



                    best_dist = d



                    best = other



        if best is not None:



            self._swap_cards(item, best)


        else:

            self._snap_card_to_nearest(item, center)

    def _snap_card_to_nearest(self, item, center):

        drop_page = int(center.y() // A4_H)

        best_idx = 0

        best_dist = float('inf')

        total_slots = MAX_CARDS * self._num_pages

        for i in range(total_slots):

            gx, gy = self._grid_pos(i)

            card_page = int(gy // A4_H)

            if card_page != drop_page:

                continue

            dx = center.x() - (gx + CARD_W / 2)

            dy = center.y() - (gy + CARD_H / 2)

            d = dx * dx + dy * dy

            if d < best_dist:

                best_dist = d

                best_idx = i

        item.setPos(*self._grid_pos(best_idx))







    def _swap_cards(self, a, b):



        pos_a = a.pos()

        pos_b = b.pos()

        a.setPos(pos_b)

        b.setPos(pos_a)

        i = self.cards.index(a)

        j = self.cards.index(b)

        self.cards[i], self.cards[j] = self.cards[j], self.cards[i]



        logger.info("طع¾ظâ€¦ طع¾بدظظ¹ظâ€‍ اظâ€‍بطاظâ€ڑطع¾ظظ¹ظâ€  %d ظث† %d", i + 1, j + 1)







    def clear_all(self):



        self.scene.clear()



        self.cards.clear()



        self._num_pages = 1



        self.scene.setSceneRect(0, 0, A4_W, A4_H)



        self._draw_page_area(0)



        logger.info("طع¾ظâ€¦ طع¾ظظ¾رظظ¹طط› جظâ€¦ظظ¹ع اظâ€‍بطاظâ€ڑاطع¾")







    def print_page(self):



        if not self.cards:



            QMessageBox.information(self, "تنبيه", "لا توجد بطاقات للطباعة.")



            return



        if getattr(self, 'subscription_check', lambda: True)():



            dialog = PrintSetupDialog(self, page_count=self._num_pages, default_paper_type="ورق عادي")



            if dialog.exec() != QDialog.Accepted:



                return



            printer_name = dialog.selected_printer()



            copies = dialog.copies()



            duplex = dialog.duplex()



            if printer_name:



                set_printer_name(printer_name)



            logger.info("طظâ€‍ب طباعة %d ظâ€ سخ ظâ€¦ظâ€  %d صظظ¾حة%s عظâ€‍ظâ€°: %s",



                        copies, self._num_pages, " (ظث†جظâ€،ظظ¹ظâ€ )" if duplex else "", printer_name)



            selected = self.scene.selectedItems()



            for item in selected:



                item.setSelected(False)



            bg_items = [it for it in self.scene.items() if it.zValue() < 0]



            for it in bg_items:



                it.setVisible(False)
            pt = dialog.paper_type()
            if pt:
                set_last_paper_type(pt)
            print_scene(self, self.scene, copies=copies, page_count=self._num_pages, duplex=duplex,
                        page_range=dialog.page_range(), paper_type=pt)





            for it in bg_items:



                it.setVisible(True)



            for item in selected:



                item.setSelected(True)







    def save_pdf(self):



        if not self.cards:



            QMessageBox.information(self, "تنبيه", "لا توجد بطاقات للحفظ.")



            return



        from PySide6.QtWidgets import QFileDialog



        path, _ = QFileDialog.getSaveFileName(self, "حفظ PDF باسم", "", "ملفات PDF (*.pdf)")



        if not path:



            return



        if getattr(self, 'subscription_check', lambda: True)():



            selected = self.scene.selectedItems()



            for item in selected:



                item.setSelected(False)



            bg_items = [it for it in self.scene.items() if it.zValue() < 0]



            for it in bg_items:



                it.setVisible(False)



            printer = QPrinter()



            printer.setOutputFormat(QPrinter.PdfFormat)



            printer.setOutputFileName(path)



            printer.setPageSize(QPageSize(QPageSize.A4))



            printer.setFullPage(False)



            from PySide6.QtCore import QRectF



            painter = QPainter(printer)



            if not painter.isActive():



                logger.error("ظظ¾شظâ€‍ بدططŒ اظâ€‍رسظâ€¦ ظâ€‍حظظ¾ظ PDF")



                QMessageBox.warning(self, "خطأ", "فشل بدء رسم الحفظ PDF")



                return



            try:



                page_rect = printer.pageRect(QPrinter.Millimeter)



                scale_x = page_rect.width() / 210.0



                scale_y = page_rect.height() / 297.0



                scale = min(scale_x, scale_y)



                for page_idx in range(1, self._num_pages + 1):



                    if page_idx > 1:



                        printer.newPage()



                    painter.save()



                    painter.scale(scale, scale)



                    source = QRectF(0, (page_idx - 1) * 297.0, 210.0, 297.0)



                    self.scene.render(painter, QRectF(), source)



                    painter.restore()



                logger.info("طع¾ظâ€¦ حظظ¾ظ PDF: %s", path)



                QMessageBox.information(self, "تم", f"تم حفظ الملف:\n{path}")



            except Exception as e:



                logger.error("ظظ¾شظâ€‍ حظظ¾ظ PDF", exc_info=True)



                QMessageBox.warning(self, "خطأ", f"فشل حفظ PDF:\n{e}")



            finally:



                painter.end()



                for it in bg_items:



                    it.setVisible(True)



                for item in selected:



                    item.setSelected(True)



