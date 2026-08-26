import logging
import math

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QFileDialog, QMessageBox, QSlider,
    QGraphicsView, QGraphicsScene, QSizePolicy, QScrollArea,
    QFrame, QSpinBox, QAbstractSpinBox, QGraphicsRectItem,
    QGraphicsPixmapItem, QGraphicsProxyWidget,
)
from PySide6.QtCore import Qt, Signal, QRectF, QPointF, QRect
from PySide6.QtGui import (
    QImage, QPixmap, QPainter, QColor, QPageSize, QPen, QFont,
    QBrush, QCursor, QTransform,
)
from PySide6.QtPrintSupport import QPrinter

from core.scanner import is_available, list_scanners, scan_with_dialog
from core.printer import print_scene, set_printer_name
from ui.a4_editor import PrintSetupDialog

logger = logging.getLogger(__name__)

A4_W = 595
A4_H = 842
MARGIN = 5

_HANDLE_SIZE = 16
_CROP_COLOR = QColor("#FF5722")


class _ScanView(QGraphicsView):
    erase_stroke = Signal(QPointF, float)
    erase_done = Signal()
    erase_started = Signal()

    MODE_NORMAL = 0
    MODE_ERASE = 2

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self._mode = self.MODE_NORMAL
        self._erase_radius = 20
        self._erase_preview = None
        self._erase_active = False
        self._dragging_item = None
        self._drag_offset = QPointF()
        self.setMouseTracking(True)
        self._page_parent = parent

    def set_mode(self, mode):
        self._mode = mode
        self._cleanup()
        if mode == self.MODE_ERASE:
            self.setCursor(Qt.PointingHandCursor)
            self.setDragMode(QGraphicsView.NoDrag)
        else:
            self.setCursor(Qt.ArrowCursor)
            self.setDragMode(QGraphicsView.ScrollHandDrag)

    def _cleanup(self):
        self._erase_active = False
        if self._erase_preview and self._erase_preview.scene():
            self._erase_preview.scene().removeItem(self._erase_preview)
        self._erase_preview = None

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)

        scene_pos = self.mapToScene(event.pos())

        if self._mode == self.MODE_ERASE:
            self._erase_active = True
            self.erase_started.emit()
            self.erase_stroke.emit(scene_pos, self._erase_radius)
            return

        items = self.scene().items(scene_pos)
        for item in items:
            if isinstance(item, QGraphicsPixmapItem) and (item.flags() & QGraphicsPixmapItem.ItemIsMovable):
                self.setDragMode(QGraphicsView.NoDrag)
                self._dragging_item = item
                self._drag_offset = scene_pos - item.pos()
                return

        self._dragging_item = None
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        scene_pos = self.mapToScene(event.pos())

        if self._dragging_item:
            new_pos = scene_pos - self._drag_offset
            new_pos.setX(max(MARGIN, min(new_pos.x(), A4_W - MARGIN - self._dragging_item.pixmap().width())))
            new_pos.setY(max(MARGIN, min(new_pos.y(), A4_H - MARGIN - self._dragging_item.pixmap().height())))
            self._dragging_item.setPos(new_pos)
            return

        if self._mode == self.MODE_ERASE:
            if not self._erase_preview:
                r = self._erase_radius
                self._erase_preview = self.scene().addEllipse(
                    -r, -r, r * 2, r * 2,
                    QPen(_CROP_COLOR, 1.5), QBrush(QColor(255, 87, 34, 30)),
                )
                self._erase_preview.setZValue(100)
            self._erase_preview.setPos(scene_pos)
            if self._erase_active:
                self.erase_stroke.emit(scene_pos, self._erase_radius)
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging_item:
            parent = self._page_parent
            if parent and hasattr(parent, '_current_page'):
                movable = [it for it in self.scene().items()
                            if isinstance(it, QGraphicsPixmapItem) and (it.flags() & QGraphicsPixmapItem.ItemIsMovable)]
                for idx, it in enumerate(movable):
                    parent._img_positions[(parent._current_page, idx)] = it.pos()
            self._dragging_item = None
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            return
        if self._mode == self.MODE_ERASE and self._erase_active:
            self._erase_active = False
            self.erase_done.emit()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        self._erase_active = False
        if self._erase_preview and self._erase_preview.scene():
            self._erase_preview.scene().removeItem(self._erase_preview)
        self._erase_preview = None
        super().leaveEvent(event)

    def wheelEvent(self, event):
        p = self._page_parent
        if p:
            p.wheelEvent(event)


def _layout_images_on_scene(scene, images, page_num, total_pages, multi_scan=False, img_positions=None, img_scales=None, scale_callback=None):
    scene.addRect(0, 0, A4_W, A4_H, QPen(QColor("#cccccc")), QColor("white"))
    aw = A4_W - 2 * MARGIN
    ah = A4_H - 2 * MARGIN
    n = len(images)
    if not multi_scan:
        if images:
            qimg = images[-1]
            if not qimg.isNull():
                pix = QPixmap.fromImage(qimg)
                scaled = pix.scaled(aw, ah, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                x = (A4_W - scaled.width()) / 2
                y = (A4_H - scaled.height()) / 2
                scene.addPixmap(scaled).setPos(x, y)
    else:
        half_w = aw / 2
        for i, qimg in enumerate(images):
            if qimg.isNull():
                continue
            col = i % 2
            x_off = MARGIN + col * half_w
            pix = QPixmap.fromImage(qimg)
            key = (page_num, i)
            scale = 1.0
            if img_scales and key in img_scales:
                scale = img_scales[key]
            scaled_w = max(10, int(half_w * scale))
            scaled_h = max(10, int(ah * scale))
            scaled = pix.scaled(scaled_w, scaled_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            item = scene.addPixmap(scaled)
            item.setFlag(QGraphicsPixmapItem.ItemIsMovable, True)
            item.setFlag(QGraphicsPixmapItem.ItemSendsGeometryChanges, True)
            item.setData(0, key)
            if img_positions and key in img_positions:
                pos = img_positions[key]
                item.setPos(pos)
            else:
                x = x_off + (half_w - scaled.width()) / 2
                y = MARGIN + (ah - scaled.height()) / 2
                item.setPos(x, y)
                if img_positions is not None:
                    img_positions[key] = QPointF(x, y)
            if scale_callback:
                btn_size = 20
                btn_style = (
                    "QPushButton{background:#2196F3;color:white;border:none;"
                    "border-radius:10px;font-weight:bold;font-size:14px;}"
                    "QPushButton:hover{background:#1976D2;}"
                )
                btn_reset_style = (
                    "QPushButton{background:#FF9800;color:white;border:none;"
                    "border-radius:10px;font-weight:bold;font-size:10px;}"
                    "QPushButton:hover{background:#F57C00;}"
                )
                proxy_minus = QGraphicsProxyWidget()
                btn_minus = QPushButton("-")
                btn_minus.setFixedSize(btn_size, btn_size)
                btn_minus.setStyleSheet(btn_style)
                btn_minus.clicked.connect(lambda _, k=key: scale_callback(k, -1))
                proxy_minus.setWidget(btn_minus)
                scene.addItem(proxy_minus)
                bx = item.pos().x() + scaled.width() / 2 - btn_size - 2
                by = item.pos().y() - btn_size - 2
                proxy_minus.setPos(bx, by)

                proxy_plus = QGraphicsProxyWidget()
                btn_plus = QPushButton("+")
                btn_plus.setFixedSize(btn_size, btn_size)
                btn_plus.setStyleSheet(btn_style)
                btn_plus.clicked.connect(lambda _, k=key: scale_callback(k, 1))
                proxy_plus.setWidget(btn_plus)
                scene.addItem(proxy_plus)
                proxy_plus.setPos(bx + btn_size + 4, by)

                proxy_reset = QGraphicsProxyWidget()
                btn_reset = QPushButton("1:1")
                btn_reset.setFixedSize(28, btn_size)
                btn_reset.setStyleSheet(btn_reset_style)
                btn_reset.clicked.connect(lambda _, k=key: scale_callback(k, 0))
                proxy_reset.setWidget(btn_reset)
                scene.addItem(proxy_reset)
                proxy_reset.setPos(bx + btn_size * 2 + 8, by)
    font = QFont()
    font.setPointSize(14)
    font.setBold(True)
    txt = scene.addText(f"{page_num + 1}/{total_pages}", font)
    txt.setDefaultTextColor(QColor("#2196F3"))
    txt.setPos(5, 5)
    txt.setData(0, "page_number")
    scene.setSceneRect(0, 0, A4_W, A4_H)


class ScannerPage(QWidget):
    go_back = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayoutDirection(Qt.RightToLeft)
        self.subscription_check = lambda: True
        self._images = []
        self._page_map = {}
        self._next_page_num = 0
        self._current_page = 0
        self._selected_idx = 0
        self._multi_scan = False
        self._current_tool = "select"
        self._img_positions = {}
        self._img_scales = {}
        self._reordering = False
        self._copied_image = None
        self._undo_stack = []
        self._redo_stack = []
        self._build_ui()
        self._refresh_scanners()
        self.setFocusPolicy(Qt.StrongFocus)

    def keyPressEvent(self, event):
        if event.matches(event.StandardKey.Copy):
            self._copy_image()
        elif event.matches(event.StandardKey.Paste):
            self._paste_image()
        else:
            super().keyPressEvent(event)

    def _tool_btn_style(self, active):
        if active:
            return ("QPushButton{background:#1a73e8;color:white;border:none;"
                    "border-radius:4px;padding:4px 14px;font-weight:bold;}"
                    "QPushButton:hover{background:#1557b0;}")
        return ("QPushButton{background:#555;color:#ccc;border:none;"
                "border-radius:4px;padding:4px 14px;}"
                "QPushButton:hover{background:#777;color:white;}")

    def _set_tool(self, tool):
        self._current_tool = tool
        is_erase = tool == "erase"
        self._btn_erase.setStyleSheet(self._tool_btn_style(is_erase))
        self._btn_stop_erase.setVisible(is_erase)
        self._view.set_mode(_ScanView.MODE_ERASE if is_erase else _ScanView.MODE_NORMAL)

    def _page_image_indices(self, page_num):
        return sorted([i for i, p in self._page_map.items() if p == page_num])

    def _page_images(self, page_num):
        return [self._images[i] for i in self._page_image_indices(page_num)]

    def _total_pages(self):
        return self._next_page_num

    def _image_scene_rect(self, gidx):
        if gidx not in self._page_map:
            return None
        qimg = self._images[gidx]
        if qimg.isNull():
            return None
        aw = A4_W - 2 * MARGIN
        ah = A4_H - 2 * MARGIN
        if self._multi_scan:
            page_gidxs = self._page_image_indices(self._page_map[gidx])
            half_w = aw / 2
            col = page_gidxs.index(gidx) % 2
            x_off = MARGIN + col * half_w
            pix = QPixmap.fromImage(qimg)
            scaled = pix.scaled(half_w, ah, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = x_off + (half_w - scaled.width()) / 2
            y = MARGIN + (ah - scaled.height()) / 2
            return QRectF(x, y, scaled.width(), scaled.height())
        pix = QPixmap.fromImage(qimg)
        scaled = pix.scaled(aw, ah, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        x = (A4_W - scaled.width()) / 2
        y = (A4_H - scaled.height()) / 2
        return QRectF(x, y, scaled.width(), scaled.height())

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(10, 10, 10, 10)

        top = QHBoxLayout()
        btn_back = QPushButton("\u21a9 رجوع")
        btn_back.setFixedHeight(36)
        btn_back.clicked.connect(self.go_back.emit)
        top.addWidget(btn_back)
        top.addSpacing(10)

        self._btn_erase = QPushButton("🧹 مسح")
        self._btn_erase.setFixedHeight(32)
        self._btn_erase.setStyleSheet(self._tool_btn_style(False))
        self._btn_erase.clicked.connect(lambda: self._set_tool("erase"))
        top.addWidget(self._btn_erase)

        self._btn_stop_erase = QPushButton("✖")
        self._btn_stop_erase.setFixedSize(32, 32)
        self._btn_stop_erase.setStyleSheet(
            "QPushButton{background:#c62828;color:white;font-weight:bold;"
            "border:none;border-radius:16px;font-size:16px;}"
            "QPushButton:hover{background:#e53935;}"
        )
        self._btn_stop_erase.clicked.connect(lambda: self._set_tool("select"))
        self._btn_stop_erase.setVisible(False)
        top.addWidget(self._btn_stop_erase)

        top.addSpacing(10)
        lbl_eraser = QLabel("حجم الفرشاة:")
        lbl_eraser.setStyleSheet("font-size:11px;color:#aaa;")
        top.addWidget(lbl_eraser)
        self._eraser_slider = QSlider(Qt.Horizontal)
        self._eraser_slider.setRange(5, 100)
        self._eraser_slider.setValue(20)
        self._eraser_slider.setFixedWidth(80)
        self._eraser_slider.valueChanged.connect(self._on_eraser_size)
        top.addWidget(self._eraser_slider)
        self._eraser_lbl = QLabel("20")
        self._eraser_lbl.setFixedWidth(22)
        self._eraser_lbl.setStyleSheet("font-size:11px;color:#aaa;")
        top.addWidget(self._eraser_lbl)

        top.addSpacing(10)
        btn_flip_h = QPushButton("↔ قلب أفقي")
        btn_flip_h.setFixedHeight(32)
        btn_flip_h.setStyleSheet(self._tool_btn_style(False))
        btn_flip_h.clicked.connect(self._flip_horizontal)
        top.addWidget(btn_flip_h)

        btn_flip_v = QPushButton("↕ قلب عمودي")
        btn_flip_v.setFixedHeight(32)
        btn_flip_v.setStyleSheet(self._tool_btn_style(False))
        btn_flip_v.clicked.connect(self._flip_vertical)
        top.addWidget(btn_flip_v)

        top.addSpacing(20)
        top.addWidget(QLabel("السكنر:"))
        self._combo = QComboBox()
        self._combo.setMinimumWidth(180)
        top.addWidget(self._combo)
        btn_refresh = QPushButton("\U0001f504")
        btn_refresh.setFixedSize(32, 32)
        btn_refresh.clicked.connect(self._refresh_scanners)
        top.addWidget(btn_refresh)
        top.addStretch()

        self._btn_multi_scan = QPushButton("📎 تصوير متعدد")
        self._btn_multi_scan.setFixedHeight(36)
        self._btn_multi_scan.setCheckable(True)
        self._btn_multi_scan.setStyleSheet(
            "QPushButton{background:#555;color:#ccc;border:none;"
            "border-radius:4px;padding:0 12px;}"
            "QPushButton:hover{background:#777;color:white;}"
            "QPushButton:checked{background:#e67e22;color:white;font-weight:bold;}"
        )
        self._btn_multi_scan.setToolTip(
            "عند التفعيل: كل صورة مسحوضة تضاف لنفس الصفحة\n"
            "عند الإيقاف: كل صورة تفتح صفحة جديدة"
        )
        self._btn_multi_scan.clicked.connect(self._toggle_multi_scan)
        top.addWidget(self._btn_multi_scan)

        btn_scan = QPushButton("\U0001f4f8 مسح ضوئي")
        btn_scan.setFixedHeight(36)
        btn_scan.setStyleSheet(
            "QPushButton{background:#4CAF50;color:white;font-weight:bold;padding:0 16px}"
            "QPushButton:hover{background:#45a049}"
        )
        btn_scan.clicked.connect(self._scan)
        top.addWidget(btn_scan)
        layout.addLayout(top)

        self._scene = QGraphicsScene()
        self._view = _ScanView(self._scene, self)
        self._view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._view.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self._view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._view.setStyleSheet("QGraphicsView { background: #4a4a4a; }")
        self._view.erase_stroke.connect(self._on_erase_stroke)
        self._view.erase_done.connect(self._update_paper)
        self._view.erase_started.connect(self._push_undo)
        layout.addWidget(self._view, 1)

        zoom_row = QHBoxLayout()
        zoom_row.addStretch()
        zoom_row.addWidget(QLabel("🔍"))
        self._zoom_slider = QSlider(Qt.Horizontal)
        self._zoom_slider.setRange(25, 400)
        self._zoom_slider.setValue(100)
        self._zoom_slider.setFixedWidth(200)
        self._zoom_slider.valueChanged.connect(self._on_zoom)
        zoom_row.addWidget(self._zoom_slider)
        self._zoom_lbl = QLabel("100%")
        self._zoom_lbl.setFixedWidth(45)
        zoom_row.addWidget(self._zoom_lbl)
        zoom_row.addStretch()
        layout.addLayout(zoom_row)

        self._thumb_scroll = QScrollArea()
        self._thumb_scroll.setWidgetResizable(True)
        self._thumb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self._thumb_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._thumb_scroll.setFixedHeight(120)
        self._thumb_scroll.setStyleSheet(
            "QScrollArea{background:#333;border:none;}"
            "QScrollBar:horizontal{background:#333;height:10px;}"
            "QScrollBar::handle:horizontal{background:#666;border-radius:4px;min-width:30px;}"
        )
        self._thumb_container = QWidget()
        self._thumb_layout = QHBoxLayout(self._thumb_container)
        self._thumb_layout.setSpacing(6)
        self._thumb_layout.setContentsMargins(6, 4, 6, 4)
        self._thumb_layout.addStretch()
        self._thumb_scroll.setWidget(self._thumb_container)
        layout.addWidget(self._thumb_scroll)

        self._lbl_info = QLabel("لا توجد صور")
        self._lbl_info.setStyleSheet("color:#888;font-size:11px;")
        self._lbl_info.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl_info)

        bottom = QHBoxLayout()
        btn_undo = QPushButton("↩ تراجع")
        btn_undo.setFixedHeight(36)
        btn_undo.setStyleSheet(
            "QPushButton{background:#795548;color:white;font-weight:bold;padding:0 12px}"
            "QPushButton:hover{background:#8d6e63}"
            "QPushButton:disabled{background:#666;color:#999}"
        )
        btn_undo.clicked.connect(self._undo)
        bottom.addWidget(btn_undo)
        self._btn_undo = btn_undo

        btn_redo = QPushButton("↪ إعادة")
        btn_redo.setFixedHeight(36)
        btn_redo.setStyleSheet(
            "QPushButton{background:#795548;color:white;font-weight:bold;padding:0 12px}"
            "QPushButton:hover{background:#8d6e63}"
            "QPushButton:disabled{background:#666;color:#999}"
        )
        btn_redo.clicked.connect(self._redo)
        bottom.addWidget(btn_redo)
        self._btn_redo = btn_redo

        btn_file = QPushButton("📂 اختيار من ملف")
        btn_file.setFixedHeight(36)
        btn_file.clicked.connect(self._import_file)
        bottom.addWidget(btn_file)

        btn_copy = QPushButton("📋 نسخ")
        btn_copy.setFixedHeight(36)
        btn_copy.setStyleSheet(
            "QPushButton{background:#5D4037;color:white;font-weight:bold;padding:0 12px}"
            "QPushButton:hover{background:#795548}"
        )
        btn_copy.clicked.connect(self._copy_image)
        bottom.addWidget(btn_copy)

        btn_paste = QPushButton("📄 لصق")
        btn_paste.setFixedHeight(36)
        btn_paste.setStyleSheet(
            "QPushButton{background:#5D4037;color:white;font-weight:bold;padding:0 12px}"
            "QPushButton:hover{background:#795548}"
        )
        btn_paste.clicked.connect(self._paste_image)
        bottom.addWidget(btn_paste)

        bottom.addStretch()
        btn_print = QPushButton("🖨️ طباعة الكل")
        btn_print.setFixedHeight(36)
        btn_print.setStyleSheet(
            "QPushButton{background:#2196F3;color:white;font-weight:bold;padding:0 16px}"
            "QPushButton:hover{background:#1e88e5}"
        )
        btn_print.clicked.connect(self._print)
        bottom.addWidget(btn_print)
        btn_img = QPushButton("🖼️ حفظ صورة")
        btn_img.setFixedHeight(36)
        btn_img.clicked.connect(self._save_image)
        bottom.addWidget(btn_img)
        btn_pdf = QPushButton("📄 حفظ PDF")
        btn_pdf.setFixedHeight(36)
        btn_pdf.setStyleSheet(
            "QPushButton{background:#f44336;color:white;font-weight:bold;padding:0 16px}"
            "QPushButton:hover{background:#e53935}"
        )
        btn_pdf.clicked.connect(self._save_pdf)
        bottom.addWidget(btn_pdf)
        layout.addLayout(bottom)

    def _push_undo(self):
        self._undo_stack.append([QImage(img) for img in self._images])
        self._redo_stack.clear()
        self._update_undo_buttons()

    def _undo(self):
        if not self._undo_stack:
            return
        self._redo_stack.append([QImage(img) for img in self._images])
        self._images = self._undo_stack.pop()
        self._update_undo_buttons()
        self._rebuild_thumbnails()
        self._update_paper()
        self._fit_view()

    def _redo(self):
        if not self._redo_stack:
            return
        self._undo_stack.append([QImage(img) for img in self._images])
        self._images = self._redo_stack.pop()
        self._update_undo_buttons()
        self._rebuild_thumbnails()
        self._update_paper()
        self._fit_view()

    def _update_undo_buttons(self):
        self._btn_undo.setEnabled(bool(self._undo_stack))
        self._btn_redo.setEnabled(bool(self._redo_stack))

    def _toggle_multi_scan(self):
        self._multi_scan = self._btn_multi_scan.isChecked()
        if self._multi_scan:
            self._btn_multi_scan.setText("📎 متعدد ✅")
        else:
            self._btn_multi_scan.setText("📎 تصوير متعدد")
        self._update_paper()
        logger.info("وضع التصوير المتعدد: %s", "مفعّل" if self._multi_scan else "معطّل")

    def _on_eraser_size(self, val):
        self._view._erase_radius = val
        self._eraser_lbl.setText(str(val))

    def _flip_horizontal(self):
        if not self._images or not (0 <= self._selected_idx < len(self._images)):
            return
        qimg = self._images[self._selected_idx]
        if qimg.isNull():
            return
        self._push_undo()
        self._images[self._selected_idx] = qimg.mirrored(True, False)
        self._rebuild_thumbnails()
        self._update_paper()
        self._fit_view()

    def _flip_vertical(self):
        if not self._images or not (0 <= self._selected_idx < len(self._images)):
            return
        qimg = self._images[self._selected_idx]
        if qimg.isNull():
            return
        self._push_undo()
        self._images[self._selected_idx] = qimg.mirrored(False, True)
        self._rebuild_thumbnails()
        self._update_paper()
        self._fit_view()

    def _copy_image(self):
        if not self._images or not (0 <= self._selected_idx < len(self._images)):
            return
        self._copied_image = QImage(self._images[self._selected_idx])

    def _paste_image(self):
        if self._copied_image is None or self._copied_image.isNull():
            return
        self._push_undo()
        new_img = QImage(self._copied_image)
        gidx = len(self._images)
        self._images.append(new_img)
        if self._multi_scan and self._page_map:
            last_page = max(self._page_map.values())
            self._page_map[gidx] = last_page
        else:
            self._page_map[gidx] = self._next_page_num
            self._next_page_num += 1
        self._selected_idx = gidx
        self._current_page = self._page_map[gidx]
        self._rebuild_thumbnails()
        self._update_paper()
        self._fit_view()

    def _on_erase_stroke(self, scene_pt, radius):
        if not self._images or not (0 <= self._selected_idx < len(self._images)):
            return
        qimg = self._images[self._selected_idx]
        if qimg.isNull():
            return
        img_rect = self._image_scene_rect(self._selected_idx)
        if not img_rect:
            return
        sx = qimg.width() / img_rect.width()
        sy = qimg.height() / img_rect.height()
        px = int((scene_pt.x() - img_rect.x()) * sx)
        py = int((scene_pt.y() - img_rect.y()) * sy)
        er = int(max(radius * sx, radius * sy))
        p = QPainter(qimg)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("white"))
        p.drawEllipse(px - er, py - er, er * 2, er * 2)
        p.end()
        self._images[self._selected_idx] = qimg
        items = self._scene.items()
        page_gidxs = self._page_image_indices(self._current_page)
        if self._selected_idx in page_gidxs:
            local_idx = page_gidxs.index(self._selected_idx)
            pix_items = [it for it in items if isinstance(it, QGraphicsPixmapItem)
                         and (it.flags() & QGraphicsPixmapItem.ItemIsMovable)]
            if local_idx < len(pix_items):
                pix = QPixmap.fromImage(qimg)
                if self._multi_scan:
                    aw = A4_W - 2 * MARGIN
                    half_w = aw / 2
                    scaled = pix.scaled(half_w, A4_H - 2 * MARGIN,
                                        Qt.KeepAspectRatio, Qt.SmoothTransformation)
                else:
                    aw = A4_W - 2 * MARGIN
                    ah = A4_H - 2 * MARGIN
                    scaled = pix.scaled(aw, ah, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                pix_items[local_idx].setPixmap(scaled)

    def _scan(self):
        if not getattr(self, "subscription_check", lambda: True)():
            return
        if not is_available():
            QMessageBox.warning(
                self, "تنبيه",
                "مكتبة pywin32 غير مثبتة.\nثبّتها: pip install pywin32",
            )
            return
        dev_id = self._combo.currentData()
        qimg = scan_with_dialog(dev_id)
        if qimg and not qimg.isNull():
            self._add_image(qimg)
        else:
            QMessageBox.information(self, "تنبيه", "لم يتم الحصول على صورة.")

    def _refresh_scanners(self):
        self._combo.clear()
        if not is_available():
            self._combo.addItem("ثبّت pywin32 لتفعيل السكنر")
            return
        scanners = list_scanners()
        if not scanners:
            self._combo.addItem("لا يوجد سكنر متصل")
            return
        for s in scanners:
            self._combo.addItem(s["name"], s["device_id"])

    def _import_file(self):
        if not getattr(self, "subscription_check", lambda: True)():
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "اختيار صور", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.webp);;All (*)",
        )
        if not paths:
            return
        from PySide6.QtWidgets import QApplication
        for path in paths:
            try:
                qimg = QImage(path)
                if not qimg.isNull():
                    gidx = len(self._images)
                    self._images.append(qimg)
                    if self._multi_scan and self._page_map:
                        last_page = max(self._page_map.values())
                        self._page_map[gidx] = last_page
                    else:
                        self._page_map[gidx] = self._next_page_num
                        self._next_page_num += 1
                    self._selected_idx = gidx
                    self._current_page = self._page_map[gidx]
            except Exception as e:
                logger.warning("فشل تحميل الصورة %s: %s", path, e)
            if len(self._images) % 5 == 0:
                QApplication.processEvents()
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._update_undo_buttons()
        self._rebuild_thumbnails()
        self._update_paper()
        self._fit_view()
        logger.info("تم استيراد %d صورة", len(paths))

    def _add_image(self, qimg):
        gidx = len(self._images)
        self._images.append(qimg)
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._update_undo_buttons()
        if self._multi_scan and self._page_map:
            last_page = max(self._page_map.values())
            self._page_map[gidx] = last_page
        else:
            self._page_map[gidx] = self._next_page_num
            self._next_page_num += 1
        self._selected_idx = gidx
        self._current_page = self._page_map[gidx]
        self._rebuild_thumbnails()
        self._update_paper()
        self._fit_view()
        logger.info("تمت إضافة صورة %d (صفحة %d/%d)",
                     gidx + 1, self._current_page + 1, self._total_pages())

    def _rebuild_thumbnails(self):
        for i in range(self._thumb_layout.count()):
            item = self._thumb_layout.itemAt(i)
            if item and item.widget():
                item.widget().deleteLater()
        for page_num in range(self._next_page_num):
            page_gidxs = self._page_image_indices(page_num)
            if not page_gidxs:
                continue
            hdr = QLabel(f"ص{page_num + 1}")
            hdr.setStyleSheet(
                "color:#aaa;font-size:10px;padding:2px 4px;"
                "background:#555;border-radius:3px;"
            )
            hdr.setFixedWidth(28)
            self._thumb_layout.insertWidget(self._thumb_layout.count() - 1, hdr)
            for gidx in page_gidxs:
                card = self._make_thumb_card(gidx, self._images[gidx])
                self._thumb_layout.insertWidget(self._thumb_layout.count() - 1, card)
        total = len(self._images)
        self._lbl_info.setText(
            f"عدد الصور: {total} | صفحات: {self._total_pages()} | "
            f"المحددة: {self._selected_idx + 1}"
            if total else "لا توجد صور"
        )

    def _make_thumb_card(self, gidx, qimg):
        frame = QFrame()
        frame.setFixedSize(100, 110)
        selected = (gidx == self._selected_idx)
        border = "#2196F3" if selected else "#666"
        frame.setStyleSheet(
            f"QFrame{{background:#444;border:2px solid {border};border-radius:6px;}}"
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        num_row = QHBoxLayout()
        num_row.setSpacing(2)
        num_spin = QSpinBox()
        num_spin.setRange(1, 999)
        num_spin.setValue(gidx + 1)
        num_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        num_spin.setFixedWidth(35)
        num_spin.setAlignment(Qt.AlignCenter)
        num_spin.setStyleSheet(
            "QSpinBox{background:#333;color:#fff;border:1px solid #555;border-radius:3px;font-size:12px;}"
        )
        num_spin.editingFinished.connect(lambda i=gidx, s=num_spin: self._on_spin_changed(i, s.value()))
        num_row.addWidget(num_spin)
        num_row.addStretch()
        btn_del = QPushButton("\u2715")
        btn_del.setFixedSize(20, 20)
        btn_del.setStyleSheet(
            "QPushButton{background:#c62828;color:white;border:none;border-radius:10px;font-size:11px;font-weight:bold;}"
            "QPushButton:hover{background:#e53935;}"
        )
        btn_del.clicked.connect(lambda _, i=gidx: self._delete_image(i))
        num_row.addWidget(btn_del)
        layout.addLayout(num_row)
        pix = QPixmap.fromImage(qimg)
        thumb = pix.scaled(90, 65, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        lbl_img = QLabel()
        lbl_img.setPixmap(thumb)
        lbl_img.setAlignment(Qt.AlignCenter)
        lbl_img.setStyleSheet("background:#555;border-radius:3px;")
        lbl_img.setFixedSize(90, 65)
        lbl_img.mousePressEvent = lambda _, i=gidx: self._select_image(i)
        layout.addWidget(lbl_img, 0, Qt.AlignHCenter)
        return frame

    def _select_image(self, gidx):
        if 0 <= gidx < len(self._images):
            self._selected_idx = gidx
            self._current_page = self._page_map.get(gidx, 0)
            self._set_tool("select")
            self._rebuild_thumbnails()
            self._update_paper()
            self._fit_view()

    def _delete_image(self, gidx):
        if not self._images:
            return
        self._push_undo()
        page_assignments = [self._page_map.get(i, i) for i in range(len(self._images))]
        del self._images[gidx]
        page_assignments.pop(gidx)
        self._page_map.clear()
        for i, page in enumerate(page_assignments):
            self._page_map[i] = page
        if not self._images:
            self._selected_idx = 0
            self._current_page = 0
            self._next_page_num = 1 if self._page_map else 0
        else:
            self._selected_idx = min(self._selected_idx, len(self._images) - 1)
            self._current_page = self._page_map.get(self._selected_idx, 0)
            self._next_page_num = max(self._page_map.values(), default=-1) + 1
        self._rebuild_thumbnails()
        self._update_paper()
        self._fit_view()

    def _on_spin_changed(self, gidx, value):
        if self._reordering:
            return
        self._reordering = True
        try:
            self._reorder_image(gidx, value - 1)
        finally:
            self._reordering = False

    def _reorder_image(self, from_gidx, to_gidx):
        if not self._images:
            return
        to_gidx = max(0, min(to_gidx, len(self._images) - 1))
        if from_gidx == to_gidx:
            return
        self._images[from_gidx], self._images[to_gidx] = self._images[to_gidx], self._images[from_gidx]
        self._img_positions.clear()
        self._img_scales.clear()
        old_selected = self._selected_idx
        if old_selected == from_gidx:
            self._selected_idx = to_gidx
        elif old_selected == to_gidx:
            self._selected_idx = from_gidx
        self._current_page = self._page_map.get(self._selected_idx, 0)
        self._rebuild_thumbnails()
        self._update_paper()
        self._fit_view()

    def _on_image_scale(self, key, direction):
        if not self._multi_scan:
            return
        page_num, img_idx = key
        if img_idx >= len(self._images):
            return
        cur = self._img_scales.get(key, 1.0)
        if direction == 0:
            new_scale = 1.0
        elif direction == 1:
            new_scale = min(5.0, cur * 1.2)
        else:
            new_scale = max(0.2, cur / 1.2)
        if direction != 0:
            self._img_scales[key] = new_scale
        else:
            self._img_scales.pop(key, None)
        self._update_paper()
        self._fit_view()

    def _update_paper(self):
        self._view._cleanup()
        self._scene.clear()
        page_images = self._page_images(self._current_page)
        _layout_images_on_scene(self._scene, page_images,
                                self._current_page, self._total_pages(),
                                self._multi_scan, self._img_positions, self._img_scales,
                                self._on_image_scale)
        self._scene.setSceneRect(0, 0, A4_W, A4_H)
        self._scene.update()
        self._view.viewport().update()

    def _make_scene_for_page(self, page_num):
        s = QGraphicsScene()
        page_images = self._page_images(page_num)
        _layout_images_on_scene(s, page_images, page_num, self._total_pages(),
                                self._multi_scan, self._img_positions, self._img_scales,
                                self._on_image_scale)
        return s

    def _fit_view(self):
        self._view.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)
        m = self._view.transform()
        pct = max(25, min(400, int(m.m11() * 100)))
        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(pct)
        self._zoom_slider.blockSignals(False)
        self._zoom_lbl.setText(f"{pct}%")

    def _on_zoom(self, val):
        s = val / 100.0
        self._view.resetTransform()
        self._view.scale(s, s)
        self._zoom_lbl.setText(f"{val}%")

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        step = 10 if delta > 0 else -10
        self._zoom_slider.setValue(max(25, min(400, self._zoom_slider.value() + step)))

    def _print(self):
        if not getattr(self, "subscription_check", lambda: True)():
            return
        if not self._images:
            QMessageBox.information(self, "تنبيه", "لا توجد صور للطباعة.")
            return
        total_pages = self._total_pages()
        scenes = [self._make_scene_for_page(p) for p in range(total_pages)]
        for s in scenes:
            for item in s.items():
                if item.data(0) == "page_number":
                    item.setVisible(False)
        dlg = PrintSetupDialog(self, page_count=total_pages)
        if dlg.exec():
            set_printer_name(dlg.selected_printer())
            print_scene(
                self, scenes,
                dlg.copies(), total_pages, dlg.duplex(),
                dlg.page_range(), dlg.paper_type(),
            )

    def _save_image(self):
        if not getattr(self, "subscription_check", lambda: True)():
            return
        if not self._images:
            QMessageBox.information(self, "تنبيه", "لا توجد صور للحفظ.")
            return
        path, filt = QFileDialog.getSaveFileName(
            self, "حفظ صورة", "", "PNG (*.png);;JPEG (*.jpg);;BMP (*.bmp)",
        )
        if not path:
            return
        fmt = "PNG"
        if "jpg" in filt.lower() or "jpeg" in filt.lower():
            fmt = "JPEG"
        elif "bmp" in filt.lower():
            fmt = "BMP"
        total_pages = self._total_pages()
        pages_img = []
        for p in range(total_pages):
            scene = self._make_scene_for_page(p)
            img = QImage(A4_W * 3, A4_H * 3, QImage.Format.Format_ARGB32)
            img.fill(QColor("white"))
            painter = QPainter(img)
            scene.render(painter)
            painter.end()
            pages_img.append(img)
        if total_pages == 1:
            pages_img[0].save(path, fmt)
        else:
            base, ext = path.rsplit(".", 1) if "." in path else (path, fmt.lower())
            for i, pg in enumerate(pages_img):
                pg.save(f"{base}_{i + 1}.{ext}", fmt)
        QMessageBox.information(self, "تم", f"تم حفظ {total_pages} صفحة.")

    def _save_pdf(self):
        if not getattr(self, "subscription_check", lambda: True)():
            return
        if not self._images:
            QMessageBox.information(self, "تنبيه", "لا توجد صور للحفظ.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "حفظ PDF", "", "PDF (*.pdf)")
        if not path:
            return
        try:
            printer = QPrinter(QPrinter.ScreenResolution)
            printer.setOutputFormat(QPrinter.PdfFormat)
            printer.setOutputFileName(path)
            printer.setPageSize(QPageSize(QPageSize.A4))
            printer.setResolution(96)
            pw = printer.width()
            ph = printer.height()
            p2 = QPainter()
            p2.begin(printer)
            total_pages = self._total_pages()
            for i in range(total_pages):
                if i > 0:
                    printer.newPage()
                scene = self._make_scene_for_page(i)
                img = QImage(A4_W, A4_H, QImage.Format.Format_ARGB32)
                img.fill(QColor("white"))
                p = QPainter(img)
                scene.render(p)
                p.end()
                p2.drawImage(QRectF(0, 0, pw, ph), img)
            p2.end()
            QMessageBox.information(self, "تم", f"تم حفظ PDF: {path}")
        except Exception as e:
            QMessageBox.warning(self, "خطأ", f"فشل حفظ PDF:\n{e}")
