import logging
import io
import os
import itertools
import numpy as np
from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QVBoxLayout,
                               QPushButton, QWidget, QHBoxLayout, QFileDialog,
                               QMessageBox, QLabel, QGraphicsPixmapItem,
                               QGraphicsItem, QGraphicsRectItem, QDialog, QStyle,
                               QMenu, QInputDialog, QComboBox, QProgressBar,
                                QSlider, QSplitter, QGroupBox, QGridLayout,
                               QScrollArea, QToolButton, QLineEdit, QApplication)
from PySide6.QtCore import Qt, Signal, QRectF, QSettings, QThread, QEvent, QBuffer, QByteArray, QIODevice, QTimer, QTime, QSize, QSizeF
from PySide6.QtGui import QPixmap, QImage, QPen, QColor, QBrush, QPainter, QShortcut, QKeySequence, QPainterPath, QAction, QPageSize, QIcon, QIntValidator
from PySide6.QtPrintSupport import QPrinter, QPrintDialog
from PIL import Image
from core.printer import print_scene, get_selected_printer_name, set_printer_name, set_last_paper_type, NO_PRINT_KEY
from ui.a4_editor import PrintSetupDialog, A4_W, A4_H, MARGIN
from ui import theme

logger = logging.getLogger(__name__)

PHOTO_SIZES = {
    "طول 4.5 سم وعرض 3.5 سم": (35, 45),
    "طول 4 سم وعرض 3 سم": (30, 40),
    "حجم 5.5 (2×2 بوصة)": (50, 50),
}
DEFAULT_PHOTO_SIZE_KEY = "طول 4 سم وعرض 3 سم"
PAPER_SIZES = {
    "A4": (210, 297),
    "A3": (297, 420),
    "A5": (148, 210),
}
DEFAULT_PAPER_KEY = "A4"
GAP = 2


# --------------------------------------------------------------------------- #
# مكتبة الملابس (إلباس الشخص) — مجلد يضيف المستخدم فيه صور الملابس
# --------------------------------------------------------------------------- #
def _garments_dir():
    """مجلد مكتبة الملابس (data/garments). يُنشأ تلقائياً إذا لم يوجد.

    يمرّ عبر core.runtime لتثبيته في مجلد data الحقيقي القابل للكتابة
    (بجانب exe في المجمّع) — لا داخل _internal الذي يُمسح عند التحديث.
    """
    from core.runtime import data_dir
    d = os.path.join(data_dir(), "garments")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = os.path.join(os.path.expanduser("~"), "WorshaGarments")
        os.makedirs(d, exist_ok=True)
    return d


def _list_garments():
    """قائمة بصور الملابس (المسارات): المكتبة المدمجة أولاً ثم المحلية.

    المكتبة المدمجة (img/clothes) مرفقة بالتطبيق وتظهر لكل المستخدمين،
    ثم الملابس التي أضافها المستخدم محلياً (data/garments) على جهازه فقط.
    """
    ext = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff")
    paths = []
    # المكتبة المدمجة مع التطبيق (مشتركة لكل المستخدمين)
    try:
        from core.clothes import CLOTHES_DIR
        for f in sorted(os.listdir(CLOTHES_DIR)):
            if f.lower().endswith(ext):
                p = os.path.join(CLOTHES_DIR, f)
                if os.path.isfile(p):
                    paths.append(p)
    except Exception:
        pass
    # إضافات المستخدم المحلية (خاصة بجهازه فقط)
    try:
        d = _garments_dir()
        seen = {os.path.basename(p) for p in paths}
        for f in sorted(os.listdir(d)):
            if f.lower().endswith(ext) and f not in seen:
                p = os.path.join(d, f)
                if os.path.isfile(p):
                    paths.append(p)
    except Exception:
        pass
    return paths


def _safe_open_image(pb):
    """فتح صورة بأمان: تصحيح اتجاه EXIF + تحويل إلزامي إلى RGB.

    بعض صورت الهواتف تأتي بـ EXIF دوران فيظهر صورت مشوّهة، وبعضها بصيغة
    CMYK/تورّد فتُفشل تحويلات Qt الصامتة. هذا يقسّر التوافق ويُرجع
    ``None`` عند فشل فك الترميز (صيغة غير مدعومة أو ملف تالف) بدل إسقاط
    صامت من المؤشرات.
    """
    try:
        from PIL import Image as _Img
        from PIL import ImageOps as _IOps
        if isinstance(pb, str):
            im = _Img.open(pb)
        else:
            im = _Img.open(io.BytesIO(pb))
        im = _IOps.exif_transpose(im)
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGB")
        return im
    except Exception:
        return None


def _add_garment_from_path(src_path, parent=None):
    """نسخ صورة ملابس من أي مكان إلى مكتبة الملابس (على قرص المستخدم)."""
    import shutil
    from PySide6.QtWidgets import QMessageBox
    if not src_path or not os.path.exists(src_path):
        return None
    d = _garments_dir()
    name = os.path.basename(src_path)
    dest = os.path.join(d, name)
    i = 1
    while os.path.exists(dest):
        stem, e = os.path.splitext(name)
        dest = os.path.join(d, f"{stem}_{i}{e}")
        i += 1
    try:
        shutil.copy2(src_path, dest)
        if parent is not None:
            QMessageBox.information(parent, "تم",
                                    "أُضيفت صورة الملابس إلى المكتبة.")
        return dest
    except Exception as e:
        logger.error("فشل إضافة الملابس: %s", e)
        if parent is not None:
            QMessageBox.warning(parent, "خطأ", f"تعذّرت إضافة الملابس:\n{e}")
        return None


class GarmentPickerDialog(QDialog):
    """نافذة اختيار/إضافة ملابس من مكتبة الملابس (data/garments)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("إلباس الملابس")
        self.setLayoutDirection(Qt.RightToLeft)
        self.selected_path = None
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        title = QLabel("اختر صورة الملابس (أو أضف ملابس جديدة)")
        title.setStyleSheet("font-size:15px;font-weight:bold;color:#0d904f;")
        lay.addWidget(title)

        # تمرير لعرض صور الملابس
        from PySide6.QtWidgets import QScrollArea, QGridLayout, QToolButton
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setMinimumHeight(320)
        self._container = QWidget()
        self._grid = QGridLayout(self._container)
        self._grid.setSpacing(12)
        self._scroll.setWidget(self._container)
        lay.addWidget(self._scroll, 1)

        # زر إضافة ملابس جديدة
        btn_add = QPushButton("＋ إضافة ملابس جديدة من جهازك")
        btn_add.setStyleSheet("background:#0d904f;color:#fff;border-radius:6px;padding:8px;font-weight:bold;")
        btn_add.clicked.connect(self._on_add)
        lay.addWidget(btn_add)

        btn_cancel = QPushButton("إلغاء")
        btn_cancel.clicked.connect(self.reject)
        lay.addWidget(btn_cancel)

        self._refresh()
        self.setMinimumSize(440, 440)

    def _refresh(self):
        # تفريغ الشبكة
        while self._grid.count():
            it = self._grid.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        garments = _list_garments()
        if not garments:
            lbl = QLabel("لا توجد ملابس بعد — اضغط «إضافة ملابس جديدة».")
            lbl.setStyleSheet("color:#888;padding:30px;")
            self._grid.addWidget(lbl, 0, 0)
            return
        row = 0
        for i, path in enumerate(garments):
            btn = QToolButton()
            btn.setIconSize(QSize(100, 100))
            try:
                pix = QPixmap(path)
                if pix.isNull():
                    continue
                btn.setIcon(QIcon(pix.scaled(100, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
            except Exception:
                continue
            btn.setText(os.path.basename(path))
            btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            btn.setFixedSize(110, 130)
            btn.setStyleSheet(
                "QToolButton{background:#222;border:1px solid #444;border-radius:8px;padding:4px;color:#ccc;}"
                "QToolButton:hover{border:2px solid #0d904f;}")
            btn.clicked.connect(lambda _, p=path: self._on_pick(p))
            self._grid.addWidget(btn, row + i // 3, i % 3)

    def _on_add(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "اختر صورة ملابس", "",
            "صور (*.png *.jpg *.jpeg *.bmp *.webp *.tiff);;كل الملفات (*)")
        if path:
            added = _add_garment_from_path(path, parent=self)
            if added:
                self._refresh()

    def _on_pick(self, path):
        self.selected_path = path
        self.accept()


# --------------------------------------------------------------------------- #
# خيط معالجة الإلباس (لا يُجمّد واجهة المستخدم)
# --------------------------------------------------------------------------- #
class TryOnProcessingThread(QThread):
    result_ready = Signal(object)  # PIL Image
    failed = Signal(str)
    progress_msg = Signal(str)

    def __init__(self, person_pil, garment_pil, garment_path, parent=None):
        super().__init__(parent)
        self._person = person_pil
        self._garment = garment_pil
        self._garment_path = garment_path

    def _report(self, text):
        self.progress_msg.emit(str(text))

    def run(self):
        try:
            # الخدمة المجانية عبر Hugging Face
            self._report("جارٍ الإلباس عبر الخدمة المجانية…")
            from core.tryon_free import try_on_free
            out = try_on_free(self._person, self._garment,
                              garment_path=self._garment_path,
                              progress=self._report)
            self.result_ready.emit(out)
        except Exception as e:
            logger.exception("فشل الإلباس")
            self.failed.emit(str(e))


class PhotoEnhanceThread(QThread):
    """كبح ضوضاء الصورة ثم توضيح حوافها، في الخلفية عبر core.ai_enhance.

    بلا تكبير وبلا توليد تفاصيل: المسار الأول (core.ai_enhance_ai) معطّل
    افتراضياً لأن القياس على صور المستخدم أظهر أن نموذج الاستعادة يجعل
    الصور الحادّة أنعم، فبقي محفوظاً خلف USE_MODEL للاستعمال الاختياري.
    """
    result_ready = Signal(object)  # PIL Image
    failed = Signal(str)

    def __init__(self, pil_image, parent=None):
        super().__init__(parent)
        self._pil = pil_image

    def run(self):
        try:
            out = None
            try:
                from core import ai_enhance_ai
                if ai_enhance_ai.should_use_model(self._pil):
                    out = ai_enhance_ai.enhance_image(self._pil)
            except Exception:
                logger.exception("فشل مسار النموذج، سيُستخدم الكلاسيكي")
                out = None
            from core.ai_enhance import enhance_image
            if out is None:
                out = enhance_image(self._pil)
            self.result_ready.emit(out)
        except Exception as e:
            logger.exception("فشل التحسين")
            self.failed.emit(str(e))


class TryOnFreeCfgDialog(QDialog):
    """إعدادات الإلباس المجاني عبر Hugging Face Spaces."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🎨 إعدادات الإلباس المجاني (Hugging Face)")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)

        tip = QLabel(
            "إلباس مجاني 100% عبر نماذج Virtual Try-On على Hugging Face "
            "(IDM-VTON/OOTDiffusion/Kolors).\n"
            "يرسل التطبيق صورة الشخص والملابس إلى نموذج مجاني ويستعيد النتيجة.\n"
            "ملاحظة: الخدمات المجانية قد تنام أو تزدحم — إن فشلت الأولى "
            "يجرّب التطبيق الـ بدائل تلقائياً.")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#999;background:#2a2a2a;border-radius:6px;padding:10px;")
        lay.addWidget(tip)

        from core.tryon_free import get_space, list_spaces, get_max_retries

        form = QGridLayout()
        form.addWidget(QLabel("الـSpace الأساسي:"), 0, 0)
        self.cmb_space = QComboBox()
        self.cmb_space.addItems(list_spaces())
        self.cmb_space.setEditable(True)
        cur = get_space()
        if cur not in [self.cmb_space.itemText(i) for i in range(self.cmb_space.count())]:
            self.cmb_space.addItem(cur)
        self.cmb_space.setCurrentText(cur)
        form.addWidget(self.cmb_space, 0, 1)

        form.addWidget(QLabel("عدد المحاولات:"), 1, 0)
        self.spin_retries = QSpinBox()
        self.spin_retries.setRange(0, 6)
        self.spin_retries.setValue(get_max_retries())
        form.addWidget(self.spin_retries, 1, 1)
        lay.addLayout(form)

        row = QHBoxLayout()
        btn_save = QPushButton("حفظ")
        btn_save.setStyleSheet("background:#0d904f;color:#fff;border-radius:6px;padding:8px 20px;font-weight:bold;")
        btn_save.clicked.connect(self._on_save)
        btn_cancel = QPushButton("إلغاء")
        btn_cancel.clicked.connect(self.reject)
        row.addWidget(btn_save)
        row.addWidget(btn_cancel)
        lay.addLayout(row)

    def _on_save(self):
        from core.tryon_free import set_space, get_max_retries, add_space_custom
        name = self.cmb_space.currentText().strip()
        if name:
            add_space_custom(name)
            set_space(name)
        # حفظ عدد المحاولات (بعدّ حفظ عبر التخزين أعادته)
        import os, json
        from core.runtime import data_dir as _rt_data_dir
        cfg_path = os.path.join(_rt_data_dir(), "app_config.json")
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                full = json.load(f) or {}
            full.setdefault("tryon_free", {})
            full["tryon_free"]["max_retries"] = int(self.spin_retries.value())
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(full, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        QMessageBox.information(self, "تم", "تم حفظ إعدادات الإلباس المجاني.")
        self.accept()


class PhotoItem(QGraphicsPixmapItem):
    def __init__(self, pixmap, pw, ph, index=0, parent=None):
        super().__init__(pixmap, parent)
        self.index = index
        self._pw = pw
        self._ph = ph
        self._rotation = 0
        self._original_pixmap = pixmap
        self._source_pixmap = pixmap
        self._crop_rect = None
        self._adjust_settings = {}
        self._source_bytes = None
        self._processing = False
        self._spin_angle = 0
        self._spin_timer = None
        self._group = None
        self._frame = False
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)

    def item_rotation(self):
        return self._rotation

    def set_item_rotation(self, angle):
        self._rotation = angle % 360
        self.update()

    def set_frame(self, enabled):
        self._frame = enabled
        self.update()

    def set_processing(self, val):
        self._processing = val
        if val:
            self._spin_angle = 0
            if self._spin_timer is None:
                self._spin_timer = QTimer()
                self._spin_timer.timeout.connect(self._spin_tick)
            self._spin_timer.start(50)
        else:
            if self._spin_timer:
                self._spin_timer.stop()
            self._spin_angle = 0
        self.update()

    def _spin_tick(self):
        self._spin_angle = (self._spin_angle + 30) % 360
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
            r = self._rotation % 360
            # المربعات المتطابقة الأبعاد (مثل 2×2 بوصة): تغطية كاملة للمربع مع قصّ متوازن
            is_square = abs(self._pw - self._ph) <= 1.0
            if r in (90, 270):
                fit_scale = min(self._ph / source.width(), self._pw / source.height())
                cover_scale = max(self._ph / source.width(), self._pw / source.height())
            else:
                fit_scale = min(self._pw / source.width(), self._ph / source.height())
                cover_scale = max(self._pw / source.width(), self._ph / source.height())
            scale = cover_scale if is_square else fit_scale
            dw = max(1, int(source.width() * scale))
            dh = max(1, int(source.height() * scale))
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            if is_square:
                # قصّ الرسم على حدود المربع حتى لا تخرج الصورة منه
                painter.setClipRect(QRectF(-self._pw / 2, -self._ph / 2, self._pw, self._ph))
                painter.drawPixmap(QRectF(-dw / 2, -dh / 2, dw, dh),
                                   source, source.rect())
            elif r in (90, 270):
                # بعد دوران 90/270، جهة "القاع" هي جهة "اليمين" في إحداثيات الرسم
                painter.drawPixmap(QRectF(self._pw / 2 - dw, -dh / 2, dw, dh),
                                   source, source.rect())
            else:
                painter.drawPixmap(QRectF(-dw / 2, self._ph / 2 - dh, dw, dh),
                                   source, source.rect())
            painter.restore()
        if self._frame:
            painter.setPen(QPen(QColor(0, 0, 0, 110), 0.6))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(QRectF(0.3, 0.3, self._pw - 0.6, self._ph - 0.6))
        selected = bool(option.state & QStyle.State_Selected)
        scene = self.scene()
        printing = bool(getattr(scene, "_printing", False)) if scene is not None else False
        if selected and not printing:
            painter.setPen(QPen(QColor("#1a73e8"), 4))
            painter.setBrush(QBrush(QColor(26, 115, 232, 20)))
            painter.drawRect(QRectF(1, 1, self._pw - 2, self._ph - 2))
        if self._processing:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(0, 0, 0, 120)))
            painter.drawRect(QRectF(0, 0, self._pw, self._ph))
            painter.setRenderHint(QPainter.Antialiasing, True)
            pen = QPen(QColor("#ffffff"))
            pen.setWidth(3)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            side = min(self._pw, self._ph) * 0.25
            cx, cy = self._pw / 2, self._ph / 2
            r = side / 2
            start = self._spin_angle * 16
            painter.drawArc(QRectF(cx - r, cy - r, side, side), start, 90 * 16)

    def mousePressEvent(self, event):
        self.setSelected(True)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        base = self.pixmap()
        if base.isNull():
            base = self._original_pixmap
        cr = self._crop_rect
        if base.size() != self._original_pixmap.size():
            cr = None
        dialog = PhotoCropDialog(
            base, None,
            slot_size=(self._pw, self._ph),
            crop_rect=cr,
            saved_settings=self._adjust_settings,
            source_pixmap=self._source_pixmap,
            source_bytes=self._source_bytes,
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
    digit_pressed = Signal(int)

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

    def keyPressEvent(self, event):
        digits = {Qt.Key_0: 0, Qt.Key_1: 1, Qt.Key_2: 2, Qt.Key_3: 3, Qt.Key_4: 4,
                  Qt.Key_5: 5, Qt.Key_6: 6, Qt.Key_7: 7, Qt.Key_8: 8, Qt.Key_9: 9}
        key = event.key()
        if event.modifiers() == Qt.NoModifier and key in digits:
            self.digit_pressed.emit(digits[key])
            event.accept()
            return
        super().keyPressEvent(event)

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
                editor = self.parent()
                editor._push_undo()
                editor._add_copies(item, count)

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
    def __init__(self, pixmap: QPixmap, parent=None, slot_size=None, crop_rect=None, saved_settings=None, source_pixmap=None, source_bytes=None):
        super().__init__(parent)
        self.setWindowTitle("تحسين الصورة")
        self._original = pixmap
        self._current = pixmap
        self._after_pixmap = None
        self._base = pixmap
        self._source_original = source_pixmap if source_pixmap is not None else pixmap
        self._source_bytes = source_bytes
        self._brush_src = None
        if source_bytes is not None:
            try:
                img = Image.open(io.BytesIO(source_bytes))
                img = img.convert("RGB")
                self._brush_src = np.array(img)
            except Exception:
                self._brush_src = None
        self.result_pixmap = None
        self.strength_value = 0
        self._slot_size = slot_size
        self._overlay = None
        self._initialized = False
        self.crop_rect = crop_rect
        self._sliders = {}
        self._saved_settings = saved_settings or {}
        self._poly_mode = False
        self._poly_points = []
        self._poly_items = []
        self._brush_mode = False
        self._brush_radius = 25
        self._btn_poly_crop = None
        self._btn_poly_clear = None
        self._btn_poly_preview = None
        self._btn_restore_brush = None
        self._tryon_thread = None
        self._setup_ui()
        self._restore_settings()
        self._add_shortcuts()

    def _setup_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 4, 8, 4)
        btn_source = QPushButton("الصورة الاصلية")
        btn_source.setToolTip("عرض الصورة كما كانت قبل إزالة الخلفية")
        btn_source.setStyleSheet("background:#8e24aa;color:#fff;border-radius:4px;padding:4px 10px;")
        btn_source.clicked.connect(self._show_source_original)
        toolbar.addWidget(btn_source)
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

        self._btn_poly_crop = QPushButton("✂ قص")
        self._btn_poly_crop.setStyleSheet(
            "QPushButton{background:#1565c0;color:#fff;border-radius:4px;padding:6px 16px;font-weight:bold;}"
            "QPushButton:hover{background:#1976d2;}"
        )
        self._btn_poly_crop.clicked.connect(self._enter_poly_mode)
        btn_bar.addWidget(self._btn_poly_crop)

        self._btn_poly_clear = QPushButton("مسح النقاط")
        self._btn_poly_clear.setStyleSheet(
            "QPushButton{background:#e67e22;color:#fff;border-radius:4px;padding:6px 14px;}"
            "QPushButton:hover{background:#f39c12;}"
        )
        self._btn_poly_clear.clicked.connect(self._poly_clear)
        self._btn_poly_clear.setEnabled(False)
        btn_bar.addWidget(self._btn_poly_clear)

        self._btn_poly_preview = QPushButton("معاينة")
        self._btn_poly_preview.setStyleSheet(
            "QPushButton{background:#2196F3;color:#fff;border-radius:4px;padding:6px 16px;font-weight:bold;}"
            "QPushButton:hover{background:#1976D2;}"
        )
        self._btn_poly_preview.clicked.connect(self._preview_poly)
        self._btn_poly_preview.setEnabled(False)
        btn_bar.addWidget(self._btn_poly_preview)

        self._btn_tryon = QPushButton("👔 إلباس الملابس")
        self._btn_tryon.setToolTip(
            "اختر صورة ملابس من المكتبة (أو أضفها من جهازك) ليتم إلباسها للشخص في الصورة"
        )
        self._btn_tryon.setStyleSheet(
            "QPushButton{background:#0d904f;color:#fff;border-radius:4px;padding:6px 16px;font-weight:bold;}"
            "QPushButton:hover{background:#0fb257;}"
        )
        self._btn_tryon.clicked.connect(self._tryon_maintenance)
        btn_bar.addWidget(self._btn_tryon)

        self._btn_enhance = QPushButton("✨ تحسين وتوضيح الصورة")
        self._btn_enhance.setToolTip(
            "تحسين الصور قيد الصيانة حالياً — سيُفتح قريباً"
        )
        self._btn_enhance.setStyleSheet(
            "QPushButton{background:#0288d1;color:#fff;border-radius:4px;padding:6px 14px;font-weight:bold;}"
            "QPushButton:hover{background:#0277bd;}"
        )
        self._btn_enhance.clicked.connect(self._enhance_maintenance)
        btn_bar.addWidget(self._btn_enhance)

        btn_bar.addStretch()

        btn_apply = QPushButton("تطبيق")
        btn_apply.setStyleSheet("background:#0d904f;color:#fff;border-radius:4px;padding:6px 20px;")
        btn_apply.clicked.connect(self._final_apply)
        btn_bar.addWidget(btn_apply)

        btn_cancel = QPushButton("إلغاء")
        btn_cancel.setStyleSheet("background:#555;color:#eee;border-radius:4px;padding:6px 20px;")
        btn_cancel.clicked.connect(self.reject)
        btn_bar.addWidget(btn_cancel)
        main.addLayout(btn_bar)

        screen = QApplication.primaryScreen()
        if screen is not None:
            sgeo = screen.availableGeometry()
            desired_w = int(min(860, sgeo.width() * 0.78))
            desired_h = int(min(760, sgeo.height() * 0.85))
        else:
            desired_w, desired_h = 780, 680
        self.resize(max(560, desired_w), max(520, desired_h))
        self.setMinimumSize(540, 560)

    def _add_shortcuts(self):
        QShortcut(QKeySequence("Return"), self, self._final_apply)
        QShortcut(QKeySequence("Enter"), self, self._final_apply)
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

    def _show_source_original(self):
        self._poly_mode = False
        self._poly_points.clear()
        self._clear_poly_items()
        self.result_pixmap = None
        self.view.viewport().unsetCursor()
        self.view.setDragMode(QGraphicsView.ScrollHandDrag)
        self._save_crop_from_overlay()
        self._current = self._source_original
        self._base = self._source_original
        self._orig_bgr = None
        self._cached_face_mask = None
        self._recreate_scene(self._source_original)
        self._zoom_fit()
        for sl in self._sliders.values():
            sl.slider.setValue(0)
        for sl in self._sliders.values():
            sl.setEnabled(True)
        if self._btn_poly_crop is not None:
            self._btn_poly_crop.setStyleSheet(
                "QPushButton{background:#1565c0;color:#fff;border-radius:4px;padding:6px 16px;font-weight:bold;}"
                "QPushButton:hover{background:#1976d2;}"
            )
            self._btn_poly_clear.setEnabled(False)
            self._btn_poly_preview.setEnabled(False)
        self._lbl_status.setText("تم عرض الصورة الأصلية قبل إزالة الخلفية — قصّ كما تريد ثم اضغط «معاينة»")
        self._update_poly_buttons()

    def _reset_to_original(self):
        self._poly_mode = False
        self._poly_points.clear()
        self._clear_poly_items()
        self.result_pixmap = None
        self.view.viewport().unsetCursor()
        self.view.setDragMode(QGraphicsView.ScrollHandDrag)
        self._current = self._original
        self._base = self._original
        self._orig_bgr = None
        self._cached_face_mask = None
        self._recreate_scene(self._original)
        self._zoom_fit()
        for sl in self._sliders.values():
            sl.slider.setValue(0)
        for sl in self._sliders.values():
            sl.setEnabled(True)
        if self._btn_poly_crop is not None:
            self._btn_poly_crop.setStyleSheet(
                "QPushButton{background:#1565c0;color:#fff;border-radius:4px;padding:6px 16px;font-weight:bold;}"
                "QPushButton:hover{background:#1976d2;}"
            )
            self._btn_poly_clear.setEnabled(False)
            self._btn_poly_preview.setEnabled(False)
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
            self._current = self._base
            self._recreate_scene(self._base)
            self._zoom_fit()
            return

        if not hasattr(self, '_orig_bgr') or self._orig_bgr is None:
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.WriteOnly)
            self._base.toImage().save(buf, "PNG")
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
        self._recreate_scene(self._current)
        self._zoom_fit()

    def _save_crop_from_overlay(self):
        if self._overlay:
            r = self._overlay.rect()
            if r.width() > 2 and r.height() > 2:
                self.crop_rect = (round(r.x()), round(r.y()), round(r.width()), round(r.height()))

    def _rescale_crop_rect(self, old_w, old_h, new_w, new_h):
        if not self.crop_rect or old_w <= 0 or old_h <= 0:
            return
        sx = new_w / old_w
        sy = new_h / old_h
        rx, ry, rw, rh = self.crop_rect
        rx = max(0, min(new_w - 1, round(rx * sx)))
        ry = max(0, min(new_h - 1, round(ry * sy)))
        rw = max(2, min(new_w - rx, round(rw * sx)))
        rh = max(2, min(new_h - ry, round(rh * sy)))
        self.crop_rect = (rx, ry, rw, rh)

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
                self.result_pixmap = self._current.copy(round(rx), round(ry), round(rw), round(rh))
                self.crop_rect = (round(rx), round(ry), round(rw), round(rh))
            else:
                self.result_pixmap = self._current
        else:
            self.result_pixmap = self._current
        self.strength_value = 0
        self.accept()

    def _recreate_scene(self, pixmap):
        self._save_crop_from_overlay()
        new_w = pixmap.width()
        new_h = pixmap.height()
        if self._overlay is not None:
            old_rect = self._overlay.image_rect
            old_w, old_h = old_rect.width(), old_rect.height()
            if old_w > 0 and old_h > 0 and (old_w, old_h) != (new_w, new_h):
                self._rescale_crop_rect(old_w, old_h, new_w, new_h)
        self.scene.clear()
        self._poly_items.clear()
        self._pixmap_item = self.scene.addPixmap(pixmap)
        self._overlay = _CropOverlay(self.scene, QRectF(self._pixmap_item.pos(), self._pixmap_item.boundingRect().size()))
        if self.crop_rect:
            self._overlay.set_rect(QRectF(*self.crop_rect))

    def _enter_poly_mode(self):
        if self._brush_mode:
            self._brush_mode = False
            if self._btn_restore_brush is not None:
                self._btn_restore_brush.setStyleSheet(
                    "QPushButton{background:#6a1b9a;color:#fff;border-radius:4px;padding:6px 16px;font-weight:bold;}"
                    "QPushButton:hover{background:#7b1fa2;}"
                )
                self._btn_restore_brush.setEnabled(self._brush_src is not None)
        self._poly_mode = True
        self._poly_points.clear()
        self._clear_poly_items()
        self.result_pixmap = None
        self._save_crop_from_overlay()
        self._recreate_scene(self._current)
        self._zoom_fit()
        self._btn_poly_crop.setStyleSheet(
            "QPushButton{background:#0d47a1;color:#fff;border-radius:4px;padding:6px 16px;font-weight:bold;}"
            "QPushButton:hover{background:#1565c0;}"
        )
        self._btn_poly_clear.setEnabled(False)
        self._btn_poly_preview.setEnabled(False)
        if self._btn_restore_brush is not None:
            self._btn_restore_brush.setEnabled(False)
        self._lbl_status.setText("انقر نقاطاً متعددة على الصورة — الخط يتصل بين النقرات، ثم اضغط «معاينة»")
        for s in self._sliders.values():
            s.setEnabled(False)
        self.view.setDragMode(QGraphicsView.NoDrag)
        self.view.viewport().setCursor(Qt.CrossCursor)

    def _poly_clear(self):
        self._poly_points.clear()
        self._clear_poly_items()
        self.result_pixmap = None
        self._update_poly_buttons()
        self._lbl_status.setText("النقاط: 0 — انقر نقاطاً ثم اضغط «معاينة»")

    def _clear_poly_items(self):
        for it in self._poly_items:
            try:
                self.scene.removeItem(it)
            except RuntimeError:
                pass
        self._poly_items.clear()

    def _add_poly_point(self, scene_pos):
        from PySide6.QtCore import QPointF
        w = self._current.width()
        h = self._current.height()
        x = max(0.0, min(float(w), scene_pos.x()))
        y = max(0.0, min(float(h), scene_pos.y()))
        pt = QPointF(x, y)
        if self._poly_points:
            last = self._poly_points[-1]
            if (pt - last).manhattanLength() < 4.0:
                return
        self._poly_points.append(pt)
        marker = self.scene.addEllipse(x - 4, y - 4, 8, 8,
                                       QPen(QColor("#00ff00")), QBrush(QColor("#00ff00")))
        marker.setZValue(20)
        self._poly_items.append(marker)
        if len(self._poly_points) > 1:
            prev = self._poly_points[-2]
            line = self.scene.addLine(prev.x(), prev.y(), x, y,
                                      QPen(QColor("#00ff00"), 2))
            line.setZValue(19)
            self._poly_items.append(line)
        self._update_poly_buttons()
        self._lbl_status.setText(f"النقاط: {len(self._poly_points)} — اضغط «معاينة» عند الانتهاء")

    def _update_poly_buttons(self):
        n = len(self._poly_points)
        self._btn_poly_clear.setEnabled(n > 0)
        self._btn_poly_preview.setEnabled(n >= 3 or self.result_pixmap is not None)

    def _preview_poly(self):
        if not self._poly_mode:
            return
        if self.result_pixmap is None:
            if len(self._poly_points) < 3:
                self._lbl_status.setText("حدد 3 نقاط على الأقل ثم اضغط «معاينة»")
                return
            cropped = self._polygon_crop(self._current, self._poly_points)
            if cropped is None or cropped.isNull():
                self._lbl_status.setText("فشل القص، أعد المحاولة")
                return
            self.result_pixmap = cropped
        self._clear_poly_items()
        try:
            self.scene.removeItem(self._pixmap_item)
        except RuntimeError:
            pass
        self._pixmap_item = self.scene.addPixmap(self.result_pixmap)
        if self._overlay:
            self._overlay.set_visible(False)
        self._zoom_fit()
        self._lbl_status.setText("تم قص الصورة — اضغط «تطبيق» للإضافة أو «قص» لإعادة التحديد")
        self._update_poly_buttons()

    def _polygon_crop(self, pixmap, points):
        import cv2
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        pixmap.toImage().save(buf, "PNG")
        buf.close()
        raw = bytes(ba.data())
        arr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            return None
        h, w = arr.shape[:2]
        pts = np.array([(int(max(0, min(w - 1, p.x()))), int(max(0, min(h - 1, p.y())))) for p in points],
                       dtype=np.int32).reshape((-1, 1, 2))
        mask = np.zeros((h, w), dtype=np.uint8)
        cv2.fillPoly(mask, [pts], 255)
        rx, ry, rw, rh = self._effective_crop_rect(w, h)
        x2r, y2r = rx + rw - 1, ry + rh - 1
        region = arr[ry:y2r + 1, rx:x2r + 1]
        rm = mask[ry:y2r + 1, rx:x2r + 1]
        out = region.copy()
        out[rm == 0] = 255
        rgb = cv2.cvtColor(out, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, rw, rh, 3 * rw, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(qimg.copy())

    def _enter_brush_mode(self):
        if not self._brush_mode:
            self._brush_mode = True
            self._poly_mode = False
            self._clear_poly_items()
            self.result_pixmap = None
            self._save_crop_from_overlay()
            self._recreate_scene(self._current)
            self._zoom_fit()
            if self._btn_restore_brush is not None:
                self._btn_restore_brush.setStyleSheet(
                    "QPushButton{background:#4a148c;color:#fff;border-radius:4px;padding:6px 16px;font-weight:bold;}"
                    "QPushButton:hover{background:#6a1b9a;}"
                )
            self._btn_poly_crop.setEnabled(False)
            self._lbl_status.setText("مرر الفرشاة فوق الأجزاء المحذوفة (ملابس) لإعادتها من الصورة الأصلية — اضغط «🖌 استعادة» مرة أخرى للخروج")
            for s in self._sliders.values():
                s.setEnabled(False)
            self.view.setDragMode(QGraphicsView.NoDrag)
            self.view.viewport().setCursor(Qt.CrossCursor)
        else:
            self._brush_mode = False
            if self._btn_restore_brush is not None:
                self._btn_restore_brush.setStyleSheet(
                    "QPushButton{background:#6a1b9a;color:#fff;border-radius:4px;padding:6px 16px;font-weight:bold;}"
                    "QPushButton:hover{background:#7b1fa2;}"
                )
            self._btn_poly_crop.setEnabled(True)
            self._lbl_status.setText("")
            for s in self._sliders.values():
                s.setEnabled(True)
            self.view.setDragMode(QGraphicsView.ScrollHandDrag)
            self.view.viewport().unsetCursor()

    def _apply_brush_stroke(self, cur_pos, prev_pos=None):
        if self._brush_src is None:
            return
        import cv2
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        # current image as RGB numpy
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        self._current.toImage().save(buf, "PNG")
        buf.close()
        raw = bytes(ba.data())
        cur_arr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if cur_arr is None:
            return
        cur_arr = cv2.cvtColor(cur_arr, cv2.COLOR_BGR2RGB)
        h, w = cur_arr.shape[:2]
        # source aligned to current size
        src = self._brush_src
        if src.shape[0] != h or src.shape[1] != w:
            from PIL import Image as _PIL
            src = np.array(_PIL.fromarray(src).resize((w, h), _PIL.LANCZOS))
        cur_arr = cur_arr.astype(np.int32)
        out = cur_arr.copy()
        def _stamp(cx, cy):
            cx = max(0, min(w - 1, int(round(cx))))
            cy = max(0, min(h - 1, int(round(cy))))
            r = max(2, int(self._brush_radius))
            y0, y1 = max(0, cy - r), min(h, cy + r + 1)
            x0, x1 = max(0, cx - r), min(w, cx + r + 1)
            yy, xx = np.mgrid[y0:y1, x0:x1]
            d2 = (xx - cx) ** 2 + (yy - cy) ** 2
            in_circle = d2 <= r * r
            # how "white"/removed each pixel is -> weight toward full restore
            base = out[y0:y1, x0:x1]
            dist_white = np.max(np.abs(base - 255.0), axis=2)
            weight = np.clip(1.0 - (dist_white / 60.0), 0.0, 1.0)
            weight[~in_circle] = 0.0
            w4 = weight[..., None]
            newpix = src[y0:y1, x0:x1].astype(np.int32)
            # White-balance: bright pixels restored from the original (white shirts
            # that had shading/shadows) are forced back to pure white so the shirt
            # does not turn gray. Only bright near-white pixels are clamped.
            luma = (newpix[..., 0] * 0.299 + newpix[..., 1] * 0.587 + newpix[..., 2] * 0.114)
            bright = luma >= 200.0
            newpix[bright] = 255
            # Blend only where the model removed background; keep existing subject.
            out[y0:y1, x0:x1] = (base * (1 - w4) + newpix * w4).astype(np.int32)
        _stamp(cur_pos.x(), cur_pos.y())
        if prev_pos is not None:
            import math
            _stamp(prev_pos.x(), prev_pos.y())
            # interpolate along the segment for smooth strokes
            dx = cur_pos.x() - prev_pos.x()
            dy = cur_pos.y() - prev_pos.y()
            dist = math.hypot(dx, dy)
            steps = max(1, int(dist // max(4, self._brush_radius // 2)))
            for i in range(1, steps):
                t = i / steps
                _stamp(prev_pos.x() + dx * t, prev_pos.y() + dy * t)
        out = np.clip(out, 0, 255).astype(np.uint8)
        qimg = QImage(out.data, w, h, 3 * w, QImage.Format.Format_RGB888)
        new_pm = QPixmap.fromImage(qimg.copy())
        self._current = new_pm
        self._base = new_pm
        try:
            self.scene.removeItem(self._pixmap_item)
        except RuntimeError:
            pass
        self._pixmap_item = self.scene.addPixmap(new_pm)
        if self._overlay:
            self._overlay.set_visible(True)
        self._lbl_status.setText("🖌 استعادة نشطة — اسحب فوق الملابس المحذوفة لإرجاعها")

    def _effective_crop_rect(self, w, h):
        rx, ry, rw, rh = 0, 0, w, h
        cr = self.crop_rect
        if cr and len(cr) == 4:
            rx, ry, rw, rh = [int(round(float(v))) for v in cr]
        rx = max(0, min(w - 2, rx))
        ry = max(0, min(h - 2, ry))
        rw = max(2, min(w - rx, rw))
        rh = max(2, min(h - ry, rh))
        return (rx, ry, rw, rh)

    def _final_apply(self):
        if self._brush_mode:
            self._brush_mode = False
            if self._btn_restore_brush is not None:
                self._btn_restore_brush.setStyleSheet(
                    "QPushButton{background:#6a1b9a;color:#fff;border-radius:4px;padding:6px 16px;font-weight:bold;}"
                    "QPushButton:hover{background:#7b1fa2;}"
                )
            self._btn_poly_crop.setEnabled(True)
            for s in self._sliders.values():
                s.setEnabled(True)
            self.view.setDragMode(QGraphicsView.ScrollHandDrag)
            self.view.viewport().unsetCursor()
            self._lbl_status.setText("")
        if self._poly_mode:
            if self.result_pixmap is None:
                self._preview_poly()
                if self.result_pixmap is None:
                    self._lbl_status.setText("حدد 3 نقاط على الأقل ثم اضغط «معاينة»")
                    return
            self._clear_poly_items()
            if self._overlay:
                self._overlay.set_visible(True)
            self._poly_mode = False
            if self._btn_restore_brush is not None:
                self._btn_restore_brush.setEnabled(self._brush_src is not None)
            self.view.setDragMode(QGraphicsView.ScrollHandDrag)
            self.view.viewport().unsetCursor()
            self.accept()
            return
        self._apply()

    def _tryon_maintenance(self):
        QMessageBox.information(
            self, "تحت الصيانة",
            "ميزة إلباس الملابس قيد الصيانة حالياً. "
            "رجاءً عد إليها لاحقاً.")

    def _enhance_maintenance(self):
        """تعطيل مؤقت لزر التحسين بطلب المستخدم — بلا تنفيذ أي معالجة."""
        QMessageBox.information(
            self, "تحت الصيانة",
            "ميزة تحسين الصور قيد الصيانة حالياً. "
            "سيُفتح قريباً بإذن الله.")

    def _run_enhance(self):
        if getattr(self, '_enhance_thread', None) is not None \
                and self._enhance_thread.isRunning():
            return
        try:
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.WriteOnly)
            self._current.toImage().save(buf, "PNG")
            buf.close()
            pil = Image.open(io.BytesIO(bytes(ba.data()))).convert("RGB")
        except Exception as e:
            self._lbl_status.setText(f"تعذّر تجهيز الصورة: {e}")
            return
        self._btn_enhance.setEnabled(False)
        self._progress.setRange(0, 0)
        self._progress.show()
        self._lbl_status.setText("جارٍ تحسين وتوضيح الصورة… قد يستغرق بعض اللحظات")
        self._enhance_thread = PhotoEnhanceThread(pil)
        self._enhance_thread.result_ready.connect(self._on_enhance_ready)
        self._enhance_thread.failed.connect(self._on_enhance_failed)
        self._enhance_thread.start()

    def _on_enhance_ready(self, out_pil):
        try:
            src_size = out_pil.info.get("realesrgan_source_size")
            out_pil = out_pil.convert("RGBA")
            qimg = QImage(out_pil.tobytes("raw", "RGBA"),
                          out_pil.size[0], out_pil.size[1],
                          out_pil.size[0] * 4, QImage.Format_RGBA8888)
            self._current = QPixmap.fromImage(qimg.copy())
            self._base = self._current
            self._orig_bgr = None
            self._cached_face_mask = None
            self.result_pixmap = None
            self._recreate_scene(self._current)
            self._zoom_fit()
            if src_size and out_pil.size[0] > src_size[0]:
                msg = ("تم التحسين بنموذج Real-ESRGAN ✓ — رُفعت الدقة من "
                       f"{src_size[0]}×{src_size[1]} إلى {out_pil.size[0]}×"
                       f"{out_pil.size[1]} (جودة طباعة أعلى) — اضغط «تطبيق» للموافقة.")
            else:
                msg = "تم تحسين الصورة وتوضيحها ✓ — اضغط «تطبيق» للموافقة."
            self._lbl_status.setText(msg)
        except Exception as e:
            self._lbl_status.setText(f"خطأ في عرض النتيجة: {e}")
        finally:
            self._btn_enhance.setEnabled(True)
            self._progress.hide()

    def _on_enhance_failed(self, err):
        self._btn_enhance.setEnabled(True)
        self._progress.hide()
        self._lbl_status.setText("تعذّر التحسين.")
        QMessageBox.warning(self, "خطأ", f"تعذّرت عملية التحسين:\n{err}")

    def _preview_card_size(self):
        """يعرض كيف ستبدو الصورة داخل مقاس البطاقة 4.5×3.5 سم — دون استخدام الرصيد."""
        try:
            from core.photo_processor import resize_to_photo_size
            # نسخة من الصورة الحالية
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.WriteOnly)
            self._current.toImage().save(buf, "PNG")
            buf.close()
            pil = Image.open(io.BytesIO(bytes(ba.data()))).convert("RGBA")
            # لا نغيّر نسب الجسد والرأس — فقط تصغير لو تجاوز المربع
            pil = resize_to_photo_size(pil, 35, 45)
            qimg = QImage(pil.tobytes("raw", "RGBA"),
                          pil.size[0], pil.size[1],
                          pil.size[0] * 4, QImage.Format_RGBA8888)
            self._current = QPixmap.fromImage(qimg.copy())
            self._base = self._current
            self._orig_bgr = None
            self._cached_face_mask = None
            self._recreate_scene(self._current)
            self._zoom_fit()
            self._lbl_status.setText("معاينة مقاس البطاقة 4.5×3.5 ✓ — لم يُستهلك رصيد.")
        except Exception as e:
            self._lbl_status.setText(f"خطأ في المعاينة: {e}")

    def _open_tryon(self):
        if self._tryon_thread is not None and self._tryon_thread.isRunning():
            QMessageBox.information(self, "تنبيه", "معالجة الإلباس جارية — فضلاً انتظر.")
            return
        # نافذة اختيار الملابس (مع إمكانية إضافة ملابس جديدة)
        picker = GarmentPickerDialog(self)
        if picker.exec() != QDialog.Accepted:
            return
        garment_path = picker.selected_path
        if not garment_path:
            return
        try:
            garment_pil = Image.open(garment_path).convert("RGB")
        except Exception as e:
            QMessageBox.warning(self, "خطأ", f"تعذّر فتح صورة الملابس:\n{e}")
            return

        # صورة الشخص = الصورة الحالية في نافذة التعديل
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        self._current.toImage().save(buf, "PNG")
        buf.close()
        try:
            person_pil = Image.open(io.BytesIO(bytes(ba.data()))).convert("RGB")
        except Exception as e:
            QMessageBox.warning(self, "خطأ", f"تعذّر تجهيز صورة الشخص:\n{e}")
            return

        self._btn_tryon.setEnabled(False)
        self._lbl_status.setText("جارٍ تجهيز الإلباس…")
        self._progress.show()
        self._tryon_thread = TryOnProcessingThread(person_pil, garment_pil,
                                                   garment_path)
        self._tryon_thread.result_ready.connect(self._on_tryon_result)
        self._tryon_thread.failed.connect(self._on_tryon_failed)
        self._tryon_thread.progress_msg.connect(
            lambda t: self._lbl_status.setText(t))
        self._tryon_thread.start()

    def _open_tryon_free_cfg(self):
        if self._tryon_thread is not None and self._tryon_thread.isRunning():
            QMessageBox.information(self, "تنبيه", "معالجة الإلباس جارية — انتظر اكتمالها.")
            return
        dlg = TryOnFreeCfgDialog(self)
        dlg.exec()

    def _on_tryon_result(self, out_pil):
        try:
            out_pil = out_pil.convert("RGBA")
            try:
                from core.photo_processor import composite_white_bg, resize_to_photo_size
                out_pil = composite_white_bg(out_pil).convert("RGBA")
                # لا نقصّ ولا نغيّر نسب الجسد والرأس — نحافِظ على حجمهما الطبيعي
                # كما أخرجه النموذج، فقط خلفية بيضاء وتصغير لو تجاوز المربع.
                out_pil = resize_to_photo_size(out_pil, 35, 45)
            except Exception:
                bg = Image.new("RGB", out_pil.size, (255, 255, 255, 255))
                bg.paste(out_pil, mask=out_pil.split()[-1])
                out_pil = bg
            qimg = QImage(out_pil.tobytes("raw", "RGBA"),
                          out_pil.size[0], out_pil.size[1],
                          out_pil.size[0] * 4, QImage.Format_RGBA8888)
            self._current = QPixmap.fromImage(qimg.copy())
            self._base = self._current
            self._orig_bgr = None
            self._cached_face_mask = None
            self._recreate_scene(self._current)
            self._zoom_fit()
            self._lbl_status.setText("تم إلباس الملابس ✓ — اضغط «تطبيق» للموافقة.")
            # الوضعية الجديدة غير مناسبة لمحرّرات الوجه (صفّرها)
            for s in self._sliders.values():
                s.slider.setValue(0)
        except Exception as e:
            self._lbl_status.setText(f"خطأ في عرض النتيجة: {e}")
        finally:
            self._btn_tryon.setEnabled(True)
            self._progress.hide()

    def _on_tryon_failed(self, err):
        self._btn_tryon.setEnabled(True)
        self._progress.hide()
        self._lbl_status.setText("فشل الإلباس.")
        QMessageBox.warning(self, "خطأ", f"تعذّرت عملية الإلباس:\n{err}")

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
        _ref = min(image_rect.width(), image_rect.height()) if image_rect.width() > 0 and image_rect.height() > 0 else 200
        self._min_size = max(8, _ref * 0.02)
        self._handle_r = max(3, _ref * 0.008)
        self._hit_margin = max(4, _ref * 0.006)

        self._border = QGraphicsRectItem()
        self._border.setPen(QPen(QColor("#1a73e8"), max(1.0, _ref * 0.003)))
        self._border.setZValue(10)
        self._border.setAcceptedMouseButtons(Qt.NoButton)
        scene.addItem(self._border)

        self._dim_items = []
        for _ in range(4):
            item = QGraphicsRectItem()
            item.setBrush(QBrush(QColor(0, 0, 0, 160)))
            item.setPen(QPen(Qt.NoPen))
            item.setZValue(9)
            item.setAcceptedMouseButtons(Qt.NoButton)
            scene.addItem(item)
            self._dim_items.append(item)

        self._handles = []
        hr = self._handle_r
        for _ in range(4):
            h = QGraphicsRectItem(-hr, -hr, hr * 2, hr * 2)
            h.setBrush(QBrush(QColor("#1a73e8")))
            h.setPen(QPen(Qt.white, max(1.0, _ref * 0.002)))
            h.setZValue(11)
            h.setAcceptedMouseButtons(Qt.NoButton)
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

    def set_visible(self, visible):
        self._border.setVisible(visible)
        for it in self._dim_items:
            it.setVisible(visible)
        for it in self._handles:
            it.setVisible(visible)

    def rect(self):
        return QRectF(self._rect)

    def hit_test(self, scene_pos):
        r = self._rect
        margin = self._hit_margin
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
        self._brush_last = None

    def wheelEvent(self, event):
        factor = 1.15 ** (event.angleDelta().y() / 120.0)
        self.scale(factor, factor)

    def mousePressEvent(self, event):
        dlg = self._dialog
        if dlg._poly_mode:
            if event.button() == Qt.LeftButton:
                dlg._add_poly_point(self.mapToScene(event.position().toPoint()))
            return
        if dlg._brush_mode:
            if event.button() == Qt.LeftButton:
                self._brush_last = self.mapToScene(event.position().toPoint())
                dlg._apply_brush_stroke(self._brush_last)
            return
        if event.button() == Qt.LeftButton and dlg._overlay:
            scene_pos = self.mapToScene(event.position().toPoint())
            edges = dlg._overlay.hit_test(scene_pos)
            if edges:
                self._drag_edge = edges
                self._drag_rect = QRectF(dlg._overlay.rect())
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        dlg = self._dialog
        scene_pos = self.mapToScene(event.position().toPoint())

        if dlg._poly_mode:
            self.viewport().setCursor(Qt.CrossCursor)
            super().mouseMoveEvent(event)
            return

        if dlg._brush_mode:
            if self._brush_last is not None and (event.buttons() & Qt.LeftButton):
                dlg._apply_brush_stroke(scene_pos, self._brush_last)
                self._brush_last = scene_pos
            else:
                self.viewport().setCursor(Qt.CrossCursor)
            super().mouseMoveEvent(event)
            return

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
        self._brush_last = None
        super().mouseReleaseEvent(event)

class PhotoProcessingThread(QThread):
    photo_ready = Signal(bytes, int)
    finished_all = Signal()

    _SENTINEL = object()

    def __init__(self, parent=None):
        super().__init__(parent)
        import queue
        self._q = queue.Queue()
        self._stop = False

    MAX_PROCESS_PX = 1200

    def enqueue(self, paths_or_bytes, global_start):
        """إضافة دفعة صور لمعالجتها تباعاً (نفس الخيط، بدون تكوين خيط ثانٍ)."""
        self._q.put((global_start, list(paths_or_bytes)))

    def stop(self):
        """طلب إنهاء الخيط بأمان بعد إنهاء الأعمال المعلقة."""
        self._stop = True
        self._q.put((0, [self._SENTINEL]))

    def run(self):
        # الاستيراد يتم داخل try: أي فشل هنا كان يقتل الخيط صامتاً
        # ويُبقي كل الصور في حالة "معالجة" (قناع أسود + مؤشر دوّار) للأبد.
        try:
            from core.photo_processor import remove_background, composite_white_bg
        except Exception as e:
            logger.error("تعذّر تحميل وحدة معالجة الصور: %s", e)
            remove_background = None
            composite_white_bg = None
        while True:
            try:
                start_idx, items = self._q.get(timeout=1.0)
            except Exception:
                if self._stop:
                    break
                continue
            if items == [self._SENTINEL]:
                break
            for i, pb in enumerate(items):
                try:
                    pil_bytes = self._process_one(pb, remove_background, composite_white_bg)
                    self.photo_ready.emit(pil_bytes or b"", start_idx + i)
                except Exception as e:
                    logger.error("فشل معالجة الصورة %d: %s", start_idx + i, e)
                    try:
                        pil_bytes = self._process_one(pb, remove_background, composite_white_bg, fallback=True)
                        self.photo_ready.emit(pil_bytes or b"", start_idx + i)
                    except Exception as e2:
                        logger.error("فشل عرض الصورة %d: %s", start_idx + i, e2)
            self.finished_all.emit()
        self.finished_all.emit()

    def _process_one(self, pb, remove_background=None, composite_white_bg=None, fallback=False):
        pil = _safe_open_image(pb)
        if pil is None:
            raise ValueError("تعذّر فك ترميز الصورة (صيغة غير مدعومة مثل HEIC أو ملف تالف)")
        pil = pil.convert("RGB")
        # المقاس المستهدف يأتي من المحرر (المقاس المختار حالياً) لا من قيمة ثابتة
        target = self._target_size()
        try:
            from core.photo_processor import auto_fit_card_size
            pil = auto_fit_card_size(pil, target[0], target[1])
        except Exception as e:
            logger.error("تعذّر تطبيق الملاءمة التلقائية للمقاس: %s", e)
        keep_bg = getattr(self.parent(), '_keep_bg', False)
        if not fallback and not keep_bg and remove_background is not None and composite_white_bg is not None:
            try:
                no_bg = remove_background(pil)
                no_bg = composite_white_bg(no_bg)
                pil = no_bg
            except Exception as e:
                logger.error("تعذّر إزالة الخلفية: %s", e)
        if max(pil.size) > self.MAX_PROCESS_PX:
            pil.thumbnail((self.MAX_PROCESS_PX, self.MAX_PROCESS_PX), Image.LANCZOS)
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        return buf.getvalue()

    def _target_size(self):
        """المقاس المختار حالياً في المحرر (بالمليمتر)."""
        parent = self.parent()
        try:
            return (parent._pw(), parent._ph())
        except Exception:
            return (30, 40)

class PhotoEditor(QWidget):
    go_back = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayoutDirection(Qt.RightToLeft)
        self.photos = []
        self._num_pages = 1
        self._size_key = self._load_saved_size()
        self._paper_key = DEFAULT_PAPER_KEY
        self._copied_pixmap = None
        self._undo_stack = []
        self._count_field = None
        self._count_hint = None
        self._progress_total = 0
        self._progress_count = 0
        self._broken_exe_warned = False
        self._unsupported_warned = False
        self._keep_bg = False
        self._build_ui()
        self._add_shortcuts()

    def _load_saved_size(self):
        """آخر مقاس استخدمه المستخدم (يُحفظ بين الجلسات). الافتراضي 4 سم."""
        try:
            settings = QSettings("ورشة طباعة", "PhotoEditor")
            key = settings.value("lastPhotoSize", "", type=str)
        except Exception:
            key = ""
        if key and key in PHOTO_SIZES:
            return key
        return DEFAULT_PHOTO_SIZE_KEY

    def _save_size(self, key):
        try:
            settings = QSettings("ورشة طباعة", "PhotoEditor")
            settings.setValue("lastPhotoSize", key)
        except Exception as e:
            logger.error("تعذّر حفظ المقاس: %s", e)

    def _warn_unsupported_file(self, pb):
        name = os.path.basename(pb) if isinstance(pb, str) else "ملف مُلصَق"
        if self._unsupported_warned:
            return
        self._unsupported_warned = True
        QMessageBox.warning(
            self, "ملف غير مدعوم",
            "تعذّر فتح صورة: %s\n\n"
            "الصيغة غير مدعومة (مثل HEIC) أو الملف تالف.\n"
            "استخدم صور JPG/PNG بدلاً منها." % name)

    def _on_keep_bg_toggled(self, checked):
        """عند التفعيل تُضاف الصور الجديدة بدون حذف الخلفية."""
        self._keep_bg = bool(checked)

    def closeEvent(self, event):
        thr = getattr(self, '_thread', None)
        if thr is not None:
            thr.stop()
            thr.wait(3000)
            thr.photo_ready.disconnect()
            thr.finished_all.disconnect()
        super().closeEvent(event)

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
        btn_save_images = QPushButton("🖼️ حفظ الصور")
        btn_save_images.setToolTip("حفظ كل صورة شخصية منفصلة كما هي بعد حذف الخلفية/التعديل")
        btn_save_images.clicked.connect(self._save_images)
        btn_clear = QPushButton("تفريغ الكل")
        btn_clear.clicked.connect(self._clear_all)

        self._btn_frame = QPushButton("إطار")
        self._btn_frame.setCheckable(True)
        self._btn_frame.setToolTip("إظهار إطار أسود خفيف حول صور البطاقات عند الطباعة")
        self._btn_frame.toggled.connect(self._on_frame_toggled)
        self._frame_enabled = False

        self._size_combo = QComboBox()
        self._size_combo.addItem("الحجم")
        for key in PHOTO_SIZES:
            self._size_combo.addItem(key)
        self._size_combo.setToolTip("مقاس صورة البطاقة (يُحفظ آخر مقاس استخدمته)")
        self._size_combo.activated.connect(self._on_size_activated)
        self._sync_size_combo_label()

        self._paper_combo = QComboBox()
        for key in PAPER_SIZES:
            self._paper_combo.addItem(key)
        self._paper_combo.setCurrentText(self._paper_key)
        self._paper_combo.setToolTip("حجم الورق (ورقة المحرر)")
        self._paper_combo.activated.connect(self._on_paper_activated)

        btn_bar.addWidget(btn_back)
        btn_bar.addWidget(btn_add)
        btn_bar.addWidget(btn_print)
        btn_bar.addWidget(btn_save_pdf)
        btn_bar.addWidget(btn_save_images)
        btn_bar.addWidget(btn_clear)
        btn_bar.addWidget(self._btn_frame)

        self._btn_keep_bg = QPushButton("بدون حذف")
        self._btn_keep_bg.setCheckable(True)
        self._btn_keep_bg.setToolTip("عند تفعيله تُضاف الصور بدون حذف خلفيتها")
        self._btn_keep_bg.toggled.connect(self._on_keep_bg_toggled)
        btn_bar.addWidget(self._btn_keep_bg)
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
        btn_bar.addWidget(self._paper_combo)
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
        self.scene.setSceneRect(0, 0, self._paper_w(), self._paper_h())
        self._draw_page_area(0)
        self.view = PhotoGraphicsView(self.scene, self)
        self.view.digit_pressed.connect(self._on_count_digit)
        layout.addWidget(self.view)
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
        return (self._size_key, self._paper_key, photos)

    def _push_undo(self):
        state = self._snapshot_state()
        self._undo_stack.append(state)
        if len(self._undo_stack) > 15:
            self._undo_stack.pop(0)

    def _undo(self):
        if not self._undo_stack:
            return
        state = self._undo_stack.pop()
        size_key, paper_key, photos_data = state
        self._size_key = size_key
        self._save_size(size_key)
        self._sync_size_combo_label()
        self._paper_key = paper_key
        self._paper_combo.setCurrentText(paper_key)
        self.scene.clear()
        self.photos.clear()
        self._num_pages = 1
        self.scene.setSceneRect(0, 0, self._paper_w(), self._paper_h())
        self._draw_page_area(0)
        for img, rotation in photos_data:
            self._place_photo(QPixmap.fromImage(img))
            if rotation:
                self.photos[-1].set_item_rotation(rotation)
        logger.info("تم التراجع عن آخر إجراء (%d صورة)", len(self.photos))

    def _sync_size_combo_label(self):
        """إظهار المقاس الحالي في أول عنصر من قائمة الحجم."""
        try:
            self._size_combo.setItemText(0, "الحجم (%s)" % self._size_key)
        except Exception:
            pass

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

    def _paper_w(self):
        return PAPER_SIZES[self._paper_key][0]

    def _paper_h(self):
        return PAPER_SIZES[self._paper_key][1]

    def _cols(self):
        return max(1, (self._paper_w() - 2 * MARGIN + GAP) // (self._pw() + GAP))

    def _rows(self):
        return max(1, (self._paper_h() - 2 * MARGIN + GAP) // (self._ph() + GAP))

    def _per_page(self):
        return self._cols() * self._rows()

    def _draw_page_area(self, page_index):
        y0 = page_index * self._paper_h()
        page_rect = self.scene.addRect(0, y0, self._paper_w(), self._paper_h(), QPen(Qt.NoPen), QBrush(Qt.white))
        page_rect.setZValue(-1)
        page_rect.setData(NO_PRINT_KEY, True)
        pen = QPen(QColor(200, 200, 200, 60), 0.3, Qt.DashLine)
        pw, ph = self._pw(), self._ph()
        cols = self._cols()
        x = MARGIN
        for col in range(cols):
            y = y0 + MARGIN
            while y + ph <= y0 + self._paper_h() - MARGIN:
                guide = self.scene.addRect(x, y, pw, ph, pen)
                guide.setData(NO_PRINT_KEY, True)
                y += ph + GAP
            x += pw + GAP

    def _ensure_page(self, page_index):
        while page_index >= self._num_pages:
            self._draw_page_area(self._num_pages)
            self._num_pages += 1
            self.scene.setSceneRect(0, 0, self._paper_w(), self._num_pages * self._paper_h())
        return page_index * self._paper_h()

    def _grid_pos(self, idx):
        page_idx = idx // self._per_page()
        local_idx = idx % self._per_page()
        y_offset = page_idx * self._paper_h()
        cols = self._cols()
        col = local_idx % cols
        row = local_idx // cols
        x = MARGIN + col * (self._pw() + GAP)
        y = y_offset + MARGIN + row * (self._ph() + GAP)
        return x, y

    def _place_photo(self, pixmap, source_bytes=None):
        idx = len(self.photos)
        self._ensure_page(idx // self._per_page())
        pw = self._pw()
        ph = self._ph()
        item = PhotoItem(pixmap, pw, ph, index=idx)
        item._source_bytes = source_bytes
        item.set_frame(self._frame_enabled)
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
        if getattr(self, '_thread', None) is None:
            self._thread = PhotoProcessingThread(self)
            self._thread.photo_ready.connect(self._on_photo_ready)
            self._thread.finished_all.connect(self._on_processing_done)
            self._thread.start()
        self._progress_total += len(paths_or_bytes_list)
        self._progress_count = self._progress_count
        self._progress_bar.setRange(0, self._progress_total)
        self._progress_bar.setValue(self._progress_count)
        self._progress_bar.setFormat(f"{self._progress_count}/{self._progress_total}")
        self._progress_bar.show()
        old_len = len(self.photos)
        # تُبنى بالتوازي مع الوضع الفعلي: كل عنصر هنا يقابل صورة وُضعت فعلاً،
        # وإلا اختلّ ترتيب الفهارس وظهرت الصورة المعالجة في مكان خاطئ.
        pending = []
        for pb in paths_or_bytes_list:
            try:
                orig = _safe_open_image(pb)
                if orig is None:
                    self._warn_unsupported_file(pb)
                    continue
                if orig.mode != "RGB":
                    orig = orig.convert("RGB")
                # تحويل تلقائي لمقاس البطاقة المختار (يُترك كما هو إن كان بمقاس البطاقة)
                try:
                    from core.photo_processor import auto_fit_card_size
                    orig = auto_fit_card_size(orig, self._pw(), self._ph())
                except Exception as e:
                    logger.error("تعذّر تطبيق الملاءمة التلقائية للمقاس: %s", e)
                buf = io.BytesIO()
                orig.save(buf, format="PNG")
                src_bytes = buf.getvalue()
                qpix = QPixmap()
                qpix.loadFromData(src_bytes)
                if not qpix.isNull():
                    self._place_photo(qpix, source_bytes=src_bytes)
                    self.photos[-1].set_processing(True)
                    pending.append(pb if isinstance(pb, str) else io.BytesIO(pb).getvalue())
            except Exception as e:
                logger.error("فشل عرض الصورة الأصلية: %s", e)
        if pending:
            self._thread.enqueue(pending, old_len)

    def _on_photo_ready(self, data, index):
        if 0 <= index < len(self.photos):
            item = self.photos[index]
            qpix = QPixmap()
            if data:
                qpix.loadFromData(data)
            if not qpix.isNull():
                item.setPixmap(qpix)
                item._original_pixmap = qpix
            else:
                logger.error("تعذّر عرض الصورة المعالَجة رقم %d", index)
            # إزالة قناع المعالجة في كل الأحوال حتى لا تظل الصورة مظلمة مع مؤشر دوّار
            item.set_processing(False)
        self._progress_count += 1
        self._progress_bar.setValue(self._progress_count)
        self._progress_bar.setFormat(f"{self._progress_count}/{self._progress_total}")

    def _on_processing_done(self):
        if self._progress_count >= self._progress_total:
            self._progress_bar.setRange(0, 0)
            self._progress_bar.setFormat("")
            self._progress_bar.hide()

    def add_image(self, path_or_bytes):
        self.add_images([path_or_bytes])

    def _on_size_changed(self, key):
        if key not in PHOTO_SIZES:
            return
        self._size_key = key
        self._save_size(key)
        self._sync_size_combo_label()
        saved = [(item.pixmap(), item.item_rotation()) for item in self.photos]
        self.scene.clear()
        self.photos.clear()
        self._num_pages = 1
        self.scene.setSceneRect(0, 0, self._paper_w(), self._paper_h())
        self._draw_page_area(0)
        for pixmap, rotation in saved:
            self._place_photo(pixmap)
            if rotation:
                self.photos[-1].set_item_rotation(rotation)
        logger.info("تم تغيير الحجم إلى %s (عدد الصور %d)", key, len(self.photos))

    def _on_paper_activated(self, index):
        key = self._paper_combo.itemText(index)
        if key not in PAPER_SIZES or key == self._paper_key:
            return
        self._push_undo()
        self._paper_key = key
        self._relayout_all()
        logger.info("تم تغيير حجم الورق إلى %s", key)

    def _on_frame_toggled(self, enabled):
        self._frame_enabled = enabled
        for item in self.photos:
            item.set_frame(enabled)
        self.view.viewport().update()

    def _relayout_all(self):
        saved = [(item.pixmap(), item.item_rotation(), getattr(item, '_frame', False)) for item in self.photos]
        self.scene.clear()
        self.photos.clear()
        self._num_pages = 1
        self.scene.setSceneRect(0, 0, self._paper_w(), self._paper_h())
        self._draw_page_area(0)
        for pixmap, rotation, frame in saved:
            self._place_photo(pixmap)
            if rotation:
                self.photos[-1].set_item_rotation(rotation)
            if frame:
                self.photos[-1].set_frame(True)
        self._zoom_fit()

    def _clear_all(self):
        if not self.photos:
            return
        self._push_undo()
        self.scene.clear()
        self.photos.clear()
        self._num_pages = 1
        self.scene.setSceneRect(0, 0, self._paper_w(), self._paper_h())
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
                self._add_copies(item, count)
                return

    def _set_photo_count(self, count):
        selected = [it for it in self.photos if it.isSelected()]
        original = next((it for it in selected if isinstance(it, PhotoItem)), None)
        if original is None:
            return
        group = original._group
        if group is None:
            group = [original]
            original._group = group
        current = len(group)
        if count == current:
            return
        self._push_undo()
        if count > current:
            self._add_copies(original, count - current)
        else:
            for item in group[count:]:
                self.scene.removeItem(item)
                if item in self.photos:
                    self.photos.remove(item)
            del group[count:]
        self.scene.clearSelection()
        leader = group[0]
        leader.setSelected(True)
        self.view.centerOn(leader)
        logger.info("عدد نسخ الصورة أصبح %d", count)

    def _on_count_digit(self, digit):
        selected = [it for it in self.photos if it.isSelected()]
        original = next((it for it in selected if isinstance(it, PhotoItem)), None)
        if original is None:
            return
        self._ensure_count_field()
        c = self.view.mapFromScene(original.sceneBoundingRect().center())
        self._count_field.setText(str(digit))
        self._count_field.setSelection(len(str(digit)), 0)
        self._count_field.setFixedSize(56, 40)
        self._count_field.move(int(c.x() - 28), int(c.y() - 20))
        self._count_field.show()
        self._count_field.setFocus()
        self._count_field.selectAll()
        self._show_count_hint_once(int(c.x()), int(c.y()))

    def _ensure_count_field(self):
        if getattr(self, '_count_field', None) is None:
            self._count_field = QLineEdit(self.view.viewport())
            self._count_field.setValidator(QIntValidator(1, 99, self))
            self._count_field.setAlignment(Qt.AlignCenter)
            self._count_field.setMaxLength(2)
            self._count_field.setStyleSheet(
                "font-size:22px;font-weight:bold;color:#000;background:#fff;"
                "border:2px solid #0078d4;border-radius:4px;padding:0px;")
            self._count_field.returnPressed.connect(self._commit_count_field)
            self._count_field.editingFinished.connect(self._hide_count_field)
            self._count_hint = QLabel(self.view.viewport())
            self._count_hint.setWordWrap(True)
            self._count_hint.setStyleSheet(
                "color:#155;background:#e6f7ff;border:1px solid #91d5ff;"
                "border-radius:4px;padding:4px;font-size:12px;")
            self._count_hint.setMaximumWidth(220)
            self._count_hint.move(0, 0)
            self._count_hint.hide()

    def _commit_count_field(self):
        text = self._count_field.text().strip()
        self._count_field.hide()
        if self._count_hint is not None:
            self._count_hint.hide()
        if not text or not text.isdigit():
            return
        count = int(text)
        if count == 0:
            count = 10
        self._set_photo_count(count)

    def _hide_count_field(self):
        if getattr(self, '_count_field', None) is not None:
            self._count_field.hide()

    def _show_count_hint_once(self, x, y):
        settings = QSettings("ورشة طباعة", "PhotoEditor")
        if settings.value("countHintShown", False, type=bool):
            return
        self._count_hint.setText(
            "اكتب عدد النسخ (رقمان كحد أقصى) ثم اضغط Enter.\n"
            "مثال: 5 تعني 5 نسخ من هذه الصورة.")
        self._count_hint.adjustSize()
        self._count_hint.move(max(0, x - 110), y + 26)
        self._count_hint.show()
        settings.setValue("countHintShown", True)

    def _slot_of(self, item):
        pw, ph = self._pw(), self._ph()
        x, y = item.pos().x(), item.pos().y()
        page = max(0, int(y // self._paper_h()))
        local_y = y - page * self._paper_h()
        col = max(0, int(round((x - MARGIN) / (pw + GAP))))
        row = max(0, int(round((local_y - MARGIN) / (ph + GAP))))
        return page * self._per_page() + row * self._cols() + col

    def _duplicate_item(self, original, slot_index=None):
        group = original._group
        if group is None:
            group = [original]
            original._group = group
        item = PhotoItem(QPixmap(original.pixmap()), self._pw(), self._ph(), index=len(self.photos))
        item._original_pixmap = original._original_pixmap
        item._source_pixmap = original._source_pixmap
        item._crop_rect = original._crop_rect
        item._adjust_settings = dict(original._adjust_settings) if original._adjust_settings else {}
        item._group = group
        if slot_index is None:
            slot_index = len(self.photos)
        item.setPos(*self._grid_pos(slot_index))
        item.set_item_rotation(original.item_rotation())
        self.scene.addItem(item)
        self.photos.append(item)
        group.append(item)
        return item

    def _add_copies(self, original, extra):
        group = original._group
        if group is None:
            group = [original]
            original._group = group
        used = {self._slot_of(it) for it in self.photos}
        start = self._slot_of(original)
        k = start + 1
        added = 0
        while added < extra:
            if k not in used:
                self._ensure_page(k // self._per_page())
                self._duplicate_item(original, k)
                used.add(k)
                added += 1
            k += 1

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
        h = self._num_pages * self._paper_h()
        self.scene.setSceneRect(0, 0, self._paper_w(), h)
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
            bg_items = [it for it in self.scene.items()
                        if not isinstance(it, PhotoItem) and it.isVisible()]
            for it in bg_items:
                it.setVisible(False)
            try:
                self.scene._printing = True
            except Exception:
                pass
            printer = QPrinter()
            printer.setOutputFormat(QPrinter.PdfFormat)
            printer.setOutputFileName(path)
            paper_w, paper_h = self._paper_w(), self._paper_h()
            printer.setPageSize(QPageSize(QSizeF(paper_w, paper_h), QPageSize.Millimeter))
            printer.setFullPage(False)
            painter = QPainter(printer)
            if not painter.isActive():
                logger.error("فشل بدء رسم الحفظ PDF")
                QMessageBox.warning(self, "خطأ", "فشل بدء رسم الحفظ PDF")
                for it in bg_items:
                    it.setVisible(True)
                try:
                    self.scene._printing = False
                except Exception:
                    pass
                return
            try:
                page_rect = printer.pageRect(QPrinter.Millimeter)
                scale_x = page_rect.width() / paper_w
                scale_y = page_rect.height() / paper_h
                scale = min(scale_x, scale_y)
                for page_idx in range(1, self._num_pages + 1):
                    if page_idx > 1:
                        printer.newPage()
                    painter.save()
                    painter.scale(scale, scale)
                    source = QRectF(0, (page_idx - 1) * paper_h, paper_w, paper_h)
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
                try:
                    self.scene._printing = False
                except Exception:
                    pass
            for item in selected:
                item.setSelected(True)

    def _save_images(self):
        if not self.photos:
            QMessageBox.information(self, "تنبيه", "لا توجد صور للحفظ.")
            return

        folder = QFileDialog.getExistingDirectory(self, "اختر مجلد حفظ الصور")
        if not folder:
            return

        if not getattr(self, 'subscription_check', lambda: True)():
            return

        saved = 0
        for i, item in enumerate(self.photos, start=1):
            pix = item.pixmap()
            if pix is None or pix.isNull():
                continue
            base = "صورة_%d" % i
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
            bg_items = [it for it in self.scene.items()
                        if not isinstance(it, PhotoItem) and it.isVisible()]
            for it in bg_items:
                it.setVisible(False)
            print_scene(self, self.scene, copies=copies, page_count=self._num_pages, duplex=duplex,
                        page_range=dialog.page_range(), paper_type=pt,
                        paper_width_mm=self._paper_w(), paper_height_mm=self._paper_h())
            for it in bg_items:
                it.setVisible(True)
            for item in selected:
                item.setSelected(True)
