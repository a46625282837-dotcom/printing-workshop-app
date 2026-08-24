import logging
import io
import numpy as np
from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QVBoxLayout,
                               QPushButton, QWidget, QHBoxLayout, QFileDialog,
                               QMessageBox, QLabel, QGraphicsPixmapItem,
                               QGraphicsItem, QGraphicsRectItem, QDialog, QStyle,
                               QMenu, QInputDialog, QComboBox, QProgressBar,
                               QSlider, QSplitter, QGroupBox, QGridLayout)
from PySide6.QtCore import Qt, Signal, QRectF, QSettings, QThread, QEvent, QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QPixmap, QImage, QPen, QColor, QBrush, QPainter, QShortcut, QKeySequence, QPainterPath, QAction, QPageSize
from PySide6.QtPrintSupport import QPrinter
from PIL import Image
from core.printer import print_scene, get_selected_printer_name, set_printer_name, set_last_paper_type
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
        self._original_pixmap = pixmap
        self._crop_rect = None
        self._adjust_settings = {}
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

    def mouseDoubleClickEvent(self, event):
        dialog = PhotoCropDialog(
            self._original_pixmap, None,
            slot_size=(self._pw, self._ph),
            crop_rect=self._crop_rect,
            saved_settings=self._adjust_settings,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        result, settings = dialog.result_pixmap, dialog.strength_value
        if result is not None:
            self._crop_rect = dialog.crop_rect
            self._adjust_settings = dialog.current_settings()
            self.setPixmap(result)
            self.setSelected(False)
            self._rotation = 0
            logger.info("تم تحسين الصورة: %s",
                         {k: v for k, v in settings.items() if v > 0} if isinstance(settings, dict) else settings)


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


    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)


class _SliderGroup(QWidget):
    """A labeled slider with value display and range 0-100."""
    valueChanged = Signal(int)
    def __init__(self, label, default=0, parent=None):
        super().__init__(parent)
        self.slider = QSlider(Qt.Vertical)
        self.slider.setRange(0, 100)
        self.slider.setValue(default)
        self.slider.installEventFilter(self)
        self.lbl_value = QLabel(f"{default}")
        self.lbl_value.setFixedWidth(28)
        self.lbl_value.setAlignment(Qt.AlignCenter)
        lbl = QLabel(label)
        lbl.setStyleSheet("color: #ccc; font-size: 11px;")
        col = QVBoxLayout(self)
        col.setContentsMargins(2, 2, 2, 2)
        col.setSpacing(2)
        hdr = QHBoxLayout()
        hdr.addWidget(lbl)
        hdr.addStretch()
        hdr.addWidget(self.lbl_value)
        col.addLayout(hdr)
        col.addWidget(self.slider, 1)
        self.slider.valueChanged.connect(self.lbl_value.setNum)
        self.slider.valueChanged.connect(self.valueChanged)

    def eventFilter(self, obj, event):
        if obj is self.slider and event.type() == QEvent.MouseButtonDblClick:
            self.slider.setValue(0)
            return True
        return super().eventFilter(obj, event)


class _ControlPanel(QWidget):
    anyValueChanged = Signal()
    autoEnhanceRequested = Signal()
    TOOLTIPS = {
        'skin_smooth': 'تمليس البشرة وتقليل المسام',
        'blemish': 'إزالة البثور والعيوب الصغيرة',
        'brightness': 'زيادة أو تقليل سطوع الوجه والرقبة',
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(280)
        self._groups = {}
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        grp = QGroupBox("التنعيم والتوضيح")
        grp.setStyleSheet("QGroupBox{color:#1a73e8;font-weight:bold;border:1px solid #333;"
                          "border-radius:6px;margin-top:12px;padding-top:16px;}")
        grid = QGridLayout(grp)
        grid.setVerticalSpacing(10)
        entries = [
            ("تنعيم البشرة", "skin_smooth"),
            ("إزالة العيوب", "blemish"),
            ("سطوع الوجه والرقبة", "brightness"),
        ]
        for i, (label, key) in enumerate(entries):
            w = _SliderGroup(label)
            w.valueChanged.connect(self.anyValueChanged)
            tip = self.TOOLTIPS.get(key)
            if tip:
                w.setToolTip(tip)
                w.slider.setToolTip(tip)
            grid.addWidget(w, i // 2, i % 2)
            self._groups[key] = w
        layout.addWidget(grp)

        btn_row = QHBoxLayout()
        auto_btn = QPushButton("✨ تحسين تلقائي")
        auto_btn.clicked.connect(self._auto_enhance)
        auto_btn.setStyleSheet("background:#e67e22;color:#fff;border-radius:4px;padding:6px 10px;font-weight:bold;")
        btn_row.addWidget(auto_btn)
        reset_btn = QPushButton("إعادة ضبط")
        reset_btn.clicked.connect(self._reset_all)
        reset_btn.setStyleSheet("background:#444;color:#eee;border-radius:4px;padding:6px 10px;")
        btn_row.addWidget(reset_btn)
        layout.addLayout(btn_row)
        layout.addStretch()

    def _reset_all(self):
        for w in self._groups.values():
            w.slider.setValue(0)

    def _auto_enhance(self):
        self.autoEnhanceRequested.emit()

    def settings(self) -> dict:
        return {key: w.slider.value() for key, w in self._groups.items()}



class PhotoCropDialog(QDialog):
    def __init__(self, pixmap: QPixmap, parent=None, slot_size=None, crop_rect=None, saved_settings=None):
        super().__init__(parent)
        self.setWindowTitle("تحسين الصورة")
        self._original = pixmap
        self._current = pixmap
        self._after_pixmap = None
        self.result_pixmap = None
        self.strength_value = 0
        self._slot_size = slot_size
        self._overlay = None
        self._initialized = False
        self.crop_rect = crop_rect
        self._sliders = {}
        self._saved_settings = saved_settings or {}
        self._setup_ui()
        self._restore_settings()
        self._add_shortcuts()

    def _setup_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 4, 8, 4)
        for text, slot in [("تكبير", lambda: self._zoom_step(1.25)),
                           ("تصغير", lambda: self._zoom_step(0.8)),
                           ("ملاءمة", self._zoom_fit)]:
            btn = QPushButton(text)
            btn.setStyleSheet("background:#333;color:#eee;border-radius:4px;padding:4px 10px;")
            btn.clicked.connect(slot)
            toolbar.addWidget(btn)
        toolbar.addStretch()
        self._lbl_size = QLabel(f"{self._original.width()}×{self._original.height()}")
        self._lbl_size.setStyleSheet("color:#888;")
        toolbar.addWidget(self._lbl_size)
        main.addLayout(toolbar)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)

        left_w = QWidget()
        left_layout = QVBoxLayout(left_w)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.scene = QGraphicsScene(self)
        self._pixmap_item = self.scene.addPixmap(self._original)
        self.view = _CropView(self.scene, self)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.view.setDragMode(QGraphicsView.ScrollHandDrag)
        left_layout.addWidget(self.view)
        self._overlay = _CropOverlay(self.scene, QRectF(self._pixmap_item.pos(), self._pixmap_item.boundingRect().size()))
        if self.crop_rect:
            self._overlay.set_rect(QRectF(*self.crop_rect))
        splitter.addWidget(left_w)

        right_w = QWidget()
        right_w.setFixedWidth(220)
        right_layout = QVBoxLayout(right_w)
        right_layout.setContentsMargins(10, 10, 10, 10)
        right_layout.setSpacing(6)

        lbl_title = QLabel("إعدادات التعديل")
        lbl_title.setStyleSheet("font-size:14px;font-weight:bold;color:#1a73e8;")
        lbl_title.setAlignment(Qt.AlignCenter)
        right_layout.addWidget(lbl_title)

        self._sliders = {}
        slider_defs = [
            ("تنعيم البشرة", "skin_smooth", -100, 100, 0),
            ("إزالة العيوب", "blemish", -100, 100, 0),
            ("تقليل الظلال", "shadow", -100, 100, 0),
            ("السطوع", "brightness", -100, 100, 0),
            ("التباين", "contrast", -100, 100, 0),
            ("التشبع", "saturation", -100, 100, 0),
            ("الشحذ", "sharpness", -100, 100, 0),
        ]

        for label, key, min_v, max_v, default in slider_defs:
            sl = self._make_slider_group(label, min_v, max_v, default)
            right_layout.addWidget(sl)
            self._sliders[key] = sl

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(6)
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet(
            "QProgressBar{background:#eee;border:none;border-radius:3px;}"
            "QProgressBar::chunk{background:#e67e22;border-radius:3px;}"
        )
        self._progress.hide()
        right_layout.addWidget(self._progress)

        self._lbl_status = QLabel("")
        self._lbl_status.setStyleSheet("color:#888;font-size:11px;")
        self._lbl_status.setAlignment(Qt.AlignCenter)
        self._lbl_status.setWordWrap(True)
        right_layout.addWidget(self._lbl_status)

        btn_reset = QPushButton("إعادة الصورة الأصلية")
        btn_reset.setStyleSheet(
            "QPushButton{background:#555;color:#eee;border:none;"
            "border-radius:4px;padding:6px;}"
            "QPushButton:hover{background:#777;}"
        )
        btn_reset.clicked.connect(self._reset_to_original)
        right_layout.addWidget(btn_reset)

        right_layout.addStretch()

        splitter.addWidget(right_w)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        main.addWidget(splitter)

        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(8, 6, 8, 6)
        for text, slot, style in [
            ("تطبيق", self._apply, "background:#0d904f;color:#fff;border-radius:4px;padding:6px 20px;"),
            ("إلغاء", self.reject, "background:#555;color:#eee;border-radius:4px;padding:6px 20px;"),
        ]:
            btn = QPushButton(text)
            btn.setStyleSheet(style)
            btn.clicked.connect(slot)
            btn_bar.addWidget(btn)
        main.addLayout(btn_bar)

        self.resize(820, 640)
        self.setMinimumSize(540, 420)

    def _add_shortcuts(self):
        QShortcut(QKeySequence("Return"), self, self._apply)
        QShortcut(QKeySequence("Enter"), self, self._apply)
        QShortcut(QKeySequence("Escape"), self, self.reject)

    def _restore_settings(self):
        for key, val in self._saved_settings.items():
            if key in self._sliders:
                self._sliders[key].slider.setValue(val)
        if self._saved_settings:
            self._apply_adjustments()

    def current_settings(self) -> dict:
        return {k: s.slider.value() for k, s in self._sliders.items()}

    def showEvent(self, event):
        super().showEvent(event)
        if not self._initialized:
            self._initialized = True
            self._zoom_fit()

    def _zoom_step(self, factor):
        self.view.scale(factor, factor)

    def _zoom_fit(self):
        self.view.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)

    def _get_crop_rect(self):
        if not self._overlay:
            return None
        r = self._overlay.rect()
        if r.width() < 2 or r.height() < 2:
            return None
        return r.toRect()

    def _make_slider_group(self, label_text, min_v, max_v, default):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lbl = QLabel(label_text)
        lbl.setStyleSheet("font-size:11px;color:#ccc;")
        lay.addWidget(lbl)
        row = QHBoxLayout()
        row.setSpacing(2)
        btn_left = QPushButton("\u25c0")
        btn_left.setFixedSize(22, 22)
        btn_left.setStyleSheet(
            "QPushButton{background:#444;color:#ccc;border:none;border-radius:3px;font-size:10px;}"
            "QPushButton:hover{background:#666;color:white;}"
        )
        slider = QSlider(Qt.Horizontal)
        slider.setRange(min_v, max_v)
        slider.setValue(default)
        slider.valueChanged.connect(self._on_slider_changed)
        btn_right = QPushButton("\u25b6")
        btn_right.setFixedSize(22, 22)
        btn_right.setStyleSheet(
            "QPushButton{background:#444;color:#ccc;border:none;border-radius:3px;font-size:10px;}"
            "QPushButton:hover{background:#666;color:white;}"
        )
        val_lbl = QLabel(str(default))
        val_lbl.setFixedWidth(30)
        val_lbl.setAlignment(Qt.AlignCenter)
        val_lbl.setStyleSheet("font-size:11px;color:#aaa;")
        slider.valueChanged.connect(lambda v, lbl=val_lbl: lbl.setText(str(v)))
        btn_left.clicked.connect(lambda: slider.setValue(slider.value() - 1))
        btn_right.clicked.connect(lambda: slider.setValue(slider.value() + 1))
        row.addWidget(btn_left)
        row.addWidget(slider, 1)
        row.addWidget(btn_right)
        row.addWidget(val_lbl)
        lay.addLayout(row)
        w.slider = slider
        return w

    def _on_slider_changed(self, _):
        from PySide6.QtCore import QTimer
        if not hasattr(self, '_debounce_timer'):
            self._debounce_timer = QTimer(self)
            self._debounce_timer.setSingleShot(True)
            self._debounce_timer.setInterval(50)
            self._debounce_timer.timeout.connect(self._apply_adjustments)
        self._debounce_timer.start()

    def _reset_to_original(self):
        self._current = self._original
        self._orig_bgr = None
        self._cached_face_mask = None
        self.scene.clear()
        self._pixmap_item = self.scene.addPixmap(self._original)
        self._overlay = _CropOverlay(self.scene, QRectF(self._pixmap_item.pos(), self._pixmap_item.boundingRect().size()))
        if self.crop_rect:
            self._overlay.set_rect(QRectF(*self.crop_rect))
        self._zoom_fit()
        for sl in self._sliders.values():
            sl.slider.setValue(0)
        self._lbl_status.setText("تمت إعادة الصورة الأصلية")

    def _apply_adjustments(self):
        import cv2
        try:
            self._do_adjustments(cv2)
        except Exception as e:
            logger.error("فشل تطبيق التعديلات: %s", e, exc_info=True)

    def _do_adjustments(self, cv2):
        vals = {k: s.slider.value() for k, s in self._sliders.items()}
        if all(v == 0 for v in vals.values()):
            self._current = self._original
            self._save_crop_from_overlay()
            self.scene.clear()
            self._pixmap_item = self.scene.addPixmap(self._original)
            self._overlay = _CropOverlay(self.scene, QRectF(self._pixmap_item.pos(), self._pixmap_item.boundingRect().size()))
            if self.crop_rect:
                self._overlay.set_rect(QRectF(*self.crop_rect))
            self._zoom_fit()
            return

        if not hasattr(self, '_orig_bgr') or self._orig_bgr is None:
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.WriteOnly)
            self._original.toImage().save(buf, "PNG")
            buf.close()
            raw = bytes(ba.data())
            arr = np.frombuffer(raw, np.uint8)
            self._orig_bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if self._orig_bgr is None or len(self._orig_bgr.shape) < 3:
                logger.error("فشل استخراج الصورة: shape=%s",
                             getattr(self._orig_bgr, 'shape', None))
                return
            if self._orig_bgr.shape[2] == 4:
                self._orig_bgr = cv2.cvtColor(self._orig_bgr, cv2.COLOR_BGRA2BGR)
            logger.info("_orig_bgr OK: shape=%s dtype=%s", self._orig_bgr.shape, self._orig_bgr.dtype)

        img_bgr = self._orig_bgr.copy()

        if vals["brightness"] != 0:
            img_bgr = cv2.convertScaleAbs(img_bgr, alpha=1.0, beta=vals["brightness"] * 0.8)

        if vals["contrast"] != 0:
            f = 1.0 + vals["contrast"] / 100.0
            img_bgr = cv2.convertScaleAbs(img_bgr, alpha=f, beta=0)

        if vals["saturation"] != 0:
            hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
            factor = 1.0 + vals["saturation"] / 100.0
            hsv[:, :, 1] = np.clip(hsv[:, :, 1] * factor, 0, 255)
            img_bgr = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

        if vals["sharpness"] != 0:
            amt = vals["sharpness"] / 100.0
            if amt > 0:
                blurred = cv2.GaussianBlur(img_bgr, (0, 0), 3)
                img_bgr = cv2.addWeighted(img_bgr, 1 + amt, blurred, -amt, 0)
            else:
                ksize = max(3, int(abs(amt) * 10) + 1)
                if ksize % 2 == 0:
                    ksize += 1
                img_bgr = cv2.GaussianBlur(img_bgr, (ksize, ksize), 0)

        skin_strength = vals["skin_smooth"] / 100.0
        blemish_strength = vals["blemish"] / 100.0
        shadow_strength = vals["shadow"] / 100.0

        if skin_strength != 0 or blemish_strength != 0 or shadow_strength != 0:
            if not hasattr(self, '_cached_face_mask') or self._cached_face_mask is None:
                self._cached_face_mask = self._detect_face_mask_cv(self._orig_bgr)
            face_mask = self._cached_face_mask
            if face_mask is None:
                h, w = img_bgr.shape[:2]
                face_mask = np.full((h, w), 255, dtype=np.uint8)
            if blemish_strength != 0:
                img_bgr = self._remove_blemishes_cv(img_bgr, face_mask, blemish_strength)
            if shadow_strength != 0:
                img_bgr = self._reduce_shadows_cv(img_bgr, face_mask, shadow_strength)
            if skin_strength != 0:
                img_bgr = self._smooth_skin_cv(img_bgr, face_mask, skin_strength)

        rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        bpl = ch * w
        qimg_out = QImage(rgb.data, w, h, bpl, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg_out.copy())
        if pixmap.isNull():
            _, png = cv2.imencode('.png', rgb)
            pixmap = QPixmap()
            pixmap.loadFromData(png.tobytes(), "PNG")
        self._current = pixmap
        self._save_crop_from_overlay()
        self.scene.clear()
        self._pixmap_item = self.scene.addPixmap(self._current)
        self._overlay = _CropOverlay(self.scene, QRectF(self._pixmap_item.pos(), self._pixmap_item.boundingRect().size()))
        if self.crop_rect:
            self._overlay.set_rect(QRectF(*self.crop_rect))
        self._zoom_fit()

    def _save_crop_from_overlay(self):
        if self._overlay:
            r = self._overlay.rect()
            if r.width() > 2 and r.height() > 2:
                self.crop_rect = (int(r.x()), int(r.y()), int(r.width()), int(r.height()))

    def _update_scene(self):
        self.scene.clear()
        self._pixmap_item = self.scene.addPixmap(self._current)
        self._overlay = _CropOverlay(self.scene, QRectF(self._pixmap_item.pos(), self._pixmap_item.boundingRect().size()))
        if self.crop_rect:
            self._overlay.set_rect(QRectF(*self.crop_rect))
        self._zoom_fit()

    def _detect_face_mask_cv(self, bgr):
        import cv2
        try:
            import mediapipe as mp
            mesh = mp.solutions.face_mesh.FaceMesh(
                static_image_mode=True, max_num_faces=5, min_detection_confidence=0.3
            )
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            h, w = rgb.shape[:2]
            results = mesh.process(rgb)
            if results.multi_face_landmarks:
                mask = np.zeros((h, w), dtype=np.uint8)
                oval = [10, 338, 297, 332, 284, 251, 389, 356, 454, 323,
                        361, 288, 397, 365, 379, 378, 400, 377, 152, 148,
                        176, 149, 150, 136, 172, 58, 132, 93, 234, 127,
                        162, 21, 54, 103, 67, 109]
                for fl in results.multi_face_landmarks:
                    pts = [[int(fl.landmark[i].x * w), int(fl.landmark[i].y * h)] for i in oval]
                    cv2.fillConvexPoly(mask, np.array(pts), 255)
                mask = cv2.GaussianBlur(mask, (21, 21), 0)
                return mask
        except Exception:
            pass
        return None

    def _smooth_skin_cv(self, img, mask, strength):
        import cv2
        strength = min(abs(strength), 1.0)
        smoothed = cv2.bilateralFilter(img, 7, 40, 40)
        mask_f = cv2.GaussianBlur(mask.astype(np.float32) / 255.0, (15, 15), 0)
        mask_f = np.clip(mask_f * strength, 0, 1)[:, :, np.newaxis]
        return np.clip(img.astype(np.float32) * (1 - mask_f) + smoothed.astype(np.float32) * mask_f, 0, 255).astype(np.uint8)

    def _remove_blemishes_cv(self, img, mask, strength):
        import cv2
        strength = min(abs(strength), 1.0)
        threshold = 8 - strength * 5
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blurred_large = cv2.GaussianBlur(gray.astype(np.float32), (31, 31), 0)
        diff = blurred_large - gray.astype(np.float32)
        dark_spots = (diff > threshold).astype(np.uint8) * 255
        dark_spots = cv2.bitwise_and(dark_spots, mask)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        dark_spots = cv2.morphologyEx(dark_spots, cv2.MORPH_OPEN, k)
        dark_spots = cv2.morphologyEx(dark_spots, cv2.MORPH_CLOSE, k)
        nlabels, labels, stats, _ = cv2.connectedComponentsWithStats(dark_spots, connectivity=8)
        for i in range(1, nlabels):
            area = stats[i, cv2.CC_STAT_AREA]
            if area < 3 or area > 800:
                continue
            x = stats[i, cv2.CC_STAT_LEFT]
            y = stats[i, cv2.CC_STAT_TOP]
            w_cc = stats[i, cv2.CC_STAT_WIDTH]
            h_cc = stats[i, cv2.CC_STAT_HEIGHT]
            pad = max(w_cc, h_cc) // 2 + 4
            y1, y2 = max(0, y - pad), min(img.shape[0], y + h_cc + pad)
            x1, x2 = max(0, x - pad), min(img.shape[1], x + w_cc + pad)
            roi = img[y1:y2, x1:x2]
            if roi.size == 0:
                continue
            roi_blur = cv2.GaussianBlur(roi, (9, 9), 0)
            cmask = (labels[y1:y2, x1:x2] == i).astype(np.float32)
            cmask = cv2.GaussianBlur(cmask, (9, 9), 0)[:, :, np.newaxis]
            img[y1:y2, x1:x2] = np.clip(roi.astype(np.float32) * (1 - cmask) + roi_blur.astype(np.float32) * cmask, 0, 255).astype(np.uint8)
        return img

    def _reduce_shadows_cv(self, img, mask, strength):
        import cv2
        strength = min(abs(strength), 1.0)
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        l_f = l.astype(np.float32)
        face_f = cv2.GaussianBlur(mask.astype(np.float32), (31, 31), 0) / 255.0
        local_mean = cv2.GaussianBlur(l_f, (51, 51), 0)
        shadow_map = np.clip(local_mean - l_f, 0, 255)
        shadow_mask = np.clip(shadow_map / 30.0, 0, 1.0) * face_f * strength
        lift = 25 * strength
        l_final = np.clip(l_f + shadow_mask * lift, 0, 255)
        lab = cv2.merge([l_final.astype(np.uint8), a, b])
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    def _apply(self):
        cr = self._get_crop_rect()
        if cr is not None:
            rx = max(0, cr.x())
            ry = max(0, cr.y())
            rw = min(cr.width(), self._current.width() - rx)
            rh = min(cr.height(), self._current.height() - ry)
            if rw > 2 and rh > 2:
                self.result_pixmap = self._current.copy(int(rx), int(ry), int(rw), int(rh))
                self.crop_rect = (int(rx), int(ry), int(rw), int(rh))
            else:
                self.result_pixmap = self._current
        else:
            self.result_pixmap = self._current
        self.strength_value = 0
        self.accept()


class _CropOverlay:
    EDGE_NONE = 0
    EDGE_TOP = 1
    EDGE_BOTTOM = 2
    EDGE_LEFT = 4
    EDGE_RIGHT = 8

    def __init__(self, scene, image_rect):
        self.scene = scene
        self.image_rect = image_rect
        self._rect = QRectF(image_rect)
        self._min_size = 20

        self._border = QGraphicsRectItem()
        self._border.setPen(QPen(QColor("#1a73e8"), 2.5))
        self._border.setZValue(10)
        scene.addItem(self._border)

        self._dim_items = []
        for _ in range(4):
            item = QGraphicsRectItem()
            item.setBrush(QBrush(QColor(0, 0, 0, 160)))
            item.setPen(QPen(Qt.NoPen))
            item.setZValue(9)
            scene.addItem(item)
            self._dim_items.append(item)

        self._handles = []
        for _ in range(4):
            h = QGraphicsRectItem(-5, -5, 10, 10)
            h.setBrush(QBrush(QColor("#1a73e8")))
            h.setPen(QPen(Qt.white, 1.5))
            h.setZValue(11)
            scene.addItem(h)
            self._handles.append(h)

        self._update()

    def _update(self):
        self._border.setRect(self._rect)
        r = self._rect
        ir = self.image_rect
        self._dim_items[0].setRect(QRectF(ir.x(), ir.y(), ir.width(), max(0, r.y() - ir.y())))
        self._dim_items[1].setRect(QRectF(ir.x(), r.bottom(), ir.width(), max(0, ir.bottom() - r.bottom())))
        self._dim_items[2].setRect(QRectF(ir.x(), r.y(), max(0, r.x() - ir.x()), r.height()))
        self._dim_items[3].setRect(QRectF(r.right(), r.y(), max(0, ir.right() - r.right()), r.height()))
        cx, cy = r.center().x(), r.center().y()
        self._handles[0].setPos(cx, r.top())
        self._handles[1].setPos(cx, r.bottom())
        self._handles[2].setPos(r.left(), cy)
        self._handles[3].setPos(r.right(), cy)

    def set_rect(self, rect):
        r = rect.normalized().intersected(self.image_rect)
        if r.width() < self._min_size or r.height() < self._min_size:
            return
        self._rect = r
        self._update()

    def rect(self):
        return QRectF(self._rect)

    def hit_test(self, scene_pos):
        r = self._rect
        margin = 8
        x, y = scene_pos.x(), scene_pos.y()
        edges = 0
        if abs(y - r.top()) <= margin and r.left() <= x <= r.right():
            edges |= self.EDGE_TOP
        if abs(y - r.bottom()) <= margin and r.left() <= x <= r.right():
            edges |= self.EDGE_BOTTOM
        if abs(x - r.left()) <= margin and r.top() <= y <= r.bottom():
            edges |= self.EDGE_LEFT
        if abs(x - r.right()) <= margin and r.top() <= y <= r.bottom():
            edges |= self.EDGE_RIGHT
        return edges

    @staticmethod
    def cursor_for_edges(edges):
        TOP = _CropOverlay.EDGE_TOP
        BOTTOM = _CropOverlay.EDGE_BOTTOM
        LEFT = _CropOverlay.EDGE_LEFT
        RIGHT = _CropOverlay.EDGE_RIGHT
        if edges in ((TOP | LEFT), (BOTTOM | RIGHT)):
            return Qt.SizeFDiagCursor
        if edges in ((TOP | RIGHT), (BOTTOM | LEFT)):
            return Qt.SizeBDiagCursor
        if edges & (TOP | BOTTOM):
            return Qt.SizeVerCursor
        if edges & (LEFT | RIGHT):
            return Qt.SizeHorCursor
        return Qt.ArrowCursor


class _CropView(QGraphicsView):
    def __init__(self, scene, dialog):
        super().__init__(scene)
        self._dialog = dialog
        self._drag_edge = 0
        self._drag_rect = None

    def wheelEvent(self, event):
        factor = 1.15 ** (event.angleDelta().y() / 120.0)
        self.scale(factor, factor)

    def mousePressEvent(self, event):
        dlg = self._dialog
        if event.button() == Qt.LeftButton and dlg._overlay:
            scene_pos = self.mapToScene(event.pos())
            edges = dlg._overlay.hit_test(scene_pos)
            if edges:
                self._drag_edge = edges
                self._drag_rect = QRectF(dlg._overlay.rect())
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        dlg = self._dialog
        scene_pos = self.mapToScene(event.pos())

        if self._drag_edge:
            r = QRectF(self._drag_rect)
            if self._drag_edge & _CropOverlay.EDGE_TOP:
                r.setTop(scene_pos.y())
            if self._drag_edge & _CropOverlay.EDGE_BOTTOM:
                r.setBottom(scene_pos.y())
            if self._drag_edge & _CropOverlay.EDGE_LEFT:
                r.setLeft(scene_pos.x())
            if self._drag_edge & _CropOverlay.EDGE_RIGHT:
                r.setRight(scene_pos.x())
            dlg._overlay.set_rect(r)
            return

        if dlg._overlay:
            edges = dlg._overlay.hit_test(scene_pos)
            self.setCursor(_CropOverlay.cursor_for_edges(edges))
        else:
            self.setCursor(Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_edge:
            self._drag_edge = 0
            self._drag_rect = None
            return
        super().mouseReleaseEvent(event)


class PhotoProcessingThread(QThread):
    photo_ready = Signal(bytes, int)
    finished_all = Signal()

    def __init__(self, paths_or_bytes, start_index=0, parent=None):
        super().__init__(parent)
        self._items = paths_or_bytes
        self._start_index = start_index

    MAX_PROCESS_PX = 1200

    def run(self):
        from core.photo_processor import remove_background, auto_crop_subject, composite_white_bg
        for i, pb in enumerate(self._items):
            try:
                if isinstance(pb, str):
                    pil = Image.open(pb)
                else:
                    pil = Image.open(io.BytesIO(pb))
                pil = pil.convert("RGB")
                if max(pil.size) > self.MAX_PROCESS_PX:
                    pil.thumbnail((self.MAX_PROCESS_PX, self.MAX_PROCESS_PX), Image.LANCZOS)
                    logger.info("تصغير الصورة %d إلى %s للمعالجة", i, pil.size)
                no_bg = remove_background(pil)
                no_bg = auto_crop_subject(no_bg)
                no_bg = composite_white_bg(no_bg)
                buf = io.BytesIO()
                no_bg.save(buf, format="PNG")
                self.photo_ready.emit(buf.getvalue(), self._start_index + i)
            except Exception as e:
                logger.error("فشل معالجة الصورة %d: %s", i, e)
                try:
                    if isinstance(pb, str):
                        fallback = Image.open(pb)
                    else:
                        fallback = Image.open(io.BytesIO(pb))
                    fallback = fallback.convert("RGB")
                    buf = io.BytesIO()
                    fallback.save(buf, format="PNG")
                    self.photo_ready.emit(buf.getvalue(), self._start_index + i)
                except Exception as e2:
                    logger.error("فشل عرض الصورة %d حتى بدون معالجة: %s", i, e2)
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
        btn_save_pdf = QPushButton("حفظ PDF")
        btn_save_pdf.clicked.connect(self._save_pdf)
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
        btn_bar.addWidget(btn_save_pdf)
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
        self._progress_bar.setFixedHeight(20)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setStyleSheet("""
            QProgressBar { background: #eee; border: none; border-radius: 4px; text-align: center; font-size: 11px; }
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
        if hasattr(self, '_thread') and self._thread is not None and self._thread.isRunning():
            try:
                self._thread.photo_ready.disconnect()
                self._thread.finished_all.disconnect()
            except RuntimeError:
                pass
        total = len(paths_or_bytes_list)
        self._progress_total = total
        self._progress_count = 0
        self._progress_bar.setRange(0, total)
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat(f"0/{total}")
        self._progress_bar.show()
        old_len = len(self.photos)
        for pb in paths_or_bytes_list:
            try:
                if isinstance(pb, str):
                    orig = Image.open(pb)
                else:
                    orig = Image.open(io.BytesIO(pb))
                orig = orig.convert("RGB")
                buf = io.BytesIO()
                orig.save(buf, format="PNG")
                qpix = QPixmap()
                qpix.loadFromData(buf.getvalue())
                if not qpix.isNull():
                    self._place_photo(qpix)
            except Exception as e:
                logger.error("فشل عرض الصورة الأصلية: %s", e)
        self._thread = PhotoProcessingThread(list(paths_or_bytes_list), start_index=old_len)
        self._thread.photo_ready.connect(self._on_photo_ready)
        self._thread.finished_all.connect(self._on_processing_done)
        self._thread.started.connect(lambda: logger.info("بدأت معالجة %d صور", total))
        self._thread.start()

    def _on_photo_ready(self, data, index):
        qpix = QPixmap()
        qpix.loadFromData(data)
        if not qpix.isNull() and index < len(self.photos):
            item = self.photos[index]
            item.setPixmap(qpix)
            item._original_pixmap = qpix
        self._progress_count += 1
        self._progress_bar.setValue(self._progress_count)
        self._progress_bar.setFormat(f"{self._progress_count}/{self._progress_total}")

    def _on_processing_done(self):
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setFormat("")
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

    def _save_pdf(self):
        if not self.photos:
            QMessageBox.information(self, "تنبيه", "لا توجد صور للحفظ.")
            return
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
            painter = QPainter(printer)
            if not painter.isActive():
                logger.error("فشل بدء رسم الحفظ PDF")
                QMessageBox.warning(self, "خطأ", "فشل بدء رسم الحفظ PDF")
                for it in bg_items:
                    it.setVisible(True)
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
                logger.info("تم حفظ PDF: %s", path)
                QMessageBox.information(self, "تم", f"تم حفظ الملف:\n{path}")
            except Exception as e:
                logger.error("فشل حفظ PDF", exc_info=True)
                QMessageBox.warning(self, "خطأ", f"فشل حفظ PDF:\n{e}")
            finally:
                painter.end()
                for it in bg_items:
                    it.setVisible(True)
            for item in selected:
                item.setSelected(True)

    def _print_page(self):
        if not self.photos:
            QMessageBox.information(self, "تنبيه", "لا توجد صور للطباعة.")
            return
        if getattr(self, 'subscription_check', lambda: True)():
            dialog = PrintSetupDialog(self, page_count=self._num_pages, default_paper_type="ورق صور لامع 180 غ")
            if dialog.exec() != QDialog.Accepted:
                return
            printer_name = dialog.selected_printer()
            copies = dialog.copies()
            duplex = dialog.duplex()
            if printer_name:
                set_printer_name(printer_name)
            pt = dialog.paper_type()
            if pt:
                set_last_paper_type(pt)
            logger.info("طلب طباعة %d نسخ من %d صفحة%s على: %s",
                        copies, self._num_pages, " (وجهين)" if duplex else "", printer_name)
            selected = self.scene.selectedItems()
            for item in selected:
                item.setSelected(False)
            print_scene(self, self.scene, copies=copies, page_count=self._num_pages, duplex=duplex,
                        page_range=dialog.page_range(), paper_type=pt)
            for item in selected:
                item.setSelected(True)
