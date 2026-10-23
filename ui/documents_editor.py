import logging
import io
import time
import numpy as np
from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QVBoxLayout,
                               QPushButton, QWidget, QHBoxLayout, QFileDialog,
                               QMessageBox, QLabel, QGraphicsPixmapItem,
                               QGraphicsItem, QGraphicsRectItem, QDialog,
                               QStyle, QMenu, QProgressBar, QSlider)
from PySide6.QtCore import (Qt, Signal, QRectF, QThread, QTimer, QSizeF,
                            QBuffer, QIODevice, QPointF)
from PySide6.QtGui import (QPixmap, QPainter, QPen, QColor, QBrush,
                           QShortcut, QKeySequence, QPainterPath, QPageSize,
                           QImage, QTransform, QFont)
from PySide6.QtPrintSupport import QPrinter
from PIL import Image
from core.printer import print_scene, set_printer_name, set_last_paper_type, NO_PRINT_KEY
from ui.a4_editor import PrintSetupDialog, A4_W, A4_H, _CropOverlay, _adjust_brightness_pixmap
from ui import theme

logger = logging.getLogger(__name__)

DOC_MARGIN = 0
STRETCH_HANDLE_SIZE = 8
PAGE_GAP = 5
DEFAULT_DPI = 300.0
# وثيقة بنسب ورقة A4 (نحو 210:297 أو 297:210) وبحجم صفحة كاملة تُفرد على
# الورقة بأكملها مهما كانت بيانات dpi المخزّنة مغلوطة أو مفقودة — فلا تتقلّص
# عند الإضافة. التسامح 4% (أوسع قليلاً من خطأ التصدير المعتاد) ولا يلتقط
# نسب 4:3 لصور الكاميرا؛ والحدّ 600 بكسل يمنع تكبير الرسوم الصغيرة.
A4_ASPECT_TOL = 0.04
A4_MIN_DOC_PX = 600


def _pil_dpi(image, default=DEFAULT_DPI):
    """دقة الصورة بالنقاط/البوصة كما هي مخزّنة في ملفها.

    تُقرأ من بيانات الملف لعرض الورقة بحجمها الحقيقي؛ وعند غيابها تُفترض
    300 نقطة/بوصة (دقة المسح المتعارف عليها) لئلا تتغيّر أحجام الصور.
    """
    try:
        dpi = (getattr(image, 'info', None) or {}).get('dpi')
        if isinstance(dpi, (tuple, list)):
            dpi = dpi[0] if dpi else None
        dpi = float(dpi)
        if 20.0 <= dpi <= 4800.0:
            return dpi
    except (TypeError, ValueError):
        pass
    return default


def _image_dpi(pixmap, default=DEFAULT_DPI):
    """دقة QPixmap بالنقاط/البوصة (من بيانات الملف) أو الافتراضي."""
    if pixmap is None or pixmap.isNull():
        return default
    try:
        dppm = pixmap.dppmX() or pixmap.dppmY()
    except (AttributeError, TypeError):
        dppm = 0
    if dppm and dppm > 1:
        return dppm * 0.0254
    return default


def _perspective_crop_document(pixmap, points):
    """قصّ رباعي من النقاط الأربع إلى مستطيل مستقيم بنسبة A4 تماماً.

    يتجاهل نسبة الرسم الأصلي ويُخرِج وثيقة بنسبة A4 (عمودية 210:297 أو
    عرضية 297:210 حسب اتجاه الرباعي)، فتُملأ الصفحة كأنها ورق A4 حقيقي.
    """
    import cv2
    buf = QBuffer()
    buf.open(QIODevice.ReadWrite)
    pixmap.save(buf, "PNG")
    buf.seek(0)
    raw = buf.data().data()
    buf.close()
    arr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        return pixmap
    pts = np.array([(p.x(), p.y()) if hasattr(p, "x") else (p[0], p[1])
                    for p in points], dtype=np.float32).reshape(4, 2)
    ordered = _order_quad_ccw(pts)
    (tl, tr, br, bl) = ordered
    w_top = np.linalg.norm(tr - tl)
    w_bot = np.linalg.norm(br - bl)
    h_lft = np.linalg.norm(bl - tl)
    h_rgt = np.linalg.norm(br - tr)
    w = max(w_top, w_bot)
    h = max(h_lft, h_rgt)
    if w < 10 or h < 10:
        return pixmap
    # نسبة A4: عمودية إذا كان الرباعي أطول من أعرض، وإلا عرضية.
    portrait = h >= w
    if portrait:
        dst_w, dst_h = int(round(A4_W * 4.0)), int(round(A4_H * 4.0))
    else:
        dst_w, dst_h = int(round(A4_H * 4.0)), int(round(A4_W * 4.0))
    dst = np.array([[0, 0], [dst_w - 1, 0],
                    [dst_w - 1, dst_h - 1], [0, dst_h - 1]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(ordered, dst)
    box_arr = cv2.warpPerspective(arr, M, (dst_w, dst_h))
    box_arr = cv2.cvtColor(box_arr, cv2.COLOR_BGR2RGB)
    ch = 3
    h2, w2 = box_arr.shape[:2]
    bytes_per_line = ch * w2
    qimg = QImage(box_arr.data.tobytes(), w2, h2, bytes_per_line, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimg)


def _order_quad_ccw(pts):
    """ترتيب النقاط الأربع TL/TR/BR/BL بعكس عقارب الساعة (يحفظ اتجاه الرسم)."""
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    c = pts.mean(axis=0)
    ang = np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0])
    ordered = pts[np.argsort(ang)]
    ys = ordered[:, 1]
    k = int(np.argmin(ys))
    if abs(ys[k] - ys[(k + 3) % 4]) < 1e-6:
        if ordered[(k + 3) % 4, 0] < ordered[k, 0]:
            k = int((k + 3) % 4)
    ordered = np.roll(ordered, -k, axis=0)
    return ordered


class DocumentItem(QGraphicsPixmapItem):
    """وثيقة A4 موضوعة في خانة صفحة."""
    def __init__(self, pixmap, pw, ph, index=0, parent=None):
        super().__init__(pixmap, parent)
        self.index = index
        self._pw = pw
        self._ph = ph
        self._rotation = 0
        self._original_pixmap = pixmap
        self._source_pixmap = pixmap
        self._adjust_settings = {}
        self._source_bytes = None
        self._processing = False
        self._spin_angle = 0
        self._spin_timer = None
        self.on_double_clicked = None
        self.on_moved = None
        self._zoom = 1.0
        self._sx = 1.0
        self._sy = 1.0
        # المقاس الطبيعي للوثيقة بالمليمتر (عرض×ارتفاع) كما تُطبع به فعلياً.
        # None = ملاءمة تلقائية تملأ الصفحة (القص اليدوي).
        self._nat_w_mm = None
        self._nat_h_mm = None
        self._offset = QPointF(0.0, 0.0)
        self._move_mode = False
        self._stretch_active = None
        self._stretch_last = None
        self._drag_panning = False
        self._drag_last = None
        self._press_scene_pos = None
        self._suppress_dblclick_until = 0.0
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)

    def set_item_rotation(self, angle):
        self._rotation = angle % 360
        self.update()

    def item_rotation(self):
        return self._rotation

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

    # مقياس العرض الأساسي: كم ملّيمتراً من الورق يساوي بكسل واحد من الصورة
    # الحالية. يُشتق من المقاس الطبيعي المحفوظ بالمليمتر، فيبقى حجم الوثيقة
    # على الورق ثابتاً مهما صُغّرت الصورة أثناء المعالجة أو تغيّر اتجاه
    # الصفحة؛ وإلا تُملأ الصفحة بالكامل (سلوك القص اليدوي).
    def _base_scale(self):
        source = self.pixmap()
        if source.isNull() or source.width() <= 0 or source.height() <= 0:
            return 1.0
        if self._nat_w_mm and self._nat_h_mm:
            return min(self._nat_w_mm / source.width(),
                       self._nat_h_mm / source.height())
        return min(self._pw / source.width(), self._ph / source.height())

    # عرض الصورة بحجمها الطبيعي: البكسل = 1/dpi بوصة على الورق، فتظهر ورقة
    # A4 بمقاسها الحقيقي (تملأ الصفحة)، وتظهر الصورة الصغيرة بحجمها الصغير
    # دون تصغير أو تكبير. وإن كانت الوثيقة بنسب ورقة A4 تماماً وبحجم صفحة
    # كاملة تُفرد على الورقة بأكملها — فلا تتقلّص عند غياب بيانات dpi من
    # الملف أو أخطائها (كالصور المصدَّرة بلا قياس مخزّن).
    def set_natural_scale(self, dpi=300.0):
        source = self.pixmap()
        if source.isNull() or source.width() <= 0 or source.height() <= 0:
            return
        try:
            d = float(dpi)
        except (TypeError, ValueError):
            d = 300.0
        if d <= 0:
            d = 300.0
        w, h = source.width(), source.height()
        a4 = A4_W / A4_H
        ar = w / h
        matches_a4 = (abs(ar - a4) <= A4_ASPECT_TOL or
                      abs(1.0 / ar - a4) <= A4_ASPECT_TOL)
        if matches_a4 and min(w, h) >= A4_MIN_DOC_PX:
            self._nat_w_mm = self._pw
            self._nat_h_mm = self._ph
        else:
            self._nat_w_mm = w * 25.4 / d
            self._nat_h_mm = h * 25.4 / d
        self._clamp_image()
        self.update()

    def _spin_tick(self):
        self._spin_angle = (self._spin_angle + 30) % 360
        self.update()

    def boundingRect(self):
        return QRectF(0, 0, self._pw, self._ph)

    def shape(self):
        # شكل العنصر هو الصورة نفسها (لا الصفحة كاملة)؛ فالنقر على الورقة
        # خارج الصورة لا يبتلعه العنصر ويتمكن التحديد من الاختفاء. عند التحديد
        # تُضمّن الدوائر وزر القلب في الشكل حتى تلتقف نقراتها بلا فقدان التحديد.
        path = self._rotated_frame_path()
        path.setFillRule(Qt.WindingFill)
        if self.isSelected():
            pad = 3.0
            for rr in self._handle_rects().values():
                path.addEllipse(rr.adjusted(-pad, -pad, pad, pad))
            rb = self._rotate_btn_rect()
            path.addEllipse(rb.adjusted(-3.0, -3.0, 3.0, 3.0))
        return path

    def paint(self, painter, option, widget):
        source = self.pixmap()
        if not source.isNull():
            painter.save()
            painter.setClipRect(QRectF(0, 0, self._pw, self._ph))
            painter.translate(self._pw / 2 + self._offset.x(),
                              self._ph / 2 + self._offset.y())
            # الدوران أولاً ثم مقاييس المحاور الذاتية: فيبقى تكبير/تصغير
            # أطوال الصورة بمحاورها الخاصة مطابقاً في كل زاوية ويطابق الإطار.
            painter.rotate(self._rotation)
            painter.scale(self._zoom * self._sx, self._zoom * self._sy)
            s = self._base_scale()
            bw = source.width() * s
            bh = source.height() * s
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.drawPixmap(QRectF(-bw / 2, -bh / 2, bw, bh), source, source.rect())
            painter.restore()
        selected = bool(option.state & QStyle.State_Selected)
        scene = self.scene()
        printing = bool(getattr(scene, "_printing", False)) if scene is not None else False
        if selected and not printing:
            painter.setClipRect(QRectF(0, 0, self._pw, self._ph))
            painter.setPen(QPen(QColor("#1a73e8"), 2.0))
            painter.setBrush(QBrush())
            painter.drawPath(self._rotated_frame_path())
            painter.setBrush(QBrush(QColor("#1a73e8")))
            painter.setPen(QPen(Qt.white, 1.2))
            for rr in self._handle_rects().values():
                painter.drawEllipse(rr)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setFont(QFont("Segoe UI", 9))
            rb = self._rotate_btn_rect()
            painter.drawEllipse(rb)
            painter.drawText(rb, Qt.AlignCenter, "↻")
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

    # أبعاد العرض الفعلية على الصفحة (بعد المقاس الأساسي والتكبير والإزاحة)
    # للصورة — تُحسب من قاعدة ثابتة مستقلة عن الدوران، وعند ربع الدورة تُبدّل
    # أطوال المحاور فقط دون إعادة ملاءمة، فيبقى حجم الصورة الذي عدّله المستخدم
    # ثابتاً مهما قلبتها.
    def _content_dims(self):
        source = self.pixmap()
        if source.isNull():
            return self._pw, self._ph
        s = self._base_scale()
        w = source.width() * s * self._zoom * self._sx
        h = source.height() * s * self._zoom * self._sy
        if self._rotation % 180 == 90:
            return h, w
        return w, h

    # أبعاد الصورة بمحاورها الذاتية قبل التطبيق الدوراني؛ يُستخدم في رسم
    # الإطار والدوائر بحيث تبقى منطبقة على حواف الصورة في أي زاوية.
    def _logical_dims(self):
        w, h = self._content_dims()
        if self._rotation % 180 == 90:
            return h, w
        return w, h

    # مستطيل إحاطة الصورة الفعلية (بعد التدوير) منطبقةً تماماً على حدودها
    # المرسومة — لا يبقى أي فراغ بين الصورة والإطار مهما كبرت أو تحركت.
    _AABB_MARGIN = 0.0

    def _image_aabb(self):
        w, h = self._logical_dims()
        cx = self._pw / 2 + self._offset.x()
        cy = self._ph / 2 + self._offset.y()
        pts = [QPointF(-w / 2, -h / 2), QPointF(w / 2, -h / 2),
               QPointF(w / 2, h / 2), QPointF(-w / 2, h / 2)]
        r = self._rotation % 360
        if r == 90:
            pts = [QPointF(-p.y(), p.x()) for p in pts]
        elif r == 180:
            pts = [QPointF(-p.x(), -p.y()) for p in pts]
        elif r == 270:
            pts = [QPointF(p.y(), -p.x()) for p in pts]
        x0 = min(p.x() + cx for p in pts)
        x1 = max(p.x() + cx for p in pts)
        y0 = min(p.y() + cy for p in pts)
        y1 = max(p.y() + cy for p in pts)
        m = self._AABB_MARGIN
        x0 = max(m, min(x0, self._pw - m))
        x1 = max(m, min(x1, self._pw - m))
        y0 = max(m, min(y0, self._ph - m))
        y1 = max(m, min(y1, self._ph - m))
        if x1 < x0:
            x1 = x0
        if y1 < y0:
            y1 = y0
        return QRectF(x0, y0, x1 - x0, y1 - y0)

    # 8 دوائر صغيرة على أطراف الصورة: الزوايا تكبّر/تصغّر العرض والارتفاع معاً،
    # ومنتصفات الأضلاع تكبّر/تصغّر محورها لوحده. تتبع الصورة في دورانها،
    # وتُثبّت مراكزها داخل حدود الصفحة حتى تبقى قابلة للنقر دائماً.
    def _handle_rects(self):
        s = STRETCH_HANDLE_SIZE / 2.0
        lo_x = s
        hi_x = self._pw - s
        lo_y = s
        hi_y = self._ph - s
        keys = ("NW", "N", "NE", "E", "SE", "S", "SW", "W")
        centers = {}
        for k, pt in zip(keys, self._content_outline()):
            centers[k] = QPointF(max(lo_x, min(hi_x, pt.x())),
                                 max(lo_y, min(hi_y, pt.y())))
        return {k: QRectF(c.x() - s, c.y() - s, 2 * s, 2 * s)
                for k, c in centers.items()}

    def _handle_at(self, local):
        for k, rect in self._handle_rects().items():
            if rect.contains(local):
                return k
        return None

    # زر التدوير: دائرة صغيرة أسفل الصورة مباشرة، يضغطها المستخدم ليدير الصورة.
    def _rotate_btn_rect(self):
        r = self._image_aabb()
        s = 16.0
        cx = r.center().x()
        y = r.bottom() + s / 2 + 4
        if y + s / 2 > self._ph:
            y = r.bottom() - s / 2 - 4
        if y - s / 2 < 0:
            y = s / 2 + 2
        return QRectF(cx - s / 2, y - s / 2, s, s)

    # إبقاء محتوى الصورة دائماً داخل إطار الصفحة مهما سُحبت: إن كانت أكبر من
    # الصفحة فلا تظهر فراغات بيضاء، وإن كانت أصغر فلا تخرج عن الحواف.
    def _clamp_image(self):
        w, h = self._content_dims()
        m = abs(w - self._pw) / 2.0
        n = abs(h - self._ph) / 2.0
        self._offset = QPointF(max(-m, min(m, self._offset.x())),
                               max(-n, min(n, self._offset.y())))

    # تحويل يرسم الصورة بدورانها حول مركزها في إحداثيات الصفحة.
    def _frame_transform(self):
        return (QTransform()
                .translate(self._pw / 2 + self._offset.x(),
                           self._ph / 2 + self._offset.y())
                .rotate(self._rotation))

    # حدود الصورة المدوّرة: 8 نقاط (زوايا ومنتصفات الأضلاع) بترتيب مقابض التكبير.
    def _content_outline(self):
        w, h = self._logical_dims()
        pts = [QPointF(-w / 2, -h / 2), QPointF(0, -h / 2), QPointF(w / 2, -h / 2),
               QPointF(w / 2, 0), QPointF(w / 2, h / 2), QPointF(0, h / 2),
               QPointF(-w / 2, h / 2), QPointF(-w / 2, 0)]
        t = self._frame_transform()
        return [t.map(p) for p in pts]

    # مسار إطار التحديد المدوّر مع الصورة، منطبق عليها تماماً في أي زاوية.
    def _rotated_frame_path(self):
        w, h = self._logical_dims()
        r = min(3.0, max(1.0, min(w, h) / 8))
        path = QPainterPath()
        path.addRoundedRect(QRectF(-w / 2, -h / 2, w, h), r, r)
        return self._frame_transform().map(path)

    def contextMenuEvent(self, event):
        self.setSelected(True)
        menu = QMenu()
        act_copy = menu.addAction("نسخ")
        act_copy.setShortcut(QKeySequence("Ctrl+C"))
        act_paste = menu.addAction("لصق")
        act_paste.setShortcut(QKeySequence("Ctrl+V"))
        act_delete = menu.addAction("مسح")
        act_delete.setShortcut(QKeySequence("Delete"))
        action = menu.exec(event.globalPos())
        editor = self.scene().parent() if self.scene() else None
        if action is None or editor is None:
            event.accept()
            return
        if action == act_copy:
            editor._copy_selected()
        elif action == act_paste:
            editor._paste()
        elif action == act_delete:
            editor._remove_document(self)
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self._rotate_btn_rect().contains(event.pos()):
                self.set_item_rotation(self._rotation + 90)
                self.setSelected(True)
                # بعد أول نقرة على زر التقليب يتحرّك الزر لموقع آخر؛ تمنع هذه
                # النافذة الزمنية القصيرة نافذةَ القصّ من الظهور عند النقرتين
                # المتتاليتين السريعتين على الزر نفسه.
                self._suppress_dblclick_until = time.monotonic() + 0.8
                event.accept()
                return
            hit = self._handle_at(event.pos())
            if hit:
                self._stretch_active = hit
                self._stretch_last = event.pos()
                self._drag_panning = False
                self.setSelected(True)
                event.accept()
                self._clamp_image()
                return
            self._press_scene_pos = self.pos()
        elif event.button() == Qt.MiddleButton:
            self._drag_panning = True
            self._drag_last = event.scenePos()
            self._press_scene_pos = self.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._stretch_active and (event.buttons() & Qt.LeftButton):
            delta = event.pos() - self._stretch_last
            self._stretch_last = event.pos()
            lw, lh = self._logical_dims()
            # تحويل إزاحة الفأرة إلى محاور الصورة الذاتية (بعكس دورانها) حتى
            # يبقى السحب طبيعياً في كل زاوية ويتبع الإطار المدوّر على الشاشة.
            dx, dy = delta.x(), delta.y()
            r = self._rotation % 360
            if r == 90:
                dx, dy = dy, -dx
            elif r == 180:
                dx, dy = -dx, -dy
            elif r == 270:
                dx, dy = -dy, dx
            if "W" in self._stretch_active and lw:
                self._sx += -dx / lw
            elif "E" in self._stretch_active and lw:
                self._sx += dx / lw
            if "N" in self._stretch_active and lh:
                self._sy += -dy / lh
            elif "S" in self._stretch_active and lh:
                self._sy += dy / lh
            self._sx = max(0.05, min(24.0, self._sx))
            self._sy = max(0.05, min(24.0, self._sy))
            self._clamp_image()
            self.update()
            event.accept()
            return
        if self._drag_panning and self._drag_last is not None:
            delta = event.scenePos() - self._drag_last
            self._drag_last = event.scenePos()
            self._offset = QPointF(self._offset.x() + delta.x(),
                                   self._offset.y() + delta.y())
            self._clamp_image()
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_panning = False
        moved = (self._stretch_active is None
                 and self._press_scene_pos is not None
                 and self.pos() != self._press_scene_pos)
        self._stretch_active = None
        self._stretch_last = None
        self._press_scene_pos = None
        super().mouseReleaseEvent(event)
        if moved and self.on_moved is not None:
            self.on_moved(self)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self.on_double_clicked is not None:
            if self._rotate_btn_rect().contains(event.pos()) or \
               self._handle_at(event.pos()) is not None or \
               time.monotonic() < self._suppress_dblclick_until:
                super().mouseDoubleClickEvent(event)
                return
            self.on_double_clicked(self)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class DocumentsGraphicsView(QGraphicsView):
    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setAcceptDrops(True)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not (event.modifiers() & Qt.ControlModifier):
            item = self.itemAt(event.pos())
            if not isinstance(item, DocumentItem):
                self.scene().clearSelection()
        super().mousePressEvent(event)
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
            self.parent().add_documents(paths)
        event.acceptProposedAction()

    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos())
        if item is not None and isinstance(item, DocumentItem):
            item.contextMenuEvent(event)
            return
        super().contextMenuEvent(event)


class DocumentsProcessingThread(QThread):
    doc_ready = Signal(bytes, int)
    finished_all = Signal()

    _SENTINEL = object()

    def __init__(self, parent=None):
        super().__init__(parent)
        import queue
        self._q = queue.Queue()
        self._stop = False

    MAX_PROCESS_PX = 2000

    def enqueue(self, paths_or_bytes, global_start, no_crop):
        self._q.put((global_start, no_crop, list(paths_or_bytes)))

    def stop(self):
        self._stop = True
        self._q.put((0, False, [self._SENTINEL]))

    def run(self):
        from core.doc_cleaner import extract_document_to_a4
        while True:
            try:
                start_idx, no_crop, items = self._q.get(timeout=1.0)
            except Exception:
                if self._stop:
                    break
                continue
            if items == [self._SENTINEL]:
                break
            for i, pb in enumerate(items):
                try:
                    if isinstance(pb, str):
                        raw = Image.open(pb)
                    else:
                        raw = Image.open(io.BytesIO(pb))
                    if raw.mode != "RGB":
                        raw = raw.convert("RGB")
                    if no_crop:
                        # بدون قصّ: تُضاف الوثيقة كما هي دون معالجة
                        pil = raw
                    else:
                        # إزالة الخلفية بالكامل ثم فرد الوثيقة النظيفة إلى مقاس
                        # A4 الكامل (dst = حدود الصفحة 2480×3508) بلا فراغات.
                        pil = extract_document_to_a4(raw)
                        # ناتج A4 عرضي → اقلبه عمودياً ليملأ صفحة A4 كاملة بلا فراغات
                        # (إعادة التدوير يدوية ممكنة من الواجهة بزر التدوير).
                        if pil.width > pil.height:
                            _a4_land = A4_H / A4_W
                            if abs(pil.width / pil.height - _a4_land) < 0.05:
                                pil = pil.transpose(Image.Transpose.ROTATE_270)
                    if max(pil.size) > self.MAX_PROCESS_PX:
                        pil.thumbnail((self.MAX_PROCESS_PX, self.MAX_PROCESS_PX), Image.LANCZOS)
                    buf = io.BytesIO()
                    pil.save(buf, format="PNG")
                    self.doc_ready.emit(buf.getvalue(), start_idx + i)
                except Exception as e:
                    logger.error("فشل معالجة الوثيقة %d: %s", start_idx + i, e)
                    try:
                        if isinstance(pb, str):
                            pil = Image.open(pb)
                        else:
                            pil = Image.open(io.BytesIO(pb))
                        pil = pil.convert("RGB")
                        if max(pil.size) > self.MAX_PROCESS_PX:
                            pil.thumbnail((self.MAX_PROCESS_PX, self.MAX_PROCESS_PX), Image.LANCZOS)
                        buf = io.BytesIO()
                        pil.save(buf, format="PNG")
                        self.doc_ready.emit(buf.getvalue(), start_idx + i)
                    except Exception as e2:
                        logger.error("فشل عرض الوثيقة %d: %s", start_idx + i, e2)
            self.finished_all.emit()
        self.finished_all.emit()


class DocumentsEditor(QWidget):
    go_back = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayoutDirection(Qt.RightToLeft)
        self.documents = []
        self._num_pages = 1
        self._current_page = 1
        self._progress_total = 0
        self._progress_count = 0
        self._landscape = False
        self._clipboard = []
        self._build_ui()
        self._add_shortcuts()

    def closeEvent(self, event):
        thr = getattr(self, '_thread', None)
        if thr is not None:
            thr.stop()
            thr.wait(3000)
            thr.doc_ready.disconnect()
            thr.finished_all.disconnect()
        super().closeEvent(event)

    def _slot_w(self):
        return self._page_w()

    def _slot_h(self):
        return self._page_h()

    def _page_w(self):
        return A4_H if self._landscape else A4_W

    def _page_h(self):
        return A4_W if self._landscape else A4_H

    # المسافة بين بداية صفحة وبداية التالية (ارتفاع الصفحة + الفراغ البسيط).
    def _page_stride(self):
        return self._page_h() + PAGE_GAP

    def _build_ui(self):
        layout = QVBoxLayout(self)
        btn_bar = QHBoxLayout()
        btn_back = QPushButton("↩ رجوع")
        btn_back.clicked.connect(self.go_back.emit)
        btn_add = QPushButton("+ إضافة وثيقة")
        btn_add.clicked.connect(self._add_document_dialog)
        btn_print = QPushButton("طباعة")
        btn_print.clicked.connect(self._print_page)
        btn_save_pdf = QPushButton("حفظ PDF")
        btn_save_pdf.clicked.connect(self._save_pdf)
        btn_clear = QPushButton("تفريغ الكل")
        btn_clear.clicked.connect(self._clear_all)
        btn_flip = QPushButton("🔄 قلب الصفحة")
        btn_flip.setToolTip("بدّل اتجاه صفحة A4 بين العمودي والعرضي")
        btn_flip.clicked.connect(self._toggle_page_orientation)
        btn_bar.addWidget(btn_back)
        btn_bar.addWidget(btn_add)
        btn_bar.addWidget(btn_print)
        btn_bar.addWidget(btn_save_pdf)
        btn_bar.addWidget(btn_clear)
        btn_bar.addWidget(btn_flip)
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
        btn_zoom_in.clicked.connect(lambda: self.view.scale(1.2, 1.2))
        btn_zoom_out = QPushButton("🔍-")
        btn_zoom_out.setToolTip("تصغير")
        btn_zoom_out.clicked.connect(lambda: self.view.scale(1 / 1.2, 1 / 1.2))
        btn_zoom_fit = QPushButton("⌖")
        btn_zoom_fit.setToolTip("ملاءمة الشاشة")
        btn_zoom_fit.clicked.connect(self._zoom_fit)
        btn_bar.addWidget(btn_zoom_fit)
        btn_bar.addWidget(btn_zoom_out)
        btn_bar.addWidget(btn_zoom_in)
        layout.addLayout(btn_bar)

        flip_bar = QHBoxLayout()
        self._page_lbl = QLabel("صفحة 1 من 1")
        self._page_lbl.setStyleSheet("font-size:13px;font-weight:bold;color:#1a73e8;")
        self._page_lbl.setAlignment(Qt.AlignCenter)
        btn_prev = QPushButton("◀ الصفحة السابقة")
        btn_prev.clicked.connect(lambda: self._flip_page(-1))
        btn_next = QPushButton("الصفحة التالية ▶")
        btn_next.clicked.connect(lambda: self._flip_page(1))
        flip_bar.addStretch()
        flip_bar.addWidget(btn_prev)
        flip_bar.addWidget(self._page_lbl)
        flip_bar.addWidget(btn_next)
        flip_bar.addStretch()
        layout.addLayout(flip_bar)

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
        self.view = DocumentsGraphicsView(self.scene, self)
        layout.addWidget(self.view)

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, 'view'):
            QTimer.singleShot(0, self._fit_current_page)

    def _fit_current_page(self):
        y0 = (self._current_page - 1) * self._page_stride()
        self.view.fitInView(QRectF(0, y0, self._page_w(), self._page_h()), Qt.KeepAspectRatio)

    def _zoom_fit(self):
        self._fit_current_page()

    def _draw_page_area(self, page_index):
        y0 = page_index * self._page_stride()
        page_rect = self.scene.addRect(0, y0, self._page_w(), self._page_h(),
                                       QPen(Qt.NoPen), QBrush(Qt.white))
        page_rect.setZValue(-1)
        page_rect.setData(NO_PRINT_KEY, True)

    def _ensure_page(self, page_index):
        while page_index >= self._num_pages:
            self._draw_page_area(self._num_pages)
            self._num_pages += 1
            self.scene.setSceneRect(0, 0, self._page_w(),
                                    self._num_pages * self._page_stride())
        self._update_page_lbl()

    def _update_page_lbl(self):
        self._page_lbl.setText(f"صفحة {min(self._current_page, self._num_pages)} من {self._num_pages}")

    def _place_document(self, pixmap, source_bytes=None, page_index=None,
                        dpi=None):
        idx = len(self.documents)
        pw = self._slot_w()
        ph = self._slot_h()
        item = DocumentItem(pixmap, pw, ph, index=idx)
        item._source_bytes = source_bytes
        item.on_double_clicked = self._show_manual_crop
        item.on_moved = self._on_item_moved
        # كل صورة تُضاف ملائمةً ورقة A4 بأكملها مهما كان حجمها (صغيرة أو كبيرة):
        # تُكبَّر لتعبي أكبر قدر ممكن داخل الصفحة دون قصّ أو تشويه — وإن طابق
        # إطارها نسبة A4 ملأتها بالكامل. والتحكم اليدوي (تكبير/قص) متاح بعدها.
        item._nat_w_mm = None
        item._nat_h_mm = None
        if page_index is None:
            page, offset = self._pack_slot(item)
        else:
            page = page_index
            offset = QPointF(0.0, 0.0)
        item._offset = offset
        item.setPos(DOC_MARGIN, page * self._page_stride() + DOC_MARGIN)
        self._ensure_page(page)
        self.scene.addItem(item)
        self.documents.append(item)
        self._current_page = page + 1
        self._update_page_lbl()
        logger.info("تمت إضافة الوثيقة %d (صفحة %d)", len(self.documents), page + 1)
        return item

    # يختار خانة/صفحة لأوثيقة جديدة: صورتان صغيرتان في نفس الصفحة
    # (عمود 0 ثم عمود 1)، والوثيقة الكبيرة في صفحة خاصة بها.
    def _pack_slot(self, item):
        pw, ph = self._slot_w(), self._slot_h()
        w, h = item._content_dims()
        half = w <= (pw / 2) and h <= ph
        if half:
            for page in range(0, self._num_pages):
                occupied = set()
                for d in self.documents:
                    p = int((d.pos().y() - DOC_MARGIN) // self._page_stride())
                    if p != page:
                        continue
                    dw = d._content_dims()[0]
                    if dw <= pw / 2:
                        col = 0 if d._offset.x() < 0 else 1
                        occupied.add(col)
                    else:
                        occupied.add(0)
                        occupied.add(1)
                for col in (0, 1):
                    if col not in occupied:
                        offset = QPointF((2 * col - 1) * (pw / 4.0), 0.0)
                        return page, offset
            page = self._num_pages
            return page, QPointF(-(pw / 4.0), 0.0)
        # الوثيقة كاملة الصفحة: توضع في أول صفحة فارغة (لا وثائق عليها) لئلا
        # تبدأ من صفحة ثانية والصفحة الأولى تبقى بيضاء؛ وإن امتلأت جميع الصفحات
        # الموجودة تُنشأ صفحة جديدة.
        for p in range(0, self._num_pages):
            if not any(int((d.pos().y() - DOC_MARGIN) // self._page_stride()) == p
                       for d in self.documents):
                return p, QPointF(0.0, 0.0)
        return self._num_pages, QPointF(0.0, 0.0)

    def _show_manual_crop(self, doc_item):
        original_pix = doc_item._original_pixmap
        if original_pix is None or original_pix.isNull():
            original_pix = doc_item.pixmap()
        if original_pix is None or original_pix.isNull():
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("قص يدوي")
        dlg.setMinimumSize(800, 600)
        dlg.setLayoutDirection(Qt.RightToLeft)
        dlg.setStyleSheet("QDialog{background:#2b2b2b;}")
        v_layout = QVBoxLayout(dlg)
        v_layout.setContentsMargins(10, 10, 10, 10)

        overlay = _CropOverlay(original_pix, dlg)
        overlay._max_points = 4
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
        brightness_slider.setValue(getattr(doc_item, "_last_brightness", 0))
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
                    QMessageBox.warning(dlg, "خطأ", "حدد 4 نقاط أولاً")
                    return
                cropped = _perspective_crop_document(original_pix, pts)
                if cropped is None or cropped.isNull():
                    QMessageBox.warning(dlg, "خطأ", "فشل في القص")
                    return
                _show_preview(cropped)
            except Exception as e:
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
                current = doc_item.pixmap()
                if current is None or current.isNull():
                    current = original_pix
                final = _adjust_brightness_pixmap(current, brightness[0])
            # تُختم التعديلات الحالية في أصل جديد (bake): يظهر التغيير السابق
            # عند العودة، ويبدأ الشريط من صفر لمواصلة التعديل دون تراكم مزدوج.
            doc_item._last_brightness = 0
            doc_item.setPixmap(final)
            doc_item._original_pixmap = final
            doc_item._rotation = 0
            doc_item._zoom = 1.0
            doc_item._sx = 1.0
            doc_item._sy = 1.0
            # بعد القص اليدوي حدود الوثيقة أصبحت معروفة، فتمتلئ الصفحة
            # بالكامل بدل عرضها بدقتها الأصلية.
            doc_item._nat_w_mm = None
            doc_item._nat_h_mm = None
            doc_item._offset = QPointF(0.0, 0.0)
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
        doc_item._last_brightness = 0

    def _add_document_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "اختر وثائق A4", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.tiff)")
        if not paths:
            return
        self.add_documents(paths)

    def add_documents(self, paths_or_bytes_list):
        if not paths_or_bytes_list:
            return
        if getattr(self, '_thread', None) is None:
            self._thread = DocumentsProcessingThread(self)
            self._thread.doc_ready.connect(self._on_doc_ready)
            self._thread.finished_all.connect(self._on_processing_done)
            self._thread.start()
        self._progress_total += len(paths_or_bytes_list)
        self._progress_count = self._progress_count
        self._progress_bar.setRange(0, self._progress_total)
        self._progress_bar.setValue(self._progress_count)
        self._progress_bar.setFormat(f"{self._progress_count}/{self._progress_total}")
        self._progress_bar.show()
        old_len = len(self.documents)
        for pb in paths_or_bytes_list:
            try:
                if isinstance(pb, str):
                    orig = Image.open(pb)
                else:
                    orig = Image.open(io.BytesIO(pb))
                dpi = _pil_dpi(orig)
                if orig.mode != "RGB":
                    orig = orig.convert("RGB")
                buf = io.BytesIO()
                orig.save(buf, format="PNG")
                src_bytes = buf.getvalue()
                qpix = QPixmap()
                qpix.loadFromData(src_bytes)
                if not qpix.isNull():
                    self._place_document(qpix, source_bytes=src_bytes, dpi=dpi)
                    self.documents[-1].set_processing(True)
            except Exception as e:
                logger.error("فشل عرض الوثيقة الأصلية: %s", e)
        pending_paths = []
        for pb in paths_or_bytes_list:
            if isinstance(pb, str):
                pending_paths.append(pb)
            else:
                pending_paths.append(io.BytesIO(pb).getvalue())
        # القصّ أصبح يدوياً فقط (نقرة مزدوجة): الإضافة بدون معالجة تلقائية.
        self._thread.enqueue(pending_paths, old_len, True)

    def _on_doc_ready(self, data, index):
        qpix = QPixmap()
        qpix.loadFromData(data)
        if not qpix.isNull() and index < len(self.documents):
            item = self.documents[index]
            item.setPixmap(qpix)
            item._original_pixmap = qpix
            item.set_processing(False)
        self._progress_count += 1
        self._progress_bar.setValue(self._progress_count)
        self._progress_bar.setFormat(f"{self._progress_count}/{self._progress_total}")

    def _on_processing_done(self):
        if self._progress_count >= self._progress_total:
            self._progress_bar.setRange(0, 0)
            self._progress_bar.setFormat("")
            self._progress_bar.hide()

    def _flip_page(self, delta):
        target = self._current_page + delta
        target = max(1, min(target, self._num_pages))
        self._current_page = target
        self._update_page_lbl()
        self._center_current_page()

    def _center_current_page(self):
        self._fit_current_page()

    def _add_blank_page(self):
        self._ensure_page(self._num_pages)
        logger.info("تمت إضافة صفحة فارغة %d", self._num_pages)

    def _delete_last_page(self):
        if self._num_pages <= 1:
            QMessageBox.information(self, "تنبيه", "لا يمكن حذف الصفحة الوحيدة.")
            return
        last_page = self._num_pages - 1
        for i, d in enumerate(self.documents):
            page = int((d.pos().y() - DOC_MARGIN) / self._page_stride())
            if page == last_page:
                QMessageBox.warning(self, "تنبيه",
                    "لا يمكن حذف الصفحة الأخيرة لأنها تحتوي على وثائق.")
                return
        saved = [(d.pixmap(), d._source_bytes, d.pos(), d.item_rotation(),
                  d._zoom, d._sx, d._sy, d._offset, getattr(d, '_crop_rect', None),
                  d._adjust_settings, (d._nat_w_mm, d._nat_h_mm)) for d in self.documents]
        self.scene.clear()
        self.documents.clear()
        self._num_pages -= 1
        self.scene.setSceneRect(0, 0, self._page_w(), self._num_pages * self._page_stride())
        for p in range(self._num_pages):
            self._draw_page_area(p)
        for pixmap, src, pos, rotation, zoom, sx, sy, offset, crop, adj, nat in saved:
            item = self._restore_document(QPixmap(pixmap), src, pos)
            if rotation:
                item.set_item_rotation(rotation)
            item._zoom = zoom
            item._sx = sx
            item._sy = sy
            if offset:
                item._offset = QPointF(offset.x(), offset.y())
            item._crop_rect = crop
            item._adjust_settings = adj
            item._nat_w_mm, item._nat_h_mm = nat
        self._current_page = min(self._current_page, self._num_pages)
        self._update_page_lbl()
        logger.info("تم حذف الصفحة %d", last_page + 1)

    def _restore_document(self, pixmap, source_bytes, pos):
        """يعيد إنشاء وثيقة في نفس موضعها (اسـتّعادة حذف الصفحة)."""
        item = DocumentItem(pixmap, self._slot_w(), self._slot_h(),
                            index=len(self.documents))
        item._source_bytes = source_bytes
        item.on_double_clicked = self._show_manual_crop
        item.on_moved = self._on_item_moved
        item.setPos(pos)
        self.scene.addItem(item)
        self.documents.append(item)
        return item

    def _on_item_moved(self, item):
        """يعيد العنصر لمحاذاة صفحته ويحوّل إزاحة السحب إلى تحريك للمحتوى
        داخل الورقة، فيبقى مكان الصورة الجديد ثابتاً بعد الإفلات. سحبها إلى
        أسفل/أعلى يضعها في الصفحة المقابلة."""
        px = item.pos().x()
        py = item.pos().y()
        stride = self._page_stride()
        page = max(0, int(round(py / stride)))
        item._offset = QPointF(item._offset.x() + px,
                               item._offset.y() + (py - page * stride))
        item.setPos(0.0, page * stride)
        if page >= self._num_pages:
            self._ensure_page(page)
        item._clamp_image()
        item.update()
        self._current_page = max(1, min(self._current_page, self._num_pages))
        self._update_page_lbl()
        self._prune_empty_trailing_pages()

    # حين تُنقل الوثيقة من آخر صفحة تبقى الصفحات الفارغة في النهاية معلّقة؛
    # تُحذف هنا تلقائياً ليبقى عدد الصفحات مطابقاً لما تحتويه فعلاً — وبإمكان
    # المستخدم إضافة صفحة فارغة جديدة متى شاء.
    def _prune_empty_trailing_pages(self):
        stride = self._page_stride()
        removed = False
        while self._num_pages > 1:
            last = self._num_pages - 1
            if any(int((d.pos().y() - DOC_MARGIN) // stride) == last
                   for d in self.documents):
                break
            y0 = last * stride
            for it in list(self.scene.items()):
                if (isinstance(it, QGraphicsRectItem) and it.data(NO_PRINT_KEY)
                        and abs(it.rect().y() - y0) < 0.5):
                    self.scene.removeItem(it)
            self._num_pages -= 1
            removed = True
        if removed:
            self.scene.setSceneRect(0, 0, self._page_w(),
                                    self._num_pages * self._page_stride())
            self._current_page = max(1, min(self._current_page, self._num_pages))
            self._update_page_lbl()

    def _clear_all(self):
        if not self.documents:
            return
        thr = getattr(self, '_thread', None)
        if thr is not None:
            try:
                thr.doc_ready.disconnect()
                thr.finished_all.disconnect()
            except Exception:
                pass
        self.scene.clear()
        self.documents.clear()
        self._num_pages = 1
        self._current_page = 1
        self.scene.setSceneRect(0, 0, self._page_w(), self._page_stride())
        self._draw_page_area(0)
        self._update_page_lbl()
        logger.info("تم تفريغ جميع الوثائق")

    def _toggle_page_orientation(self):
        """يقلب جميع صفحات A4 عمودي/عرضي مع تدوير محتواها مع الورقة،
        دون تغيير تقريب الشاشة. الضغط مرتين يعيد الحالة تماماً."""
        was_landscape = self._landscape
        old_stride = self._page_stride()
        self._landscape = not self._landscape
        nw, nh = self._page_w(), self._page_h()
        for it in list(self.scene.items()):
            if not isinstance(it, DocumentItem):
                self.scene.removeItem(it)
        for d in self.documents:
            page = int((d.pos().y() - DOC_MARGIN) / old_stride) if old_stride else 0
            ox = d._offset.x()
            oy = d._offset.y()
            if not was_landscape:
                dx, dy = -oy, ox
            else:
                dx, dy = oy, -ox
            d._offset = QPointF(dx, dy)
            d._pw = nw
            d._ph = nh
            d.set_item_rotation(d.item_rotation() + (90 if not was_landscape else -90))
            d.setPos(DOC_MARGIN, page * self._page_stride() + DOC_MARGIN)
            d._clamp_image()
            d.update()
        for p in range(self._num_pages):
            self._draw_page_area(p)
        self.scene.setSceneRect(0, 0, nw, self._num_pages * self._page_stride())
        cy = min((self._current_page - 1) * self._page_stride() + nh / 2.0,
                 self._num_pages * self._page_stride())
        self.view.centerOn(0, cy)
        self._update_page_lbl()
        logger.info("تم قلب الصفحات إلى الاتجاه %s",
                    "العرضي" if self._landscape else "العمودي")

    def _copy_selected(self):
        docs = [d for d in self._selected_documents()]
        if not docs:
            return
        self._clipboard = [{
            'pix': QPixmap(d.pixmap()),
            'src': d._source_bytes,
            'pos': d.pos(),
            'rot': d.item_rotation(),
            'zoom': d._zoom,
            'sx': d._sx,
            'sy': d._sy,
            'offset': QPointF(d._offset),
            'crop': getattr(d, '_crop_rect', None),
            'adj': dict(d._adjust_settings) if d._adjust_settings else {},
            'bright': getattr(d, '_last_brightness', 0),
            'nat': (d._nat_w_mm, d._nat_h_mm),
        } for d in docs]
        logger.info("تم نسخ %d وثيقة", len(self._clipboard))

    def _paste(self):
        if not self._clipboard:
            return
        added = []
        for snap in self._clipboard:
            item = DocumentItem(QPixmap(snap['pix']), self._slot_w(),
                                self._slot_h(), index=len(self.documents))
            item._source_bytes = snap['src']
            item._rotation = snap['rot'] % 360
            item._zoom = snap['zoom']
            item._sx = snap['sx']
            item._sy = snap['sy']
            item._offset = QPointF(snap['offset'])
            item._crop_rect = snap['crop']
            item._adjust_settings = dict(snap['adj']) if snap['adj'] else {}
            item._last_brightness = snap['bright']
            item._nat_w_mm, item._nat_h_mm = snap.get('nat') or (None, None)
            item.on_double_clicked = self._show_manual_crop
            item.on_moved = self._on_item_moved
            # تُختار خانة/صفحة جديدة فلا تُلصق النسخة فوق الأصل: صورة كبيرة بقدر
            # صفحة A4 تنتقل لصفحة جديدة، والصورة الصغيرة تأخذ الخانة المجاورة.
            page, offset = self._pack_slot(item)
            item._offset = offset
            if page >= self._num_pages:
                self._ensure_page(page)
            item.setPos(DOC_MARGIN, page * self._page_stride() + DOC_MARGIN)
            item.setSelected(True)
            self.scene.addItem(item)
            self.documents.append(item)
            item._clamp_image()
            item.update()
            added.append(item)
            logger.info("تم لصق الوثيقة في صفحة %d", page + 1)
        if added:
            # تُحدَّد النسخة المُلصقة وحدها؛ الأصل (وكل المحدَّد قبلاً) يُرفع عنه التحديد.
            for it in self.scene.selectedItems():
                if it not in added:
                    it.setSelected(False)
            self._current_page = int((added[-1].pos().y() - DOC_MARGIN) / self._page_stride()) + 1
            self._update_page_lbl()

    def _duplicate_selected(self):
        self._copy_selected()
        self._paste()

    def _remove_document(self, item):
        self.scene.removeItem(item)
        if item in self.documents:
            self.documents.remove(item)
        self._update_page_lbl()
        logger.info("تم حذف الوثيقة")

    def _delete_selected(self):
        to_remove = [item for item in self.documents if item.isSelected()]
        if not to_remove:
            return
        for item in to_remove:
            self._remove_document(item)

    def _add_shortcuts(self):
        QShortcut(QKeySequence("Delete"), self, self._delete_selected)
        QShortcut(QKeySequence("Backspace"), self, self._delete_selected)
        QShortcut(QKeySequence("Ctrl+P"), self, self._print_page)
        QShortcut(QKeySequence("R"), self, self._rotate_selected_doc)
        QShortcut(QKeySequence("Ctrl++"), self, lambda: self._zoom_selected(1.25))
        QShortcut(QKeySequence("Ctrl+-"), self, lambda: self._zoom_selected(1 / 1.25))
        QShortcut(QKeySequence("Ctrl+C"), self, self._copy_selected)
        QShortcut(QKeySequence("Ctrl+V"), self, self._paste)
        QShortcut(QKeySequence("Ctrl+D"), self, self._duplicate_selected)

    def _selected_documents(self):
        return [d for d in self.documents if d.isSelected()]

    def _rotate_selected_doc(self):
        for d in self._selected_documents():
            d.set_item_rotation(d.item_rotation() + 90)

    def _zoom_selected(self, factor):
        for d in self._selected_documents():
            d._zoom = max(0.3, min(8.0, d._zoom * factor))
            d._clamp_image()
            d.update()

    def _save_pdf(self):
        if not self.documents:
            QMessageBox.information(self, "تنبيه", "لا توجد وثائق للحفظ.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "حفظ PDF باسم", "", "ملفات PDF (*.pdf)")
        if not path:
            return
        if getattr(self, 'subscription_check', lambda: True)():
            selected = self.scene.selectedItems()
            for item in selected:
                item.setSelected(False)
            bg_items = [it for it in self.scene.items()
                        if not isinstance(it, DocumentItem) and it.isVisible()]
            for it in bg_items:
                it.setVisible(False)
            try:
                self.scene._printing = True
            except Exception:
                pass
            printer = QPrinter()
            printer.setOutputFormat(QPrinter.PdfFormat)
            printer.setOutputFileName(path)
            printer.setPageSize(QPageSize(QSizeF(self._page_w(), self._page_h()),
                                          QPageSize.Millimeter))
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
                scale_x = page_rect.width() / self._page_w()
                scale_y = page_rect.height() / self._page_h()
                scale = min(scale_x, scale_y)
                for page_idx in range(1, self._num_pages + 1):
                    if page_idx > 1:
                        printer.newPage()
                    painter.save()
                    painter.scale(scale, scale)
                    source = QRectF(0, (page_idx - 1) * self._page_stride(),
                                    self._page_w(), self._page_h())
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

    def _print_page(self):
        if not self.documents:
            QMessageBox.information(self, "تنبيه", "لا توجد وثائق للطباعة.")
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
            pt = dialog.paper_type()
            if pt:
                set_last_paper_type(pt)
            logger.info("طلب طباعة %d نسخ من %d صفحة%s على: %s",
                        copies, self._num_pages, " (وجهين)" if duplex else "", printer_name)
            selected = self.scene.selectedItems()
            for item in selected:
                item.setSelected(False)
            bg_items = [it for it in self.scene.items()
                        if not isinstance(it, DocumentItem) and it.isVisible()]
            for it in bg_items:
                it.setVisible(False)
            print_scene(self, self.scene, copies=copies, page_count=self._num_pages, duplex=duplex,
                        page_range=dialog.page_range(), paper_type=pt,
                        paper_width_mm=self._page_w(), paper_height_mm=self._page_h(),
                        page_stride_mm=self._page_stride())
            for it in bg_items:
                it.setVisible(True)
            for item in selected:
                item.setSelected(True)