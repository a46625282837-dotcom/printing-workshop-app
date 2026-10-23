import logging



import io



import os



from concurrent.futures import ThreadPoolExecutor, as_completed



from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QVBoxLayout,



                               QPushButton, QWidget, QHBoxLayout, QFileDialog,



                               QMessageBox, QDialog, QComboBox, QDialogButtonBox,



                               QLabel, QSpinBox, QAbstractSpinBox, QCheckBox,



                               QGraphicsProxyWidget, QInputDialog, QMenu, QScrollArea, QLineEdit, QSlider, QDoubleSpinBox)



from PySide6.QtCore import Qt, Signal, QSettings, QThread, QTimer, QRectF



from PySide6.QtGui import QPixmap, QPen, QColor, QBrush, QPainter, QShortcut, QKeySequence, QTransform, QImage



from PySide6.QtGui import QPageSize



from PySide6.QtPrintSupport import QPrinter, QPrinterInfo



from PIL import Image

import numpy as np

from ui import theme

from core.printer import print_scene, get_selected_printer_name, set_printer_name, PAPER_TYPES, PAPER_TYPE_NAMES, get_last_paper_type, set_last_paper_type, NO_PRINT_KEY, show_printer_properties



from ui.id_card_item import IDCardItem, CARD_W, CARD_H







logger = logging.getLogger(__name__)


def _adjust_brightness_pixmap(pix: QPixmap, value: int) -> QPixmap:
    """ضبط سطوع QPixmap: value من -100 (تعتيم) إلى +100 (إضاءة).

    ضرب موحّد لعناصر RGB يحفظ الألوان في الاتجاهين: عند تقليل الإضاءة
    فقط تقل السطوع دون أن تتحول الصورة إلى أسود/أبيض.
    """
    if value == 0 or pix is None or pix.isNull():
        return pix
    img = pix.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    arr = np.frombuffer(bytes(img.bits()), dtype=np.uint8).reshape(h, w, 4).copy()
    factor = 1.0 + (value / 100.0) * 0.6
    rgb = arr[:, :, :3].astype(np.float32) * factor
    arr[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    out_img = QImage(arr.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
    return QPixmap.fromImage(out_img)







A4_W = 210



A4_H = 297



MARGIN = 5



ROW_GAP = 2

COL_GAP = 10



CARDS_PER_ROW = 2



MAX_ROWS = (A4_H - 2 * MARGIN + ROW_GAP) // (CARD_H + ROW_GAP)



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
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        theme.on_change(self._apply_canvas_bg)
        self._apply_canvas_bg()




    def _apply_canvas_bg(self, mode=None):
        self.setBackgroundBrush(QColor(theme.canvas_color()))







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



        self.printer_combo.setEditable(True)



        self.printer_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)



        self.printer_combo.setToolTip("اختر طابعة من القائمة أو اكتب اسم الطابعة مباشرة")



        for p in QPrinterInfo.availablePrinters():



            self.printer_combo.addItem(p.printerName())



        current = get_selected_printer_name()



        if current:



            idx = self.printer_combo.findText(current)



            if idx >= 0:



                self.printer_combo.setCurrentIndex(idx)



        printer_row.addWidget(self.printer_combo)

        self.printer_props_btn = QPushButton("⚙ خصائص الطابعة")
        self.printer_props_btn.setToolTip("اختيار نوع الورق الفعلي الذي تطبعه الطابعة، جودة الطباعة، وبقية خصائص الطابعة")
        self.printer_props_btn.setStyleSheet("""
            QPushButton {
                background: #5a5a5a; color: white; font-size: 12px; font-weight: bold;
                padding: 6px 12px; border-radius: 5px; border: none;
            }
            QPushButton:hover { background: #e67e22; }
        """)
        self.printer_props_btn.clicked.connect(self._open_printer_properties)
        printer_row.addWidget(self.printer_props_btn)

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

        self.back_off_lbl = QLabel("معايرة الوجه الخلفي (للطباعة على وجهين فقط، اتركه صفر):")
        layout.addWidget(self.back_off_lbl)
        back_dx_row = QHBoxLayout()
        back_dx_row.addWidget(QLabel("إزاحة الظهر أفقياً (مم):"))
        self.back_dx_spin = QDoubleSpinBox()
        self.back_dx_spin.setRange(-10.0, 10.0)
        self.back_dx_spin.setDecimals(1)
        self.back_dx_spin.setSingleStep(0.1)
        self.back_dx_spin.setValue(0.0)
        back_dx_row.addWidget(self.back_dx_spin)
        back_dx_row.addStretch(1)
        layout.addLayout(back_dx_row)
        back_dy_row = QHBoxLayout()
        back_dy_row.addWidget(QLabel("إزاحة الظهر عمودياً (مم):"))
        self.back_dy_spin = QDoubleSpinBox()
        self.back_dy_spin.setRange(-10.0, 10.0)
        self.back_dy_spin.setDecimals(1)
        self.back_dy_spin.setSingleStep(0.1)
        self.back_dy_spin.setValue(0.0)
        back_dy_row.addWidget(self.back_dy_spin)
        back_dy_row.addStretch(1)
        layout.addLayout(back_dy_row)

        self.duplex_cb.toggled.connect(self._on_duplex_toggled)
        self.printer_combo.currentTextChanged.connect(self._load_back_offsets)
        self._load_back_offsets()







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







    def _open_printer_properties(self):
        printer_name = self.printer_combo.currentText().strip()
        if not printer_name:
            QMessageBox.information(self, "تنبيه", "اكتب أو اختر اسم الطابعة أولاً.")
            return
        logger.info("فتح خصائص الطابعة: %r", printer_name)
        ok, why = show_printer_properties(self, printer_name)
        if not ok:
            QMessageBox.warning(self, "تنبيه",
                                f"تعذر فتح خصائص الطابعة '{printer_name}'.\n{why or ''}")

    def _on_all_pages_toggled(self, checked):



        self.from_spin.setEnabled(not checked)



        self.to_spin.setEnabled(not checked)







    def selected_printer(self):



        return self.printer_combo.currentText()







    def copies(self):



        return self.copies_spin.value()







    def duplex(self):



        return self.duplex_cb.isChecked()

    def _on_duplex_toggled(self, checked):
        self.back_off_lbl.setEnabled(checked)
        self.back_dx_spin.setEnabled(checked)
        self.back_dy_spin.setEnabled(checked)

    def _load_back_offsets(self):
        settings = QSettings("ورقة طباعة", "A4Editor")
        pname = self.printer_combo.currentText().strip()
        def_dx, def_dy = 0.0, 0.0
        if pname and "hp" in pname.lower():
            def_dx, def_dy = 9.0, -1.0
        if not pname:
            self.back_dx_spin.setValue(0.0)
            self.back_dy_spin.setValue(0.0)
        else:
            self.back_dx_spin.setValue(
                settings.value(f"duplexOffset/{pname}/dx", def_dx, type=float))
            self.back_dy_spin.setValue(
                settings.value(f"duplexOffset/{pname}/dy", def_dy, type=float))
        self._on_duplex_toggled(self.duplex_cb.isChecked())

    def back_offset_dx(self):
        return self.back_dx_spin.value()

    def back_offset_dy(self):
        return self.back_dy_spin.value()

    def accept(self):
        settings = QSettings("ورقة طباعة", "A4Editor")
        pname = self.printer_combo.currentText().strip()
        if pname:
            settings.setValue(f"duplexOffset/{pname}/dx", self.back_dx_spin.value())
            settings.setValue(f"duplexOffset/{pname}/dy", self.back_dy_spin.value())
        super().accept()







    def page_range(self):

        if self.all_pages_cb.isChecked():

            return None

        return (self.from_spin.value(), self.to_spin.value())

    def paper_type(self):

        return self.paper_combo.currentText()











class CardProcessingThread(QThread):



    card_ready = Signal(bytes, object)

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
                from core.ai_agent import ai_cut_card

                name = item if isinstance(item, str) else "clipboard-%d" % (len(self._items))

                if isinstance(item, str):
                    pil = Image.open(item)
                    name = os.path.basename(item)
                else:
                    pil = Image.open(io.BytesIO(item))

                orig_buf = io.BytesIO()
                try:
                    pil.convert("RGB").save(orig_buf, format="PNG")
                    original_bytes = orig_buf.getvalue()
                except Exception:
                    original_bytes = b""

                if self._no_crop:
                    result = pil.convert("RGB")
                    corners = [(0.0, 0.0), (result.width - 1, 0.0),
                               (result.width - 1, result.height - 1),
                               (0.0, result.height - 1)]
                else:
                    corners = []
                    result = ai_cut_card(pil, image_name=name, corners_out=corners)

                if result is None:
                    # الوكيل فشل — لا نقصّ بالطريقة التقليدية أبداً، لا تُوضع البطاقة.
                    return None, original_bytes, []

                buf = io.BytesIO()
                result.save(buf, format="PNG")

                return buf.getvalue(), original_bytes, corners







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



                            self.card_ready.emit(val[0], val[2])
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
        self._display_pixmap = None
        self._cache_key = None
        self._duplicate_tolerance = 8.0
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

    def _cached_display(self):
        key = (self.size(), self._drawing_pixmap.cacheKey())
        if self._cache_key != key:
            self._display_pixmap = self._drawing_pixmap.scaled(
                self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._cache_key = key
        return self._display_pixmap

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
            new_pt = QPointF(event.position().x(), event.position().y())
            if self._points:
                last = self._points[-1]
                if (new_pt - last).manhattanLength() < self._duplicate_tolerance:
                    event.accept()
                    return
            self._points.append(new_pt)
            self.update()
            event.accept()

    def mouseDoubleClickEvent(self, event):
        event.accept()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._drawing_pixmap and not self._drawing_pixmap.isNull():
            scaled = self._cached_display()
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




        btn_save_images = QPushButton("🖼️ حفظ الصور")




        btn_save_images.setToolTip("حفظ كل بطاقة مقصوصة صورة منفصلة بجودتها الكاملة")




        btn_save_images.clicked.connect(self.save_images)



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




        btn_bar.addWidget(btn_save_images)



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



        self.scene.selectionChanged.connect(self._on_selection_changed)



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
    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, 'view'):
            QTimer.singleShot(0, self._scroll_to_page_top)


    def _scroll_to_page_top(self):
        if not hasattr(self, 'view'):
            return
        sb_v = self.view.verticalScrollBar()
        sb_h = self.view.horizontalScrollBar()
        sb_v.setValue(sb_v.minimum())
        sb_h.setValue(sb_h.minimum())









    def resizeEvent(self, event):



        super().resizeEvent(event)



        if hasattr(self, '_spinner') and self.view:

            self._spinner.move(



                (self.view.width() - self._spinner.width()) // 2,



                (self.view.height() - self._spinner.height()) // 2



            )

            self._recenter_status()







    def closeEvent(self, event):



        if hasattr(self, '_processing_thread') and self._processing_thread and self._processing_thread.isRunning():



            self._processing_thread.quit()



            self._processing_thread.wait(2000)



        super().closeEvent(event)







    def _col_x(self, col):
        """إحداثي x لعمود معين يملأ عرض الورقة حتى تلتصق الأعمدة الأطراف بالحواف."""
        if CARDS_PER_ROW <= 1:
            return MARGIN
        gap = (A4_W - 2 * MARGIN - CARDS_PER_ROW * CARD_W) / float(CARDS_PER_ROW - 1)
        return MARGIN + col * (CARD_W + gap)

    def _draw_page_area(self, page_index):



        y0 = page_index * A4_H



        page_rect = self.scene.addRect(0, y0, A4_W, A4_H, QPen(Qt.black), QBrush(Qt.white))
        page_rect.setZValue(-1)
        page_rect.setData(NO_PRINT_KEY, True)



        pen = QPen(QColor(200, 200, 200, 60), 0.3, Qt.DashLine)



        for col in range(CARDS_PER_ROW):



            cx = self._col_x(col)

            y = y0 + MARGIN



            while y + CARD_H <= y0 + A4_H - MARGIN:



                slot = self.scene.addRect(cx, y, CARD_W, CARD_H, pen)
                slot.setData(NO_PRINT_KEY, True)



                y += CARD_H + ROW_GAP







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







    def _open_ai_settings(self):
        from core.local_yolo import get_local_model_path, set_local_model_path, has_local_model

        dlg = QDialog(self)
        dlg.setWindowTitle("إعدادات القص المحلي (YOLOv8)")
        dlg.setFixedSize(500, 330)
        dlg.setLayoutDirection(Qt.RightToLeft)
        dlg.setStyleSheet("QDialog{background:#f5f5f5;}")
        lay = QVBoxLayout(dlg)
        lay.setSpacing(10)
        lay.setContentsMargins(20, 20, 20, 20)

        lbl = QLabel("وكيل القصّ المحلي (YOLOv8) — فائق السرعة، بلا إنترنت")
        lbl.setStyleSheet("font-size:14px;font-weight:bold;color:#000;")
        lay.addWidget(lbl)

        info = QLabel("يعمل التطبيق بنموذج YOLO المدرَّب المحلي (models/card_detector.onnx) "
                      "ويقصر زوايا الهوية تلقائياً. إن توفر ملف .pt مدرب على هوياتك فسيُستخدم "
                      "أولاً — وإلا يقع الكشف على نموذج card_detector المضمّن.")
        info.setWordWrap(True)
        info.setStyleSheet("font-size:11px;color:#777;")
        lay.addWidget(info)

        lbl_mp = QLabel("مسار ملف النموذج المحلي (.pt)")
        lbl_mp.setStyleSheet("font-size:12px;font-weight:bold;color:#000;")
        lay.addWidget(lbl_mp)
        txt_mp = QLineEdit()
        txt_mp.setPlaceholderText("مثال: models/id_card.pt")
        txt_mp.setText(get_local_model_path())
        lay.addWidget(txt_mp)
        mp_status = QLabel()
        mp_status.setStyleSheet("font-size:11px;color:#777;")
        mp_status.setAlignment(Qt.AlignCenter)
        mp_status.setText("✓ النموذج موجود وجاهز" if has_local_model() else "يُستعمل نموذج card_detector المضمّن")
        lay.addWidget(mp_status)

        btn_row = QHBoxLayout()
        btn_save = QPushButton("💾 حفظ")
        btn_save.setStyleSheet(
            "QPushButton{background:#0078d4;color:white;font-size:13px;font-weight:bold;border:none;border-radius:6px;padding:0 20px}"
            "QPushButton:hover{background:#005fa3}")
        btn_log = QPushButton("📜 عرض السجل")
        btn_log.setStyleSheet(
            "QPushButton{background:#ff9800;color:white;font-size:13px;font-weight:bold;border:none;border-radius:6px;padding:0 20px}"
            "QPushButton:hover{background:#e68900}")
        btn_close = QPushButton("إغلاق")
        btn_close.clicked.connect(dlg.accept)

        def on_save():
            set_local_model_path(txt_mp.text())
            status.setText("✓ تم حفظ مسار النموذج المحلي")

        def on_log():
            from core.ai_agent import get_crop_log
            log_dlg = QDialog(dlg)
            log_dlg.setWindowTitle("سجلّ القصّ")
            log_dlg.resize(620, 420)
            log_dlg.setLayoutDirection(Qt.RightToLeft)
            lay2 = QVBoxLayout(log_dlg)
            tx = QTextEdit()
            tx.setReadOnly(True)
            tx.setStyleSheet("font-size:12px;font-family:Consolas,'Courier New';direction:ltr;text-align:left;")
            tx.setPlainText(get_crop_log())
            lay2.addWidget(tx)
            btn_ok = QPushButton("إغلاق")
            btn_ok.clicked.connect(log_dlg.accept)
            lay2.addWidget(btn_ok)
            log_dlg.exec()

        status = QLabel()
        status.setStyleSheet("font-size:12px;color:#4caf50;font-weight:bold;")
        status.setAlignment(Qt.AlignCenter)
        lay.addWidget(status)

        btn_save.clicked.connect(on_save)
        btn_log.clicked.connect(on_log)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_log)
        btn_row.addWidget(btn_close)
        lay.addLayout(btn_row)

        dlg.exec()

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
        self._no_crop = no_crop
        thread = CardProcessingThread(paths, no_crop)
        thread.card_ready.connect(self._on_card_buffer_ready)
        thread.original_ready.connect(self._on_original_buffer_ready)
        thread.finished.connect(self._on_processing_done)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        self._processing_thread = thread
        self._spinner.show()
        if hasattr(self, "_status_label"):
            self._status_label.setText("جارٍ قصّ الهويات بالذكاء الاصطناعي...")
            self._status_label.show()

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







    def _on_card_buffer_ready(self, buf, corners=None):



        if not buf:
            return  # الوكيل رفض القص — لا تُوضع البطاقة إطلاقاً

        qpix = QPixmap()



        if qpix.loadFromData(buf):



            self._place_card(qpix, detect_corners=corners)
    def _on_original_buffer_ready(self, buf):



        qpix = QPixmap()
        if qpix.loadFromData(buf) and self.cards:

            self.cards[-1]._original_pixmap = qpix

    def _show_original_image(self, card_item):
        original_pix = card_item._original_pixmap
        if original_pix is None or original_pix.isNull():
            original_pix = card_item.pixmap()
        if original_pix is None or original_pix.isNull():
            return
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

        content_row = QHBoxLayout()
        left_col = QVBoxLayout()
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.addWidget(overlay, 1)
        left_col.addWidget(preview_label, 1)
        content_row.addLayout(left_col, 1)
        v_layout.addLayout(content_row, 1)

        slider_box = QVBoxLayout()
        brightness_label = QLabel("سطوع")
        brightness_label.setAlignment(Qt.AlignCenter)
        brightness_label.setStyleSheet("color:#ccc;font-weight:bold;font-size:11px;")
        slider_box.addWidget(brightness_label)
        brightness_slider = QSlider(Qt.Vertical)
        brightness_slider.setRange(-100, 100)
        brightness_slider.setValue(getattr(card_item, "_last_brightness", 0))
        brightness_slider.setFixedHeight(340)
        brightness_slider.setTickPosition(QSlider.TickPosition.TicksLeft)
        brightness_slider.setStyleSheet(
            "QSlider::groove:vertical{background:#444;width:6px;border-radius:3px;}"
            "QSlider::handle:vertical{background:#f1c40f;width:18px;height:26px;margin:-8px -6px;border-radius:9px;}"
        )
        slider_box.addWidget(brightness_slider, 1, Qt.AlignCenter)
        slider_box.addStretch()
        content_row.addLayout(slider_box)

        preview_pixmap = [None]
        preview_transforms = [0]
        preview_hflip = [False]
        brightness = [brightness_slider.value()]
        brightness_base = [None]
        display_original = [original_pix]

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

        def _render_preview():
            base = brightness_base[0]
            if base is None:
                return
            adjusted = _adjust_brightness_pixmap(base, brightness[0])
            preview_pixmap[0] = adjusted
            transformed = _apply_transforms(adjusted)
            scaled = transformed.scaled(preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            preview_label.setPixmap(scaled)

        def _show_preview(cropped):
            brightness_base[0] = cropped
            brightness[0] = brightness_slider.value()
            overlay.hide()
            preview_label.show()
            _render_preview()
            _update_buttons(True)

        def _show_overlay():
            preview_pixmap[0] = None
            brightness_base[0] = None
            brightness[0] = 0
            brightness_slider.setValue(0)
            display_original[0] = original_pix
            preview_label.hide()
            overlay.show()
            overlay.clear_points()
            _update_buttons(False)

        def _apply_brightness_overlay(v):
            brightness[0] = v
            if brightness_base[0] is not None:
                _render_preview()
                return
            display_original[0] = _adjust_brightness_pixmap(original_pix, v)
            overlay.set_display(display_original[0], 1.0, 0, 0)
            overlay.update()

        # تحديث خفيف للسحب: تُدمج إشارات valueChanged في مؤقّت قصير وتبقى
        # أحدث قيمة فقط — فيتحرك الشريط بنعومة دون أن يثقل إعادة الرسم.
        _bright_timer = QTimer(dlg)
        _bright_timer.setInterval(60)
        _bright_timer.setSingleShot(True)
        _bright_pending = [None]

        def _bright_schedule(v):
            _bright_pending[0] = v
            _bright_timer.start()

        def _bright_flush():
            if _bright_pending[0] is not None:
                _apply_brightness_overlay(_bright_pending[0])
                _bright_pending[0] = None

        _bright_timer.timeout.connect(_bright_flush)

        brightness_slider.valueChanged.connect(_bright_schedule)
        _apply_brightness_overlay(brightness_slider.value())

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
            btn_add.setEnabled(True)

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
            if preview_pixmap[0] is not None:
                final = _apply_transforms(preview_pixmap[0])
            else:
                from PySide6.QtGui import QPixmap as _QPixmap
                current = card_item.pixmap()
                if current is None or current.isNull():
                    current = original_pix
                final = _adjust_brightness_pixmap(current, brightness[0])
            # تُختم التعديلات الحالية في أصل جديد (bake): يظهر التغيير السابق
            # عند العودة، ويبدأ الشريط من صفر لمواصلة التعديل دون تراكم مزدوج.
            card_item._last_brightness = 0
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
        btn_add.setEnabled(True)

        def on_resize(event):
            if brightness_base[0] is not None:
                _render_preview()
            elif not display_original[0].isNull():
                overlay.set_display(display_original[0], 1.0, 0, 0)
                scaled = display_original[0].scaled(overlay.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                sx = overlay.width() - scaled.width()
                sy = overlay.height() - scaled.height()
                overlay.set_display(display_original[0], scaled.width() / display_original[0].width(), sx // 2, sy // 2)

        _resize_pending = [False]

        def _finalize_resize():
            _resize_pending[0] = False
            on_resize(None)

        def _schedule_resize(event=None):
            if not _resize_pending[0]:
                _resize_pending[0] = True
                QTimer.singleShot(120, _finalize_resize)

        dlg.resizeEvent = _schedule_resize
        QTimer.singleShot(0, lambda: on_resize(None))
        btn_close.setFocus()
        dlg.exec()
        card_item._last_brightness = brightness_slider.value()

    def _perspective_crop(self, pixmap, points):
        """القاعدة الثابتة: تحديد الزوايا الأربع الحقيقية ثم warpPerspective.

        - ترتيب الزوايا عبر order_card_corners (نفس قاعدة YOLO): مستقر لأي
          ميلان حتى ~90° — لا ينقلب أبداً عند 45° كما كانت تقع سابقاً.
        - warpPerspective حصرياً من النقاط الأربع إلى مستطيل مستقيم أفقي
          (الناتج كأنه مسحوب بماسح ضوئي: بلا ميلان نهائياً).
        - فحص استثنائي: ناتج عمودي/نسبة شاذة ⇒ قصّ مستطيل بسيط بهامش 5
          (لا يُخرج صورة مقلوبة تحت أي ظرف)."""
        import cv2
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        from core.local_yolo import _landscape_order
        buf = QBuffer()
        buf.open(QIODevice.ReadWrite)
        pixmap.save(buf, "PNG")
        buf.seek(0)
        raw = buf.data().data()
        buf.close()
        arr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            return pixmap
        h, w = arr.shape[:2]
        pts = np.array([(p.x(), p.y()) if hasattr(p, "x") else (p[0], p[1])
                        for p in points], dtype=np.float32).reshape(4, 2)
        # ترتيب النقاط الأربع حرفياً كما نقرها المستخدم (TL/TR/BR/BL) —
        # بلا إعادة بناء عبر minAreaRect كي لا تظهر خلفية خارج المربع المرسوم.
        ordered = _landscape_order(pts)

        (tl, tr, br, bl) = ordered
        w_top = np.linalg.norm(tr - tl)
        w_bot = np.linalg.norm(br - bl)
        h_lft = np.linalg.norm(bl - tl)
        h_rgt = np.linalg.norm(br - tr)
        dst_w = int(max(w_top, w_bot))
        dst_h = int(max(h_lft, h_rgt))
        aspect = (dst_w / dst_h) if dst_h > 0 else 0.0

        # فحص استثنائي: ناتج عمودي أو نسبة بعيدة عن بطاقة الهوية — قصّ بسيط آمن
        if dst_w < 10 or dst_h < 10 or dst_w < dst_h or not (1.1 <= aspect <= 2.6):
            try:
                x, y, bw, bh = cv2.boundingRect(pts.astype(np.int32).reshape(-1, 1, 2))
                pad = 5
                x0, y0 = max(0, x - pad), max(0, y - pad)
                x1, y1 = min(w, x + bw + pad), min(h, y + bh + pad)
                if x1 - x0 < 2 or y1 - y0 < 2:
                    return pixmap
                box_arr = arr[y0:y1, x0:x1]
            except Exception:
                return pixmap
        else:
            dst_w = int(round(dst_w * 1.015))
            dst_h = int(round(dst_h * 1.015))
            dst = np.array([[0, 0], [dst_w - 1, 0],
                            [dst_w - 1, dst_h - 1], [0, dst_h - 1]], dtype=np.float32)
            M = cv2.getPerspectiveTransform(ordered, dst)
            box_arr = cv2.warpPerspective(arr, M, (dst_w, dst_h))

        box_arr = cv2.cvtColor(box_arr, cv2.COLOR_BGR2RGB)
        ch = 3
        h2, w2 = box_arr.shape[:2]
        bytes_per_line = ch * w2
        from PySide6.QtGui import QImage as QImg
        qimg = QImg(box_arr.data.tobytes(), w2, h2, bytes_per_line, QImg.Format.Format_RGB888)
        return QPixmap.fromImage(qimg)

    def _replace_card(self, card_idx, new_pixmap):
        if card_idx < 0 or card_idx >= len(self.cards):
            return
        item = self.cards[card_idx]
        old_pos = item.pos()
        old_rotation = item._rotation
        old_brightness = getattr(item, "_last_brightness", 0)
        self.scene.removeItem(item)
        new_item = IDCardItem(new_pixmap, index=card_idx)
        new_item.on_dropped = self._on_card_dropped
        new_item.on_double_clicked = self._show_original_image
        new_item.on_sakan_toggled = self._reflow_page_layouts
        new_item._sakan_center_x = A4_W / 2.0
        new_item.attach_action_button(lambda checked=False, item=new_item: item.toggle_sakan_size())
        new_item._original_pixmap = new_pixmap
        new_item._last_brightness = old_brightness
        new_item.setPos(old_pos)
        new_item._rotation = old_rotation
        new_item.set_item_scale(1.0, snap=True)
        self.scene.addItem(new_item)
        self.cards[card_idx] = new_item







    def _recenter_status(self):

        if not hasattr(self, "_status_label") or not self.view:

            return

        self._status_label.adjustSize()

        self._status_label.move(

            (self.view.width() - self._status_label.width()) // 2,

            self._spinner.y() + self._spinner.height() + 10,

        )

    def _on_ai_status(self, text):

        if not hasattr(self, "_status_label"):

            return

        self._status_label.setText(text)

        self._status_label.adjustSize()

        self._recenter_status()

        self._status_label.show()

    def _on_processing_done(self):



        self._spinner.hide()

        if hasattr(self, "_status_label"):
            self._status_label.hide()



        logger.info("اظâ€ طع¾ظâ€،طع¾ ظâ€¦عاظâ€‍جة جظâ€¦ظظ¹ع اظâ€‍صظث†ر")





    def add_image(self, path_or_bytes):
        from core.ai_agent import ai_cut_card

        try:
            if isinstance(path_or_bytes, str):
                pil = Image.open(path_or_bytes)
            else:
                pil = Image.open(io.BytesIO(path_or_bytes))

            if self.btn_nocrop.isChecked():
                result = pil.convert("RGB")
            else:
                result = ai_cut_card(pil)

            if result is None:
                QMessageBox.warning(self, "تعذّر القصّ",
                                    "لم يتعرّف الوكيل المحلي على البطاقة — لم تُقصّ الصورة.")
                return

            buf = io.BytesIO()
            result.save(buf, format="PNG")
            buf.seek(0)

            qpix = QPixmap()
            if not qpix.loadFromData(buf.getvalue()):
                raise ValueError("فشل تحميل الصورة المعالجة")

            self._place_card(qpix)

        except Exception as e:
            logger.error("فشل إضافة الصورة", exc_info=True)
            QMessageBox.warning(self, "خطأ", f"فشل تحميل الصورة: {e}")







    def _on_selection_changed(self):
        for card in self.cards:
            card.show_action_button(card.isSelected())

    def _make_card_large(self, item):
        """تبديل حجم مستطيل البطاقة بين الأصلي و 9.8×6سم."""
        item.toggle_sakan_size()

    def _reflow_page_layouts(self):
        """إعادة ترتيب البطاقات في كل صفحة حتى لا يلتصق المستطيل المكبّر بمجاوراته.

        عند تكبير بطاقة إلى 98×60، يزيد ارتفاع صفّها، فتُنثنى الفراغات الرأسية
        تلقائياً ليبقى هناك مسافة إيجابية بين كل صف والآخر. وتبقى المسافات
        الحالية (ROW_GAP) كما هي تماماً عندما لا توجد أي بطاقة مكبّرة.
        """
        if not self.cards:
            return
        avail_h = A4_H - 2 * MARGIN
        for page_idx in range(self._num_pages):
            page_start = page_idx * MAX_CARDS
            page_end = page_start + MAX_CARDS
            in_page = [c for c in self.cards if page_start <= c.index < page_end]
            if not in_page:
                continue
            rows = {}
            for c in in_page:
                local = c.index % MAX_CARDS
                row = local // CARDS_PER_ROW
                h = float(c.card_size_mm()[1])
                rows[row] = max(rows.get(row, CARD_H), h)
            row_ids = sorted(rows.keys())
            if not row_ids:
                continue
            any_large = any(getattr(c, "_sakan_large", False) for c in in_page)
            sum_h = sum(rows[r] for r in row_ids)
            n_gaps = max(0, len(row_ids) - 1)
            gap = ROW_GAP
            if any_large and n_gaps > 0:
                available = avail_h - sum_h
                gap = min(ROW_GAP, max(0.5, available / n_gaps))
            y = page_idx * A4_H + MARGIN
            y_of_row = {}
            for r in row_ids:
                y_of_row[r] = y
                y += rows[r] + gap
            for c in in_page:
                local = c.index % MAX_CARDS
                col = local % CARDS_PER_ROW
                row = local // CARDS_PER_ROW
                w = float(c.card_size_mm()[0])
                x = self._col_x(col)
                if getattr(c, "_sakan_large", False):
                    x = max(MARGIN, min(x, A4_W - MARGIN - w))
                c.setPos(x, y_of_row[row])
                c._snapped = True
                c._position_action_button()

    def _grid_pos(self, idx):



        page_idx = idx // MAX_CARDS



        local_idx = idx % MAX_CARDS



        y_offset = page_idx * A4_H



        col = local_idx % CARDS_PER_ROW



        row = local_idx // CARDS_PER_ROW



        x = self._col_x(col)



        y = y_offset + MARGIN + row * (CARD_H + ROW_GAP)



        return x, y








    def _find_first_free_slot(self, start_page=0):
        total_slots = MAX_CARDS * max(1, self._num_pages)
        for idx in range(start_page * MAX_CARDS, total_slots):
            gx, gy = self._grid_pos(idx)
            cx, cy = gx + CARD_W / 2.0, gy + CARD_H / 2.0
            blocked = False
            for other in self.cards:
                ow, oh = other.card_size_mm()
                ox, oy = other.pos().x(), other.pos().y()
                if cx >= ox and cx <= ox + ow and cy >= oy and cy <= oy + oh:
                    blocked = True
                    break
            if not blocked:
                self._ensure_page(idx // MAX_CARDS)
                return idx
        idx = total_slots
        self._ensure_page(idx // MAX_CARDS)
        return idx



    def _place_card(self, pixmap: QPixmap, detect_corners=None):



        idx = self._find_first_free_slot()



        self._ensure_page(idx // MAX_CARDS)



        item = IDCardItem(pixmap, index=idx, detect_corners=detect_corners)



        item.on_dropped = self._on_card_dropped
        item.on_double_clicked = self._show_original_image
        item.on_sakan_toggled = self._reflow_page_layouts
        item._sakan_center_x = A4_W / 2.0
        item.attach_action_button(lambda checked=False, item=item: item.toggle_sakan_size())



        item.setPos(*self._grid_pos(idx))

        item._position_action_button()


        item.set_item_scale(1.0, snap=True)



        self.scene.addItem(item)



        ins = 0
        for c in self.cards:
            if c.index <= idx:
                try:
                    ins = self.cards.index(c) + 1
                except Exception:
                    pass
        self.cards.insert(ins, item)

        item._original_pixmap = pixmap


        return item


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
        """إرساء البطاقة على أقرب خانة حرة في صفحتها الحالية.

        تُحدَّد الصفحة من موضع البطاقة (أعلى-يسار) لا من مركزها حتى لا تقفز
        بطاقة الصف الأخير إلى الصفحة التالية عند ميلان بسيط أثناء نقلها
        أفقياً. تُختار أقرب خانة لا تحوي بطاقة أخرى فلا يحدث تراكب ولا انتزاع
        لجارة، ثم يُسند الفهرس للبطاقة وتُعاد صفوف الصفحة بالحجوم الفعلية
        (الجزء الذي تركته بطاقة مكبّرة ينكمش ويعود طبيعياً).
        """
        drop_page = max(0, min(self._num_pages - 1, int(item.pos().y() / A4_H)))
        self._ensure_page(drop_page)
        total_slots = MAX_CARDS * self._num_pages
        free = []
        for i in range(total_slots):
            gx, gy = self._grid_pos(i)
            if int(gy / A4_H) != drop_page:
                continue
            cx, cy = gx + CARD_W / 2.0, gy + CARD_H / 2.0
            blocked = False
            for other in self.cards:
                if other is item:
                    continue
                ow, oh = other.card_size_mm()
                ox, oy = other.pos().x(), other.pos().y()
                if cx < ox or cx > ox + ow or cy < oy or cy > oy + oh:
                    continue
                blocked = True
                break
            if not blocked:
                dx = center.x() - (gx + CARD_W / 2)
                dy = center.y() - (gy + CARD_H / 2)
                free.append((dx * dx + dy * dy, i))
        if not free:
            self._constrain_card(item)
            return
        _, best_idx = min(free)
        if item.index != best_idx:
            item.index = best_idx
            if item in self.cards:
                self.cards.remove(item)
            ins = 0
            for c in self.cards:
                if c.index <= best_idx:
                    ins = self.cards.index(c) + 1
            self.cards.insert(ins, item)
        item.setPos(*self._grid_pos(best_idx))
        self._reflow_page_layouts()
        self._constrain_card(item)







    def _swap_cards(self, a, b):



        pos_a = a._drag_origin if a._drag_origin is not None else a.pos()

        pos_b = b.pos()

        a.setPos(pos_b)

        b.setPos(pos_a)

        i = self.cards.index(a)

        j = self.cards.index(b)

        self.cards[i], self.cards[j] = self.cards[j], self.cards[i]

        a.index = j
        b.index = i
        self._reflow_page_layouts()


        logger.info("طع¾ظâ€¦ طع¾بدظظ¹ظâ€‍ اظâ€‍بطاظâ€ڑطع¾ظظ¹ظâ€  %d ظث† %d", i + 1, j + 1)







    def _constrain_card(self, item):
        """قيّد موضع بطاقة مكبّرة داخل حدود الصفحة ومنع تراكبها مع المجاورات.

        البطاقة الكبيرة (98×60 عبر سكن) بعد سحبها قد تتجاوز حافة الورقة
        اليمنى أو تتراكب مع البطاقة التي تحتها (الشبكة مبنية لمقاس 90×55).
        نقيّد x ضمن حدود الصفحة، ونزاح عمودياً حتى لا يوجد تداخل مع أي
        بطاقة أخرى في نفس الصفحة.
        """
        w, h = item.card_size_mm()
        p = item.pos()
        page = int(p.y() // A4_H)
        lo_y = page * A4_H + MARGIN
        hi_y = page * A4_H + (A4_H - MARGIN - h)
        x = max(MARGIN, min(p.x(), A4_W - MARGIN - w))
        y = max(lo_y, min(p.y(), hi_y))

        def overlaps(cx, cy, cw, ch):
            for other in self.cards:
                if other is item or int(round(other.pos().y() / A4_H)) != page:
                    continue
                ow, oh = other.card_size_mm()
                ox, oy = other.pos().x(), other.pos().y()
                if cx + cw <= ox + 0.01 or cx >= ox + ow - 0.01:
                    continue
                if cy + ch <= oy + 0.01 or cy >= oy + oh - 0.01:
                    continue
                return True
            return False

        guard = 0
        while guard < 10 and overlaps(x, y, w, h):
            guard += 1
            oy = None
            for other in self.cards:
                if other is item or int(round(other.pos().y() / A4_H)) != page:
                    continue
                ow, oh = other.card_size_mm()
                ox, ooy = other.pos().x(), other.pos().y()
                if x + w <= ox + 0.01 or x >= ox + ow - 0.01:
                    continue
                if y + h <= ooy + 0.01 or y >= ooy + oh - 0.01:
                    continue
                oy = ooy
                break
            if oy is None:
                break
            if oy >= y:
                ny = oy - h - ROW_GAP
            else:
                ny = oy + oh + ROW_GAP
            ny = max(lo_y, min(ny, hi_y))
            if abs(ny - y) < 0.01:
                break
            y = ny
        # إن بقي تداخل (مثل صف ممتلئ أفقياً بعد تبديل): ننقل البطاقة إلى
        # أقرب صف خالٍ في نفس الصفحة حيث لا تتصادم مع أي بطاقة أخرى.
        if overlaps(x, y, w, h):
            candidates = set(MARGIN + r * (CARD_H + ROW_GAP) for r in range(MAX_ROWS))
            candidates.add(A4_H - MARGIN - h)
            best_y, best_d = None, None
            for yy in candidates:
                yy = max(lo_y, min(yy, hi_y))
                if overlaps(x, yy, w, h):
                    continue
                d = abs(yy - y)
                if best_y is None or d < best_d:
                    best_y, best_d = yy, d
            if best_y is not None:
                y = best_y
        item.setPos(x, y)
        item._position_action_button()

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



            bg_items = [it for it in self.scene.items() if it.data(NO_PRINT_KEY) and it.isVisible()]



            for it in bg_items:



                it.setVisible(False)
            pt = dialog.paper_type()
            if pt:
                set_last_paper_type(pt)
            print_scene(self, self.scene, copies=copies, page_count=self._num_pages, duplex=duplex,
                        page_range=dialog.page_range(), paper_type=pt,
                        back_offset_dx_mm=dialog.back_offset_dx(), back_offset_dy_mm=dialog.back_offset_dy())





            for it in bg_items:



                it.setVisible(True)

                for item in selected:





                    item.setSelected(True)

    def save_images(self):
        if not self.cards:
            QMessageBox.information(self, "تنبيه", "لا توجد بطاقات للحفظ.")
            return

        folder = QFileDialog.getExistingDirectory(self, "اختر مجلد حفظ الصور")
        if not folder:
            return

        if not getattr(self, 'subscription_check', lambda: True)():
            return

        saved = 0
        for i, card in enumerate(self.cards, start=1):
            pix = card.pixmap()
            if pix is None or pix.isNull():
                continue
            base = "هوية_%d" % i
            path = os.path.join(folder, base + ".png")
            n = 2
            while os.path.exists(path):
                path = os.path.join(folder, "%s_%d.png" % (base, n))
                n += 1
            try:
                if pix.save(path, "PNG"):
                    saved += 1
            except Exception as e:
                logger.warning("فشل حفظ الصورة %d: %s", i, e)
        if saved:
            QMessageBox.information(self, "تم", "حُفظت %d صورة في:\n%s" % (saved, folder))
        else:
            QMessageBox.warning(self, "تنبيه", "لم تُحفظ أي صورة.")







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



            bg_items = [it for it in self.scene.items() if it.data(NO_PRINT_KEY) and it.isVisible()]



            for it in bg_items:



                it.setVisible(False)



            printer = QPrinter()



            try:
                self.scene._printing = True
            except Exception:
                pass
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

                try:
                    self.scene._printing = False
                except Exception:
                    pass



                for it in bg_items:



                    it.setVisible(True)



                for item in selected:



                    item.setSelected(True)



