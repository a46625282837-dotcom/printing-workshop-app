from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsItem, QStyle, QGraphicsRectItem
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPixmap, QPen, QPainter, QColor, QBrush, QFont, QPainterPath, QTransform
from typing import Optional

CARD_W = 90
CARD_H = 55
ROT_HANDLE_SIZE = 12
STRETCH_HANDLE_SIZE = 8


class IDCardItem(QGraphicsPixmapItem):

    def __init__(self, pixmap: QPixmap, index: int = 0, parent=None,
                 detect_corners: Optional[list] = None):
        super().__init__(pixmap, parent)
        self.index = index
        self._detect_corners = detect_corners
        self._rotation = 0
        self._scale = 1.0
        self._stretch_w = 1.0
        self._stretch_h = 1.0
        self._stretch_active = None
        self._stretch_last = None
        self._snapped = False
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._panning = False
        self._pan_start = None
        self._drag_start = None
        self._drag_origin = None
        self._move_hold = False
        self.on_dropped = None
        self.on_double_clicked = None
        self.on_sakan_toggled = None
        self._original_pixmap = None
        self._card_w = float(CARD_W)
        self._card_h = float(CARD_H)
        self._sakan_large = False
        self._sakan_orig = (float(CARD_W), float(CARD_H))
        self._sakan_state = (self._scale, self._snapped)
        self._action_button = None
        self._sakan_center_x = None
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)

    def card_size_mm(self):
        """أبعاد المستطيل الحالية (عرض، ارتفاع) بالملليمتر."""
        return self._card_w, self._card_h

    def set_size_mm(self, w, h):
        """تغيير حجم مستطيل البطاقة إلى (عرض، ارتفاع) بالملليمتر."""
        self._card_w = max(30.0, float(w))
        self._card_h = max(20.0, float(h))
        self._snapped = False
        self._clamp_pan()
        self.update()

    def toggle_sakan_size(self):
        """تبديل حجم البطاقة بين الأصلي والحجم الكبير 98×60.

        عند التكبير تحشو الصورة المستطيل بالكامل من كل الاتجاهات (cover):
        تُقصّ الأطراف الزائدة ويعود الموضع إلى الأعلى-اليسار. عند التراجع
        تُستعاد الملوءة الأصلية (contain) بالكامل.
        """
        if self._sakan_large:
            self._card_w, self._card_h = self._sakan_orig
            self._scale, self._snapped = self._sakan_state
            self._sakan_large = False
        else:
            self._sakan_orig = self.card_size_mm()
            self._sakan_state = (self._scale, self._snapped)
            self._card_w, self._card_h = 98.0, 60.0
            self._cover_fill()
            self._sakan_large = True
        self._clamp_pan()
        self._position_action_button()
        self.update()
        if self.on_sakan_toggled is not None:
            self.on_sakan_toggled()

    def _cover_fill(self):
        """تكبير الصورة لتغطية كامل المستطيل (قد تُقصّ الأطراف الزائدة)."""
        source = self.pixmap()
        if source.isNull():
            return
        w, h = source.width(), source.height()
        base = min(self._card_w / w, self._card_h / h, 1.0)
        fill = max(self._card_w / (w * base), self._card_h / (h * base))
        self._scale = max(0.3, min(3.0, fill))
        self._snapped = True
        self._clamp_pan()

    def attach_action_button(self, on_clicked):
        """زر سكن صغير بجانب البطاقة، يظهر في منتصف الفراغ بين العمودين عند الاختيار."""
        if self._action_button is not None:
            return self._action_button
        from PySide6.QtWidgets import QPushButton, QGraphicsProxyWidget
        btn = QPushButton("سكن")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip("تكبير البطاقة إلى 9.8×6سم")
        btn.setFixedSize(40, 20)
        btn.setStyleSheet(
            "QPushButton{background:#1a73e8;color:#fff;border:none;"
            "border-radius:3px;font-size:9pt;padding:0px;}"
            "QPushButton:hover{background:#1976d2;}"
        )
        btn.clicked.connect(on_clicked)
        proxy = QGraphicsProxyWidget(self)
        proxy.setWidget(btn)
        proxy.setZValue(10)
        self._action_button = proxy
        self._sakan_scale = 9.0 / float(btn.width())
        proxy.setTransform(QTransform().scale(self._sakan_scale, self._sakan_scale))
        self._position_action_button()
        proxy.setVisible(False)
        return proxy

    def _position_action_button(self):
        if self._action_button is None:
            return
        btn = self._action_button.widget()
        sw = btn.width() * self._sakan_scale
        sh = btn.height() * self._sakan_scale
        if self._sakan_center_x is not None:
            cx = self._sakan_center_x
        else:
            cx = self.pos().x() + self._card_w
        self._action_button.setPos(cx - self.pos().x() - sw / 2.0,
                                   (self._card_h - sh) / 2.0)

    def show_action_button(self, show):
        if self._action_button is not None:
            self._action_button.setVisible(show)

    def item_rotation(self):
        return self._rotation

    def set_item_rotation(self, angle):
        self._rotation = angle % 360
        self._snapped = False
        self._pan_x = 0.0
        self._pan_y = 0.0
        self.update()

    def item_scale(self):
        return self._scale

    def set_item_scale(self, factor, snap=False):
        self._scale = max(0.3, min(3.0, factor))
        if snap:
            self._stretch_w = 1.0
            self._stretch_h = 1.0
            self._snap_to_fill()
        else:
            self._snapped = False
        if abs(factor - 1.0) < 0.01:
            self._pan_x = 0.0
            self._pan_y = 0.0
        self.update()

    def stretch_factors(self):
        """عوامل التمدد الحالية (عرض، ارتفاع) — أفقياً وعمودياً بشكل مستقل."""
        return self._stretch_w, self._stretch_h

    def set_stretch_factors(self, sw, sh):
        """ضبط عوامل التمدد مع حدود آمنة؛ يُلغي أي snap حتى لا يفرض النسبة القديمة."""
        self._stretch_w = max(0.5, min(3.0, float(sw)))
        self._stretch_h = max(0.5, min(3.0, float(sh)))
        self._snapped = False
        self.update()

    def _dims(self):
        """أبعاد الصورة الحقيقية المرسومة داخل النافذة (بعد الدوران/التمدد)."""
        source = self.pixmap()
        if source.isNull():
            return 0, 0
        w, h = source.width(), source.height()
        base = min(self._card_w / w, self._card_h / h, 1.0)
        scale = base * self._scale
        if self._rotation in (90, 270):
            rbase = min(self._card_h / w, self._card_w / h, 1.0)
            scale = rbase * self._scale
        dw = max(1, int(w * scale * self._stretch_w))
        dh = max(1, int(h * scale * self._stretch_h))
        return dw, dh

    def _snap_to_fill(self):
        source = self.pixmap()
        if source.isNull():
            return
        w, h = source.width(), source.height()
        base = min(self._card_w / w, self._card_h / h, 1.0)
        cur = base * self._scale
        dw = w * cur
        dh = h * cur
        if dw >= self._card_w and dh >= self._card_h:
            self._snapped = False
            return
        fill = max(self._card_w / (w * base), self._card_h / (h * base))
        if fill > cur:
            self._scale = max(0.3, min(3.0, fill))
            self._snapped = True

    def _image_overflows(self):
        dw, dh = self._dims()
        if self._rotation in (90, 270):
            dw, dh = dh, dw
        return dw > self._card_w or dh > self._card_h

    @property
    def _rotate_rect(self):
        return QRectF(self._card_w - ROT_HANDLE_SIZE - 2, 2, ROT_HANDLE_SIZE, ROT_HANDLE_SIZE)

    @property
    def _zoom_in_rect(self):
        return QRectF(self._card_w - ROT_HANDLE_SIZE - 2, 2 + ROT_HANDLE_SIZE + 1,
                      ROT_HANDLE_SIZE, ROT_HANDLE_SIZE)

    @property
    def _zoom_out_rect(self):
        return QRectF(self._card_w - ROT_HANDLE_SIZE - 2, 2 + 2 * (ROT_HANDLE_SIZE + 1),
                      ROT_HANDLE_SIZE, ROT_HANDLE_SIZE)

    @property
    def _move_rect(self):
        return QRectF(self._card_w - ROT_HANDLE_SIZE - 2, 2 + 3 * (ROT_HANDLE_SIZE + 1),
                      ROT_HANDLE_SIZE, ROT_HANDLE_SIZE)

    @property
    def _stretch_rects(self):
        """نقاط السحب على حدود البطاقة: أعلى/أسفل/يسار فقط (حُذفت اليمنى
        لأنها تتداخل مع أسهم التكبير/التصغير في الزاوية العلوية اليمنى)."""
        h = STRETCH_HANDLE_SIZE
        cx, cy = self._card_w / 2.0, self._card_h / 2.0
        return {
            "T": QRectF(cx - h / 2.0, 0.0, h, h),
            "B": QRectF(cx - h / 2.0, self._card_h - h, h, h),
            "L": QRectF(0.0, cy - h / 2.0, h, h),
        }

    def _stretch_at(self, pos):
        for key, rect in self._stretch_rects.items():
            if rect.contains(pos):
                return key
        return None

    def boundingRect(self):
        return QRectF(0, 0, self._card_w, self._card_h)

    def shape(self):
        path = QPainterPath()
        path.addRect(QRectF(0, 0, self._card_w, self._card_h))
        return path

    def get_image_rect(self):
        """مستطيل الصورة الفعلية المرسومة (x, y, w, h) داخل إحداثيات العنصر."""
        dw, dh = self._dims()
        px, py = self._pan_x, self._pan_y
        if self._rotation == 0:
            if self._snapped:
                return (px, py, dw, dh)
            return ((self._card_w - dw) / 2 + px, (self._card_h - dh) / 2 + py, dw, dh)
        if self._rotation == 180:
            if self._snapped:
                return (px + (self._card_w - dw) / 2, py + (self._card_h - dh) / 2, dw, dh)
            return ((self._card_w - dw) / 2 + px, (self._card_h - dh) / 2 + py, dw, dh)
        return ((self._card_w - dw) / 2 + px, (self._card_h - dh) / 2 + py, dw, dh)

    def _image_corners(self):
        """زوايا الصورة المعروضة فعلاً في إحداثيات العنصر — تطابق هندسة paint تماماً."""
        from PySide6.QtGui import QTransform
        from PySide6.QtCore import QPointF
        dw, dh = self._dims()
        r = self._rotation
        px, py = self._pan_x, self._pan_y
        if r == 0:
            if self._snapped:
                dx, dy = px, py
            else:
                dx, dy = (self._card_w - dw) / 2 + px, (self._card_h - dh) / 2 + py
            return [(dx, dy), (dx + dw, dy), (dx + dw, dy + dh), (dx, dy + dh)]
        if self._snapped and r in (90, 270):
            cx, cy = dh / 2 + px, dw / 2 + py
        else:
            cx, cy = self._card_w / 2 + px, self._card_h / 2 + py
        t = QTransform().translate(cx, cy).rotate(r)
        pts = [(-dw / 2, -dh / 2), (dw / 2, -dh / 2), (dw / 2, dh / 2), (-dw / 2, dh / 2)]
        out = []
        for x, y in pts:
            mp = t.map(QPointF(x, y))
            out.append((float(mp.x()), float(mp.y())))
        return out

    def _detect_corner_points(self):
        """مواضع زوايا كشف النظام في إحداثيات العنصر (أو [] إن لم تتوفر).

        زوايا الكشف (بكسل) مخزنة بنسبة إلى الصورة المقصوصة الأصلية؛ تُسقَط
        بنفس قواعد paint تماماً (dw/dh + مركز + دوران) فتلتصق بالبطاقة مهما
        دُوّرت أو صُغّرت.
        """
        if not self._detect_corners:
            return []
        source = self.pixmap()
        if source.isNull() or source.width() < 2 or source.height() < 2:
            return []
        dw, dh = self._dims()
        r = self._rotation
        px, py = self._pan_x, self._pan_y
        if r == 0:
            if self._snapped:
                dx, dy = px, py
            else:
                dx, dy = (self._card_w - dw) / 2 + px, (self._card_h - dh) / 2 + py
            out = []
            W, H = float(source.width()), float(source.height())
            for (x, y) in self._detect_corners:
                fx, fy = (x / W), (y / H)
                out.append((dx + fx * dw, dy + fy * dh))
            return out
        if self._snapped and r in (90, 270):
            cx, cy = dh / 2 + px, dw / 2 + py
        else:
            cx, cy = self._card_w / 2 + px, self._card_h / 2 + py
        from PySide6.QtGui import QTransform
        from PySide6.QtCore import QPointF
        t = QTransform().translate(cx, cy).rotate(r)
        out = []
        W, H = float(source.width()), float(source.height())
        for (x, y) in self._detect_corners:
            fx, fy = (x / W), (y / H)
            lp = QPointF((fx - 0.5) * dw, (fy - 0.5) * dh)
            mp = t.map(lp)
            out.append((float(mp.x()), float(mp.y())))
        return out

    def paint(self, painter, option, widget):
        source = self.pixmap()
        if not source.isNull():
            dw, dh = self._dims()
            painter.save()
            painter.setClipRect(QRectF(0, 0, self._card_w, self._card_h))
            painter.fillRect(QRectF(0, 0, self._card_w, self._card_h), Qt.white)
            r = self._rotation
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            px, py = self._pan_x, self._pan_y
            if self._snapped:
                if r == 0:
                    painter.drawPixmap(QRectF(px, py, dw, dh), source, source.rect())
                elif r == 90:
                    painter.translate(dh / 2 + px, dw / 2 + py)
                    painter.rotate(90)
                    painter.drawPixmap(QRectF(-dw / 2, -dh / 2, dw, dh), source, source.rect())
                elif r == 180:
                    painter.translate(dw / 2 + px, dh / 2 + py)
                    painter.rotate(180)
                    painter.drawPixmap(QRectF(-dw / 2, -dh / 2, dw, dh), source, source.rect())
                elif r == 270:
                    painter.translate(dh / 2 + px, dw / 2 + py)
                    painter.rotate(270)
                    painter.drawPixmap(QRectF(-dw / 2, -dh / 2, dw, dh), source, source.rect())
            else:
                if r == 0:
                    dx = (self._card_w - dw) / 2 + px
                    dy = (self._card_h - dh) / 2 + py
                    painter.drawPixmap(QRectF(dx, dy, dw, dh), source, source.rect())
                elif r == 180:
                    painter.translate(self._card_w / 2 + px, self._card_h / 2 + py)
                    painter.rotate(180)
                    painter.drawPixmap(QRectF(-dw / 2, -dh / 2, dw, dh), source, source.rect())
                else:
                    painter.translate(self._card_w / 2 + px, self._card_h / 2 + py)
                    painter.rotate(r)
                    painter.drawPixmap(QRectF(-dw / 2, -dh / 2, dw, dh), source, source.rect())
            painter.restore()
        scene = self.scene()
        printing = bool(getattr(scene, "_printing", False)) if scene is not None else False
        selected = bool(option.state & QStyle.State_Selected)
        if not printing:
            pass
        if selected and not printing:
            painter.setPen(QPen(QColor("#1a73e8"), 2.5))
            painter.setBrush(QBrush())
            painter.drawRoundedRect(QRectF(1.5, 1.5, self._card_w - 3, self._card_h - 3), 3, 3)
            painter.setBrush(QBrush(QColor("#1a73e8")))
            painter.setPen(QPen(Qt.white, 1.2))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawEllipse(self._rotate_rect)
            painter.drawText(self._rotate_rect, Qt.AlignCenter, "↻")
            painter.drawEllipse(self._zoom_in_rect)
            painter.drawText(self._zoom_in_rect, Qt.AlignCenter, "↑")
            painter.drawEllipse(self._zoom_out_rect)
            painter.drawText(self._zoom_out_rect, Qt.AlignCenter, "↓")
            painter.setBrush(QBrush(QColor("#1a73e8")))
            painter.setPen(QPen(Qt.white, 1.2))
            painter.drawEllipse(self._move_rect)
            painter.drawText(self._move_rect, Qt.AlignCenter, "⤡")
            for _, rect in self._stretch_rects.items():
                painter.drawEllipse(rect)
        elif not printing:
            painter.setPen(QPen(Qt.gray, 0.5))
            painter.drawRect(QRectF(0, 0, self._card_w, self._card_h))

    def _clamp_pan(self):
        source = self.pixmap()
        if source.isNull():
            self._pan_x = 0.0
            self._pan_y = 0.0
            return
        dw, dh = self._dims()
        if self._rotation in (90, 270):
            dw, dh = dh, dw
        if dw > self._card_w:
            if self._snapped:
                lo, hi = self._card_w - dw, 0.0
            else:
                half = (dw - self._card_w) / 2.0
                lo, hi = -half, half
            self._pan_x = max(lo, min(hi, self._pan_x))
        else:
            self._pan_x = 0.0
        if dh > self._card_h:
            if self._snapped:
                lo, hi = self._card_h - dh, 0.0
            else:
                half = (dh - self._card_h) / 2.0
                lo, hi = -half, half
            self._pan_y = max(lo, min(hi, self._pan_y))
        else:
            self._pan_y = 0.0

    def _on_plain_press(self, pos):
        """إرجاع الحجم الأصلي عند إعادة النقر على بطاقة مكبّرة برز سكن."""
        if self._sakan_large and self.isSelected():
            if self._rotate_rect.contains(pos) or \
               self._zoom_in_rect.contains(pos) or \
               self._zoom_out_rect.contains(pos) or \
               self._move_rect.contains(pos) or \
               self._stretch_at(pos) is not None:
                return False
            self.toggle_sakan_size()
            return True
        return False

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._on_plain_press(event.pos()):
            event.accept()
            return
        self._drag_start = self.pos()
        self._drag_origin = self.pos()
        if event.button() == Qt.LeftButton:
            if self._rotate_rect.contains(event.pos()):
                self.set_item_rotation((self._rotation + 90) % 360)
                event.accept()
                return
            if self._zoom_in_rect.contains(event.pos()):
                self.set_item_scale(self._scale * 1.05)
                event.accept()
                return
            if self._zoom_out_rect.contains(event.pos()):
                self.set_item_scale(self._scale / 1.05)
                event.accept()
                return
            hit = self._stretch_at(event.pos())
            if hit:
                self._stretch_active = hit
                self._stretch_last = event.pos()
                self._panning = False
                event.accept()
                return
            if self._image_overflows():
                if self._move_rect.contains(event.pos()):
                    self._move_hold = True
                    self.setFlag(QGraphicsItem.ItemIsMovable, True)
                else:
                    self._move_hold = False
                    self.setFlag(QGraphicsItem.ItemIsMovable, False)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._stretch_active and (event.buttons() & Qt.LeftButton):
            pos = event.pos()
            delta = pos - self._stretch_last
            self._stretch_last = pos
            sw, sh = self.stretch_factors()
            if self._stretch_active in ("L", "R"):
                dw, _ = self._dims()
                if dw and delta.x():
                    sw += delta.x() / dw if self._stretch_active == "R" else -delta.x() / dw
            elif self._stretch_active in ("T", "B"):
                _, dh = self._dims()
                if dh and delta.y():
                    sh += delta.y() / dh if self._stretch_active == "B" else -delta.y() / dh
            self.set_stretch_factors(sw, sh)
            event.accept()
            return
        if (event.buttons() & Qt.MiddleButton) and self._image_overflows():
            if not self._panning:
                self._panning = True
                self._pan_start = event.pos()
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            self._pan_x += delta.x()
            self._pan_y += delta.y()
            self._clamp_pan()
            self.update()
            event.accept()
            return
        if (event.buttons() & Qt.LeftButton) and self._image_overflows() and not self._move_hold:
            if not self._panning:
                self._panning = True
                self._pan_start = event.pos()
                self.setPos(self._drag_start)
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            self._pan_x += delta.x()
            self._pan_y += delta.y()
            self._clamp_pan()
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._stretch_active:
            self._stretch_active = None
            self._stretch_last = None
            event.accept()
            return
        if self._panning:
            self._panning = False
            self._pan_start = None
            self.setFlag(QGraphicsItem.ItemIsMovable, True)
            self._drag_start = None
            event.accept()
            return
        super().mouseReleaseEvent(event)
        if self._drag_start is not None and self.pos() != self._drag_start:
            if self.on_dropped:
                self.on_dropped(self)
        self._drag_start = None
        if self._move_hold:
            self._move_hold = False
            if self._image_overflows():
                self.setFlag(QGraphicsItem.ItemIsMovable, False)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self._original_pixmap is not None:
            if self._rotate_rect.contains(event.pos()) or \
               self._zoom_in_rect.contains(event.pos()) or \
               self._zoom_out_rect.contains(event.pos()) or \
               self._move_rect.contains(event.pos()) or \
               self._stretch_at(event.pos()) is not None:
                super().mouseDoubleClickEvent(event)
                return
            if self.on_double_clicked:
                self.on_double_clicked(self)
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)
