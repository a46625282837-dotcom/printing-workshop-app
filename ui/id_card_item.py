from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsItem, QStyle
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPixmap, QPen, QPainter, QColor, QBrush, QFont, QPainterPath

CARD_W = 95
CARD_H = 55
ROT_HANDLE_SIZE = 12


class IDCardItem(QGraphicsPixmapItem):

    def __init__(self, pixmap: QPixmap, index: int = 0, parent=None):
        super().__init__(pixmap, parent)
        self.index = index
        self._rotation = 0
        self._scale = 1.0
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
        self._original_pixmap = None
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)

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
            self._snap_to_fill()
        else:
            self._snapped = False
        if abs(factor - 1.0) < 0.01:
            self._pan_x = 0.0
            self._pan_y = 0.0
        self.update()

    def _snap_to_fill(self):
        source = self.pixmap()
        if source.isNull():
            return
        w, h = source.width(), source.height()
        base = min(CARD_W / w, CARD_H / h, 1.0)
        cur = base * self._scale
        dw = w * cur
        dh = h * cur
        if dw >= CARD_W and dh >= CARD_H:
            self._snapped = False
            return
        fill = max(CARD_W / (w * base), CARD_H / (h * base))
        if fill > cur:
            self._scale = max(0.3, min(3.0, fill))
            self._snapped = True

    def _image_overflows(self):
        source = self.pixmap()
        if source.isNull():
            return False
        w, h = source.width(), source.height()
        base = min(CARD_W / w, CARD_H / h, 1.0)
        scale = base * self._scale
        if self._rotation in (90, 270):
            rbase = min(CARD_H / w, CARD_W / h, 1.0)
            scale = rbase * self._scale
        dw = w * scale
        dh = h * scale
        return dw > CARD_W or dh > CARD_H

    @property
    def _rotate_rect(self):
        return QRectF(CARD_W - ROT_HANDLE_SIZE - 2, 2, ROT_HANDLE_SIZE, ROT_HANDLE_SIZE)

    @property
    def _zoom_in_rect(self):
        return QRectF(CARD_W - ROT_HANDLE_SIZE - 2, 2 + ROT_HANDLE_SIZE + 1,
                       ROT_HANDLE_SIZE, ROT_HANDLE_SIZE)

    @property
    def _zoom_out_rect(self):
        return QRectF(CARD_W - ROT_HANDLE_SIZE - 2, 2 + 2 * (ROT_HANDLE_SIZE + 1),
                       ROT_HANDLE_SIZE, ROT_HANDLE_SIZE)

    @property
    def _move_rect(self):
        return QRectF(CARD_W - ROT_HANDLE_SIZE - 2, 2 + 3 * (ROT_HANDLE_SIZE + 1),
                       ROT_HANDLE_SIZE, ROT_HANDLE_SIZE)

    def boundingRect(self):
        return QRectF(0, 0, CARD_W, CARD_H)

    def shape(self):
        path = QPainterPath()
        path.addRect(QRectF(0, 0, CARD_W, CARD_H))
        return path

    def paint(self, painter, option, widget):
        source = self.pixmap()
        if not source.isNull():
            painter.save()
            painter.setClipRect(QRectF(0, 0, CARD_W, CARD_H))
            painter.fillRect(QRectF(0, 0, CARD_W, CARD_H), Qt.white)
            r = self._rotation
            base = min(CARD_W / source.width(), CARD_H / source.height(), 1.0)
            scale = base * self._scale
            if r == 90 or r == 270:
                rbase = min(CARD_H / source.width(), CARD_W / source.height(), 1.0)
                scale = rbase * self._scale
            dw = max(1, int(source.width() * scale))
            dh = max(1, int(source.height() * scale))
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
                    dx = (CARD_W - dw) / 2 + px
                    dy = (CARD_H - dh) / 2 + py
                    painter.drawPixmap(QRectF(dx, dy, dw, dh), source, source.rect())
                elif r == 180:
                    painter.translate(CARD_W / 2 + px, CARD_H / 2 + py)
                    painter.rotate(180)
                    painter.drawPixmap(QRectF(-dw / 2, -dh / 2, dw, dh), source, source.rect())
                else:
                    painter.translate(CARD_W / 2 + px, CARD_H / 2 + py)
                    painter.rotate(r)
                    painter.drawPixmap(QRectF(-dw / 2, -dh / 2, dw, dh), source, source.rect())
            painter.restore()
        scene = self.scene()
        printing = bool(getattr(scene, "_printing", False)) if scene is not None else False
        selected = bool(option.state & QStyle.State_Selected)
        if selected and not printing:
            painter.setPen(QPen(QColor("#1a73e8"), 2.5))
            painter.setBrush(QBrush())
            painter.drawRoundedRect(QRectF(1.5, 1.5, CARD_W - 3, CARD_H - 3), 3, 3)
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
        elif not printing:
            painter.setPen(QPen(Qt.gray, 0.5))
            painter.drawRect(QRectF(0, 0, CARD_W, CARD_H))

    def _clamp_pan(self):
        source = self.pixmap()
        if source.isNull():
            self._pan_x = 0.0
            self._pan_y = 0.0
            return
        w, h = source.width(), source.height()
        base = min(CARD_W / w, CARD_H / h, 1.0)
        scale = base * self._scale
        if self._rotation in (90, 270):
            rbase = min(CARD_H / w, CARD_W / h, 1.0)
            scale = rbase * self._scale
        dw = w * scale
        dh = h * scale
        if self._rotation in (90, 270):
            dw, dh = dh, dw
        if dw > CARD_W:
            if self._snapped:
                lo, hi = CARD_W - dw, 0.0
            else:
                half = (dw - CARD_W) / 2.0
                lo, hi = -half, half
            self._pan_x = max(lo, min(hi, self._pan_x))
        else:
            self._pan_x = 0.0
        if dh > CARD_H:
            if self._snapped:
                lo, hi = CARD_H - dh, 0.0
            else:
                half = (dh - CARD_H) / 2.0
                lo, hi = -half, half
            self._pan_y = max(lo, min(hi, self._pan_y))
        else:
            self._pan_y = 0.0

    def mousePressEvent(self, event):
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
            if self._image_overflows():
                if self._move_rect.contains(event.pos()):
                    self._move_hold = True
                    self.setFlag(QGraphicsItem.ItemIsMovable, True)
                else:
                    self._move_hold = False
                    self.setFlag(QGraphicsItem.ItemIsMovable, False)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
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
               self._move_rect.contains(event.pos()):
                super().mouseDoubleClickEvent(event)
                return
            if self.on_double_clicked:
                self.on_double_clicked(self)
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)
