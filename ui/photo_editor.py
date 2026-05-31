import logging
import io
from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QVBoxLayout,
                               QPushButton, QWidget, QHBoxLayout, QFileDialog,
                               QMessageBox, QLabel, QGraphicsPixmapItem,
                               QGraphicsItem, QDialog, QStyle,
                               QMenu, QInputDialog, QComboBox, QProgressBar)
from PySide6.QtCore import Qt, Signal, QRectF, QSettings, QThread
from PySide6.QtGui import QPixmap, QImage, QPen, QColor, QBrush, QPainter, QShortcut, QKeySequence, QPainterPath, QAction
from PIL import Image
from core.printer import print_scene, get_selected_printer_name, set_printer_name
from ui.a4_editor import PrintSetupDialog, A4_W, A4_H, MARGIN

logger = logging.getLogger(__name__)

PHOTO_SIZES = {
    "طول 4.5 سم وعرض 3.5 سم": (35, 45),
    "طول 4 سم وعرض 3 سم": (30, 40),
}
GAP = 5


class PhotoItem(QGraphicsPixmapItem):
    def __init__(self, pixmap, pw, ph, index=0, parent=None):
        super().__init__(pixmap, parent)
        self.index = index
        self._pw = pw
        self._ph = ph
        self._rotation = 0
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)

    def item_rotation(self):
        return self._rotation

    def set_item_rotation(self, angle):
        self._rotation = angle % 360
        self.update()

    def boundingRect(self):
        return QRectF(0, 0, self._pw, self._ph)

    def shape(self):
        path = QPainterPath()
        path.addRect(QRectF(0, 0, self._pw, self._ph))
        return path

    def paint(self, painter, option, widget):
        source = self.pixmap()
        if not source.isNull():
            painter.save()
            painter.translate(self._pw / 2, self._ph / 2)
            if self._rotation:
                painter.rotate(self._rotation)
            if self._rotation in (90, 270):
                scale = min(self._ph / source.width(), self._pw / source.height(), 1.0)
            else:
                scale = min(self._pw / source.width(), self._ph / source.height(), 1.0)
            dw = source.width() * scale
            dh = source.height() * scale
            painter.drawPixmap(QRectF(-dw / 2, -dh / 2, dw, dh), source, source.rect())
            painter.restore()
        selected = bool(option.state & QStyle.State_Selected)
        if selected:
            painter.setPen(QPen(QColor("#1a73e8"), 4))
            painter.setBrush(QBrush(QColor(26, 115, 232, 20)))
            painter.drawRect(QRectF(1, 1, self._pw - 2, self._ph - 2))

    def mousePressEvent(self, event):
        self.setSelected(True)
        super().mousePressEvent(event)


class PhotoGraphicsView(QGraphicsView):
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
            self.parent().add_images(paths)
        event.acceptProposedAction()

    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos())
        if not item or not isinstance(item, PhotoItem):
            super().contextMenuEvent(event)
            return
        menu = QMenu()
        act_del = menu.addAction("حذف")
        act_dup = menu.addAction("تكرار...")
        action = menu.exec(event.globalPos())
        if action == act_del:
            editor = self.parent()
            editor._push_undo()
            editor._remove_photo(item)
        elif action == act_dup:
            count, ok = QInputDialog.getInt(self, "تكرار الصورة", "عدد مرات التكرار:", 2, 1, 999)
            if ok:
                pixmap = item.pixmap()
                for _ in range(count):
                    self.parent()._place_photo(QPixmap(pixmap))


class PhotoProcessingThread(QThread):
    photo_ready = Signal(object, int)
    finished_all = Signal()

    def __init__(self, paths_or_bytes, parent=None):
        super().__init__(parent)
        self._items = paths_or_bytes

    def run(self):
        from core.photo_processor import remove_background, auto_crop_subject
        for i, pb in enumerate(self._items):
            try:
                if isinstance(pb, str):
                    pil = Image.open(pb)
                else:
                    pil = Image.open(io.BytesIO(pb))
                pil = pil.convert("RGB")
                no_bg = remove_background(pil)
                no_bg = auto_crop_subject(no_bg)
                buf = io.BytesIO()
                no_bg.save(buf, format="PNG")
                buf.seek(0)
                qpix = QPixmap()
                qpix.loadFromData(buf.getvalue())
                self.photo_ready.emit(qpix, i)
            except Exception as e:
                logger.error("فشل معالجة الصورة %d: %s", i, e)
        self.finished_all.emit()


class PhotoEditor(QWidget):
    go_back = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayoutDirection(Qt.RightToLeft)
        self.photos = []
        self._num_pages = 1
        self._size_key = "طول 4.5 سم وعرض 3.5 سم"
        self._copied_pixmap = None
        self._undo_stack = []
        self._build_ui()
        self._add_shortcuts()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        btn_bar = QHBoxLayout()
        btn_back = QPushButton("↩ رجوع")
        btn_back.clicked.connect(self.go_back.emit)
        btn_add = QPushButton("+ إضافة صورة شخصية")
        btn_add.clicked.connect(self._add_image_dialog)
        btn_print = QPushButton("طباعة")
        btn_print.clicked.connect(self._print_page)
        btn_clear = QPushButton("تفريغ الكل")
        btn_clear.clicked.connect(self._clear_all)

        self._size_combo = QComboBox()
        self._size_combo.addItem("الحجم")
        for key in PHOTO_SIZES:
            self._size_combo.addItem(key)
        self._size_combo.activated.connect(self._on_size_activated)

        btn_bar.addWidget(btn_back)
        btn_bar.addWidget(btn_add)
        btn_bar.addWidget(btn_print)
        btn_bar.addWidget(btn_clear)
        btn_add_page = QPushButton("➕ إضافة صفحة")
        btn_add_page.setToolTip("أضف صفحة A4 فارغة جديدة")
        btn_add_page.clicked.connect(self._add_blank_page)
        btn_del_page = QPushButton("➖ حذف صفحة")
        btn_del_page.setToolTip("احذف آخر صفحة فارغة")
        btn_del_page.clicked.connect(self._delete_last_page)
        btn_bar.addStretch()
        btn_bar.addWidget(btn_del_page)
        btn_bar.addWidget(btn_add_page)
        btn_zoom_in = QPushButton("🔍+")
        btn_zoom_in.setToolTip("تكبير")
        btn_zoom_in.clicked.connect(lambda: (self.view.scale(1.2, 1.2), self._save_zoom()))
        btn_zoom_out = QPushButton("🔍-")
        btn_zoom_out.setToolTip("تصغير")
        btn_zoom_out.clicked.connect(lambda: (self.view.scale(1 / 1.2, 1 / 1.2), self._save_zoom()))
        btn_zoom_fit = QPushButton("⌖")
        btn_zoom_fit.setToolTip("ملاءمة الشاشة")
        btn_zoom_fit.clicked.connect(self._zoom_fit)
        btn_bar.addWidget(btn_zoom_fit)
        btn_bar.addWidget(btn_zoom_out)
        btn_bar.addWidget(btn_zoom_in)
        btn_bar.addWidget(self._size_combo)
        layout.addLayout(btn_bar)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setFixedHeight(8)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setStyleSheet("""
            QProgressBar { background: #eee; border: none; border-radius: 4px; }
            QProgressBar::chunk { background: #e67e22; border-radius: 4px; }
        """)
        self._progress_bar.hide()
        layout.addWidget(self._progress_bar)

        self.scene = QGraphicsScene(self)
        self.scene.setSceneRect(0, 0, A4_W, A4_H)
        self._draw_page_area(0)
        self.view = PhotoGraphicsView(self.scene, self)
        layout.addWidget(self.view)
        self._apply_default_zoom()

    def _apply_default_zoom(self):
        settings = QSettings("ورشة طباعة", "PhotoEditor")
        zoom = settings.value("defaultZoom", 2.2, type=float)
        self.view.scale(zoom, zoom)
        logger.info("تم تطبيق التكبير الافتراضي: %.2f", zoom)

    def _save_zoom(self):
        t = self.view.transform()
        zoom = t.m11()
        settings = QSettings("ورشة طباعة", "PhotoEditor")
        settings.setValue("defaultZoom", zoom)

    def _zoom_fit(self):
        self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)
        self._save_zoom()

    def _snapshot_state(self):
        photos = []
        for item in self.photos:
            photos.append((item.pixmap().toImage(), item.item_rotation()))
        return (self._size_key, photos)

    def _push_undo(self):
        state = self._snapshot_state()
        self._undo_stack.append(state)
        if len(self._undo_stack) > 15:
            self._undo_stack.pop(0)

    def _undo(self):
        if not self._undo_stack:
            return
        state = self._undo_stack.pop()
        size_key, photos_data = state
        self._size_key = size_key
        self.scene.clear()
        self.photos.clear()
        self._num_pages = 1
        self.scene.setSceneRect(0, 0, A4_W, A4_H)
        self._draw_page_area(0)
        for img, rotation in photos_data:
            self._place_photo(QPixmap.fromImage(img))
            if rotation:
                self.photos[-1].set_item_rotation(rotation)
        logger.info("تم التراجع عن آخر إجراء (%d صورة)", len(self.photos))

    def _on_size_activated(self, index):
        if index == 0:
            self._size_combo.blockSignals(True)
            self._size_combo.setCurrentIndex(0)
            self._size_combo.blockSignals(False)
            return
        key = self._size_combo.itemText(index)
        self._on_size_menu(key)
        self._size_combo.blockSignals(True)
        self._size_combo.setCurrentIndex(0)
        self._size_combo.blockSignals(False)

    def _on_size_menu(self, key):
        if key not in PHOTO_SIZES or key == self._size_key:
            return
        self._push_undo()
        self._on_size_changed(key)

    def _pw(self):
        return PHOTO_SIZES[self._size_key][0]

    def _ph(self):
        return PHOTO_SIZES[self._size_key][1]

    def _cols(self):
        return max(1, (A4_W - 2 * MARGIN + GAP) // (self._pw() + GAP))

    def _rows(self):
        return max(1, (A4_H - 2 * MARGIN + GAP) // (self._ph() + GAP))

    def _per_page(self):
        return self._cols() * self._rows()

    def _draw_page_area(self, page_index):
        y0 = page_index * A4_H
        self.scene.addRect(0, y0, A4_W, A4_H, QPen(Qt.NoPen), QBrush(Qt.white)).setZValue(-1)
        pen = QPen(QColor(200, 200, 200, 60), 0.3, Qt.DashLine)
        pw, ph = self._pw(), self._ph()
        cols = self._cols()
        x = MARGIN
        for col in range(cols):
            y = y0 + MARGIN
            while y + ph <= y0 + A4_H - MARGIN:
                self.scene.addRect(x, y, pw, ph, pen)
                y += ph + GAP
            x += pw + GAP

    def _ensure_page(self, page_index):
        while page_index >= self._num_pages:
            self._draw_page_area(self._num_pages)
            self._num_pages += 1
            self.scene.setSceneRect(0, 0, A4_W, self._num_pages * A4_H)
        return page_index * A4_H

    def _grid_pos(self, idx):
        page_idx = idx // self._per_page()
        local_idx = idx % self._per_page()
        y_offset = page_idx * A4_H
        cols = self._cols()
        col = local_idx % cols
        row = local_idx // cols
        x = MARGIN + col * (self._pw() + GAP)
        y = y_offset + MARGIN + row * (self._ph() + GAP)
        return x, y

    def _place_photo(self, pixmap):
        idx = len(self.photos)
        self._ensure_page(idx // self._per_page())
        pw = self._pw()
        ph = self._ph()
        item = PhotoItem(pixmap, pw, ph, index=idx)
        item.setPos(*self._grid_pos(idx))
        self.scene.addItem(item)
        self.photos.append(item)
        logger.info("تمت إضافة الصورة %d (صفحة %d)", len(self.photos), idx // self._per_page() + 1)

    def _add_image_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "اختر صور شخصية", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.tiff)")
        if not paths:
            return
        self._push_undo()
        self.add_images(paths)

    def add_images(self, paths_or_bytes_list):
        if not paths_or_bytes_list:
            return
        self._progress_bar.show()
        self._thread = PhotoProcessingThread(list(paths_or_bytes_list))
        self._thread.photo_ready.connect(self._on_photo_ready)
        self._thread.finished_all.connect(self._on_processing_done)
        self._thread.start()

    def _on_photo_ready(self, qpix, index):
        if not qpix.isNull():
            self._place_photo(qpix)

    def _on_processing_done(self):
        self._progress_bar.hide()

    def add_image(self, path_or_bytes):
        self.add_images([path_or_bytes])

    def _on_size_changed(self, key):
        if key not in PHOTO_SIZES:
            return
        self._size_key = key
        saved = [(item.pixmap(), item.item_rotation()) for item in self.photos]
        self.scene.clear()
        self.photos.clear()
        self._num_pages = 1
        self.scene.setSceneRect(0, 0, A4_W, A4_H)
        self._draw_page_area(0)
        for pixmap, rotation in saved:
            self._place_photo(pixmap)
            if rotation:
                self.photos[-1].set_item_rotation(rotation)
        logger.info("تم تغيير الحجم إلى %s (عدد الصور %d)", key, len(self.photos))

    def _clear_all(self):
        if not self.photos:
            return
        self._push_undo()
        self.scene.clear()
        self.photos.clear()
        self._num_pages = 1
        self.scene.setSceneRect(0, 0, A4_W, A4_H)
        self._draw_page_area(0)
        logger.info("تم تفريغ جميع الصور")

    def _remove_photo(self, item):
        self.scene.removeItem(item)
        if item in self.photos:
            self.photos.remove(item)
        logger.info("تم حذف الصورة")

    def _delete_selected(self):
        to_remove = [item for item in self.photos if item.isSelected()]
        if not to_remove:
            return
        self._push_undo()
        for item in to_remove:
            self._remove_photo(item)

    def _add_shortcuts(self):
        QShortcut(QKeySequence("Delete"), self, self._delete_selected)
        QShortcut(QKeySequence("Backspace"), self, self._delete_selected)
        QShortcut(QKeySequence("Ctrl+C"), self, self._copy_selected)
        QShortcut(QKeySequence("Ctrl+V"), self, self._paste_copied)
        QShortcut(QKeySequence("Ctrl+R"), self, self._rotate_selected)
        QShortcut(QKeySequence("Ctrl+P"), self, self._print_page)
        QShortcut(QKeySequence("Ctrl+D"), self, self._duplicate_selected)
        QShortcut(QKeySequence("Ctrl+Z"), self, self._undo)

    def _copy_selected(self):
        items = self.scene.selectedItems()
        for item in items:
            if isinstance(item, PhotoItem):
                self._copied_pixmap = item.pixmap()
                logger.info("تم نسخ الصورة")
                return

    def _paste_copied(self):
        if self._copied_pixmap is None or self._copied_pixmap.isNull():
            return
        self._push_undo()
        self._place_photo(QPixmap(self._copied_pixmap))
        self.scene.clearSelection()
        self.photos[-1].setSelected(True)
        self.view.centerOn(self.photos[-1])
        logger.info("تم لصق الصورة")

    def _duplicate_selected(self):
        items = self.scene.selectedItems()
        for item in items:
            if isinstance(item, PhotoItem):
                count, ok = QInputDialog.getInt(self, "تكرار الصورة", "عدد مرات التكرار:", 2, 1, 999)
                if not ok:
                    return
                self._push_undo()
                pixmap = item.pixmap()
                for _ in range(count):
                    self._place_photo(QPixmap(pixmap))
                return

    def _rotate_selected(self):
        items = self.scene.selectedItems()
        if not any(isinstance(item, PhotoItem) for item in items):
            return
        self._push_undo()
        for item in items:
            if isinstance(item, PhotoItem):
                item.set_item_rotation((item.item_rotation() + 90) % 360)
                logger.info("تم تدوير الصورة إلى %.0f°", item.item_rotation())

    def _add_blank_page(self):
        self._ensure_page(self._num_pages)
        logger.info("تمت إضافة صفحة فارغة %d", self._num_pages)

    def _delete_last_page(self):
        if self._num_pages <= 1:
            QMessageBox.information(self, "تنبيه", "لا يمكن حذف الصفحة الوحيدة.")
            return
        last_page = self._num_pages - 1
        for i, c in enumerate(self.photos):
            if i // self._per_page() == last_page:
                QMessageBox.warning(self, "تنبيه",
                    "لا يمكن حذف الصفحة الأخيرة لأنها تحتوي على صور.")
                return
        saved = [(c.pixmap(), c.item_rotation()) for c in self.photos]
        self.scene.clear()
        self.photos.clear()
        self._num_pages -= 1
        h = self._num_pages * A4_H
        self.scene.setSceneRect(0, 0, A4_W, h)
        for p in range(self._num_pages):
            self._draw_page_area(p)
        for pixmap, rotation in saved:
            self._place_photo(QPixmap(pixmap))
            if rotation:
                self.photos[-1].set_item_rotation(rotation)
        logger.info("تم حذف الصفحة %d", last_page + 1)

    def _print_page(self):
        if not self.photos:
            QMessageBox.information(self, "تنبيه", "لا توجد صور للطباعة.")
            return
        if getattr(self, 'subscription_check', lambda: True)():
            dialog = PrintSetupDialog(self, page_count=self._num_pages)
            if dialog.exec() != QDialog.Accepted:
                return
            printer_name = dialog.selected_printer()
            copies = dialog.copies()
            duplex = dialog.duplex()
            if printer_name:
                set_printer_name(printer_name)
            logger.info("طلب طباعة %d نسخ من %d صفحة%s على: %s",
                        copies, self._num_pages, " (وجهين)" if duplex else "", printer_name)
            selected = self.scene.selectedItems()
            for item in selected:
                item.setSelected(False)
            print_scene(self, self.scene, copies=copies, page_count=self._num_pages, duplex=duplex,
                        page_range=dialog.page_range())
            for item in selected:
                item.setSelected(True)
