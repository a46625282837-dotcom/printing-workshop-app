import logging
import os
import json
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QFontComboBox, QToolBar, QColorDialog, QSpinBox,
    QTextEdit, QFileDialog, QMessageBox, QDialog, QLineEdit,
    QTabWidget, QScrollArea, QFrame, QSplitter, QSizePolicy,
    QDialogButtonBox, QSlider, QGroupBox, QGridLayout, QInputDialog,
    QGraphicsDropShadowEffect, QGraphicsView, QGraphicsScene,
    QGraphicsRectItem, QGraphicsProxyWidget,
)
from PySide6.QtGui import (
    QAction, QFont, QColor, QTextCharFormat, QTextBlockFormat,
    QTextCursor, QIcon, QPageSize, QImage,
    QPainter, QPixmap, QTextDocument, QShortcut, QKeySequence,
    QTextOption, QAbstractTextDocumentLayout, QPageLayout,
)
from PySide6.QtCore import Qt, Signal, QSize, QMargins, QMarginsF, QRectF, QEvent, QTimer, QSizeF, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtPrintSupport import QPrinter
from core.printer import get_selected_printer_name, set_printer_name, set_last_paper_type

logger = logging.getLogger(__name__)


class _ZoomableTextEdit(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._zoom_level = 0
        self._paper_frame = None
        self._graphics_view = None
        self._proxy = None
        self._base_w = 595
        self._base_h = 842
        self._align = Qt.AlignRight
        self._pipe = QLabel(self.viewport())
        self._pipe.setText("|")
        self._pipe.setStyleSheet("color: black; background: transparent; font-size: 14px;")
        self._pipe.setAlignment(Qt.AlignCenter)
        self._pipe.setFixedWidth(10)
        self._pipe.setLayoutDirection(Qt.LeftToRight)
        self._pipe.hide()
        self.textChanged.connect(self._update_pipe)
        self.cursorPositionChanged.connect(self._update_pipe)

    def paintEvent(self, event):
        cw = self.cursorWidth()
        self.setCursorWidth(0)
        super().paintEvent(event)
        self.setCursorWidth(cw)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.setCursorWidth(0)

    def _current_block_empty(self):
        cursor = self.textCursor()
        block_text = cursor.block().text()
        return block_text.strip() == ""

    def _update_pipe(self):
        if self._current_block_empty():
            self._show_pipe()
        else:
            self._pipe.hide()

    def _show_pipe(self):
        if not self._current_block_empty():
            self._pipe.hide()
            return
        vp = self.viewport()
        vw = vp.width()
        margin = self.document().documentMargin()
        fh = self.fontMetrics().height()
        self._pipe.setFixedHeight(fh + 4)
        cursor = self.textCursor()
        pos = cursor.positionInBlock()
        block = cursor.block()
        layout = block.layout()
        line = layout.lineForTextPosition(pos)
        if self._align == Qt.AlignRight:
            if line.isValid():
                right_edge = line.rect().right() + margin
                text_before = block.text()[:pos]
                tw = self.fontMetrics().horizontalAdvance(text_before)
                x = right_edge - tw - self._pipe.width() - 4
            else:
                x = vw - self._pipe.width() - margin - 4
        elif self._align == Qt.AlignLeft:
            if line.isValid():
                left_edge = margin
                text_before = block.text()[:pos]
                tw = self.fontMetrics().horizontalAdvance(text_before)
                x = left_edge + tw + 4
            else:
                x = margin + 4
        else:
            x = (vw - self._pipe.width()) // 2
        cursor_rect = self.cursorRect()
        y = cursor_rect.top()
        self._pipe.move(max(0, int(x)), y)
        self._pipe.show()
        self._pipe.raise_()

    def set_align(self, align):
        self._align = align
        self._update_pipe()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_pipe()

    def set_paper_frame(self, frame):
        self._paper_frame = frame
        self.setFixedSize(self._base_w, self._base_h)

    def set_graphics_view(self, view, proxy):
        self._graphics_view = view
        self._proxy = proxy

    def _apply_zoom(self):
        scale = 1.0 + self._zoom_level * 0.1
        if hasattr(self, '_graphics_view') and self._graphics_view:
            center = self._graphics_view.mapToScene(
                self._graphics_view.viewport().rect().center()
            )
            self._graphics_view.resetTransform()
            self._graphics_view.scale(scale, scale)
            self._graphics_view.centerOn(center)

    def zoom_in(self):
        self._zoom_level += 1
        self._apply_zoom()

    def zoom_out(self):
        self._zoom_level -= 1
        self._apply_zoom()

    def zoom_reset(self):
        self._zoom_level = 0
        self._apply_zoom()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            elif delta < 0:
                self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)


def _icon_emoji(emoji, size=20):
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    from PySide6.QtGui import QFont as QF
    f = QF("Segoe UI Emoji", size - 4)
    p.setFont(f)
    p.drawText(pix.rect(), Qt.AlignCenter, emoji)
    p.end()
    return QIcon(pix)


class _StyleDialog(QDialog):
    def __init__(self, parent=None, fmt=None):
        super().__init__(parent)
        self.setWindowTitle("تنسيق النص")
        self.setMinimumWidth(500)
        self._fmt = fmt or QTextCharFormat()
        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        layout.addWidget(tabs)

        char_tab = QWidget()
        cl = QVBoxLayout(char_tab)
        cl.setSpacing(8)

        f1 = QHBoxLayout()
        f1.addWidget(QLabel("نوع الخط:"))
        self._font_combo = QFontComboBox()
        self._font_combo.setCurrentFont(QFont(self._fmt.font().family()))
        f1.addWidget(self._font_combo)
        cl.addLayout(f1)

        f2 = QHBoxLayout()
        f2.addWidget(QLabel("حجم الخط:"))
        self._size_spin = QSpinBox()
        self._size_spin.setRange(6, 144)
        self._size_spin.setValue(self._fmt.font().pointSize() if self._fmt.font().pointSize() > 0 else 14)
        f2.addWidget(self._size_spin)
        cl.addLayout(f2)

        f3 = QHBoxLayout()
        f3.addWidget(QLabel("لون الخط:"))
        self._color_btn = QPushButton("  ")
        self._color_btn.setFixedSize(80, 28)
        c = self._fmt.foreground().color() if self._fmt.foreground() else QColor("#000000")
        self._color_btn.setStyleSheet(f"background: {c.name()}; border: 1px solid #999;")
        self._color_btn.clicked.connect(self._pick_color)
        f3.addWidget(self._color_btn)
        cl.addLayout(f3)

        f4 = QHBoxLayout()
        f4.addWidget(QLabel("لون التظليل:"))
        self._hl_btn = QPushButton("  ")
        self._hl_btn.setFixedSize(80, 28)
        hc = self._fmt.background().color() if self._fmt.background() else QColor(Qt.white)
        self._hl_btn.setStyleSheet(f"background: {hc.name()}; border: 1px solid #999;")
        self._hl_btn.clicked.connect(self._pick_highlight)
        f4.addWidget(self._hl_btn)
        cl.addLayout(f4)

        f5 = QHBoxLayout()
        f5.addWidget(QLabel("التأثير:"))
        self._effect_combo = QComboBox()
        self._effect_combo.addItems(["عادي", "ظل", "حافة", "انعكاس"])
        f5.addWidget(self._effect_combo)
        cl.addLayout(f5)

        layout.addWidget(QLabel("تنسيق الحرف:"))
        layout.addWidget(char_tab)
        tabs.addTab(char_tab, "الحرف")

        para_tab = QWidget()
        pl = QVBoxLayout(para_tab)
        pl.setSpacing(8)

        a1 = QHBoxLayout()
        a1.addWidget(QLabel("المحاذاة:"))
        self._align_combo = QComboBox()
        self._align_combo.addItems(["يمين", "يسار", "وسط", "-K两边"])
        pl.addLayout(a1)
        pl.addWidget(self._align_combo)

        a2 = QHBoxLayout()
        a2.addWidget(QLabel("المسافة قبل:"))
        self._space_before = QSpinBox()
        self._space_before.setRange(0, 100)
        self._space_before.setValue(0)
        self._space_before.setSuffix(" pt")
        a2.addWidget(self._space_before)
        pl.addLayout(a2)

        a3 = QHBoxLayout()
        a3.addWidget(QLabel("المسافة بعد:"))
        self._space_after = QSpinBox()
        self._space_after.setRange(0, 100)
        self._space_after.setValue(0)
        self._space_after.setSuffix(" pt")
        a3.addWidget(self._space_after)
        pl.addLayout(a3)

        a4 = QHBoxLayout()
        a4.addWidget(QLabel("التباعد:"))
        self._line_spacing = QComboBox()
        self._line_spacing.addItems(["1.0", "1.15", "1.5", "2.0", "حرفي"])
        a4.addWidget(self._line_spacing)
        pl.addLayout(a4)

        a5 = QHBoxLayout()
        a5.addWidget(QLabel("الإزاحة:"))
        self._indent_spin = QSpinBox()
        self._indent_spin.setRange(0, 200)
        self._indent_spin.setValue(0)
        self._indent_spin.setSuffix(" px")
        a5.addWidget(self._indent_spin)
        pl.addLayout(a5)

        layout.addWidget(QLabel("تنسيق الفقرة:"))
        layout.addWidget(para_tab)
        tabs.addTab(para_tab, "الفقرة")

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _pick_color(self):
        c = QColorDialog.getColor(QColor("#000000"), self, "لون الخط")
        if c.isValid():
            self._color_btn.setStyleSheet(f"background: {c.name()}; border: 1px solid #999;")

    def _pick_highlight(self):
        c = QColorDialog.getColor(QColor("#ffff00"), self, "لون التظليل")
        if c.isValid():
            self._hl_btn.setStyleSheet(f"background: {c.name()}; border: 1px solid #999;")

    def get_formats(self):
        cursor_fmt = QTextCharFormat()
        cursor_fmt.setFont(self._font_combo.currentFont())
        cursor_fmt.setFontPointSize(self._size_spin.value())
        color = QColor(self._color_btn.styleSheet().split("background: ")[-1].split(";")[0])
        cursor_fmt.setForeground(color)
        hl = QColor(self._hl_btn.styleSheet().split("background: ")[-1].split(";")[0])
        if hl.isValid() and hl != QColor(Qt.white):
            cursor_fmt.setBackground(hl)

        block_fmt = QTextBlockFormat()
        align_map = {"يمين": Qt.AlignRight, "يسار": Qt.AlignLeft, "وسط": Qt.AlignCenter, "-两边": Qt.AlignJustify}
        align_text = self._align_combo.currentText()
        block_fmt.setAlignment(align_map.get(align_text, Qt.AlignRight))
        block_fmt.setTopMargin(self._space_before.value())
        block_fmt.setBottomMargin(self._space_after.value())
        sp = self._line_spacing.currentText()
        if sp == "حرفي":
            block_fmt.setLineHeight(0, 0)
        else:
            block_fmt.setLineHeight(int(float(sp) * 100), 1)
        block_fmt.setLeftMargin(self._indent_spin.value())

        return cursor_fmt, block_fmt


class TextEditor(QWidget):
    go_back = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._font_size = 14
        self._font_family = "Arial"
        self._text_color = QColor("#000000")
        self._bg_color = QColor(Qt.white)
        self._current_file = None
        self._init_ui()
        logger.info("تم تهيئة محرر النصوص")

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(3)

        self.setStyleSheet("""
            QWidget { background: #4a4a4a; }
            QToolBar QPushButton {
                background: #666; color: white; border: 1px solid #555;
                padding: 4px 8px; border-radius: 3px; font-size: 12px;
            }
            QToolBar QPushButton:hover { background: #888; border-color: #777; }
            QToolBar QPushButton:pressed { background: #555; }
            QToolBar QPushButton:checked { background: #e67e22; border-color: #d35400; }
        """)

        top_bar = QHBoxLayout()
        btn_back = QPushButton("← رجوع")
        btn_back.setStyleSheet("""
            QPushButton {
                background: transparent; color: #e67e22; font-size: 14px;
                border: none; font-weight: bold; padding: 5px;
            }
            QPushButton:hover { color: #d35400; }
        """)
        btn_back.clicked.connect(self.go_back.emit)
        top_bar.addWidget(btn_back)
        title = QLabel("محرر النصوص")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #e67e22;")
        title.setAlignment(Qt.AlignCenter)
        top_bar.addWidget(title)
        top_bar.addStretch()
        main_layout.addLayout(top_bar)

        toolbar1 = QToolBar()
        toolbar1.setStyleSheet("QToolBar { spacing: 3px; padding: 2px; background: #5a5a5a; }")
        toolbar1.setMovable(False)
        self._combo_font = QFontComboBox()
        self._combo_font.setMaximumWidth(180)
        self._combo_font.currentFontChanged.connect(self._on_font_changed)
        toolbar1.addWidget(self._combo_font)

        toolbar1.addSeparator()

        self._combo_size = QComboBox()
        self._combo_size.setEditable(True)
        self._combo_size.addItems([str(s) for s in [8, 9, 10, 11, 12, 14, 16, 18, 20, 22, 24, 26, 28, 36, 48, 72]])
        self._combo_size.setCurrentText("14")
        self._combo_size.setMaximumWidth(60)
        self._combo_size.currentTextChanged.connect(self._on_size_changed)
        toolbar1.addWidget(self._combo_size)

        toolbar1.addSeparator()

        btn_larger = QPushButton("A+")
        btn_larger.setToolTip("تكبير الخط")
        btn_larger.clicked.connect(self._increase_font_size)
        toolbar1.addWidget(btn_larger)

        btn_smaller = QPushButton("A-")
        btn_smaller.setToolTip("تصغير الخط")
        btn_smaller.clicked.connect(self._decrease_font_size)
        toolbar1.addWidget(btn_smaller)

        toolbar1.addSeparator()

        btn_bold = QPushButton("عريض")
        btn_bold.setCheckable(True)
        btn_bold.setToolTip("تثخين (Ctrl+B)")
        btn_bold.clicked.connect(self._toggle_bold)
        toolbar1.addWidget(btn_bold)

        btn_italic = QPushButton("مائل")
        btn_italic.setCheckable(True)
        btn_italic.setToolTip("مائل (Ctrl+I)")
        btn_italic.clicked.connect(self._toggle_italic)
        toolbar1.addWidget(btn_italic)

        btn_underline = QPushButton("تحته خط")
        btn_underline.setCheckable(True)
        btn_underline.setToolTip("تحته خط (Ctrl+U)")
        btn_underline.clicked.connect(self._toggle_underline)
        toolbar1.addWidget(btn_underline)

        btn_strike = QPushButton("يتوسطه خط")
        btn_strike.setCheckable(True)
        btn_strike.setToolTip("خط يتوسط النص")
        btn_strike.clicked.connect(self._toggle_strikeout)
        toolbar1.addWidget(btn_strike)

        toolbar1.addSeparator()

        btn_color = QPushButton("لون الخط")
        btn_color.clicked.connect(self._pick_text_color)
        toolbar1.addWidget(btn_color)

        btn_highlight = QPushButton("تظليل")
        btn_highlight.clicked.connect(self._pick_highlight_color)
        toolbar1.addWidget(btn_highlight)

        main_layout.addWidget(toolbar1)

        toolbar2 = QToolBar()
        toolbar2.setStyleSheet("QToolBar { spacing: 3px; padding: 2px; background: #5a5a5a; }")
        toolbar2.setMovable(False)

        btn_superscript = QPushButton("X²")
        btn_superscript.setToolTip("أنيق (Superscript)")
        btn_superscript.clicked.connect(self._toggle_superscript)
        toolbar2.addWidget(btn_superscript)

        btn_subscript = QPushButton("X₂")
        btn_subscript.setToolTip("منخفض (Subscript)")
        btn_subscript.clicked.connect(self._toggle_subscript)
        toolbar2.addWidget(btn_subscript)

        toolbar2.addSeparator()

        btn_align_right = QPushButton("يمين")
        btn_align_right.setToolTip("محاذاة يمين")
        btn_align_right.clicked.connect(lambda: self._set_alignment(Qt.AlignRight))
        toolbar2.addWidget(btn_align_right)

        btn_align_center = QPushButton("وسط")
        btn_align_center.setToolTip("محاذاة وسط")
        btn_align_center.clicked.connect(lambda: self._set_alignment(Qt.AlignCenter))
        toolbar2.addWidget(btn_align_center)

        btn_align_left = QPushButton("يسار")
        btn_align_left.setToolTip("محاذاة يسار")
        btn_align_left.clicked.connect(lambda: self._set_alignment(Qt.AlignLeft))
        toolbar2.addWidget(btn_align_left)

        toolbar2.addSeparator()

        btn_bullet = QPushButton("•")
        btn_bullet.setToolTip("قائمة نقاط")
        btn_bullet.clicked.connect(self._insert_bullet_list)
        toolbar2.addWidget(btn_bullet)

        btn_number = QPushButton("1.")
        btn_number.setToolTip("قائمة رقمية")
        btn_number.clicked.connect(self._insert_numbered_list)
        toolbar2.addWidget(btn_number)

        toolbar2.addSeparator()

        btn_line_spacing = QPushButton("↕")
        btn_line_spacing.setToolTip("التباعد السطري")
        btn_line_spacing.clicked.connect(self._change_line_spacing)
        toolbar2.addWidget(btn_line_spacing)

        btn_indent = QPushButton("→|")
        btn_indent.setToolTip("زيادة الإزاحة")
        btn_indent.clicked.connect(self._increase_indent)
        toolbar2.addWidget(btn_indent)

        btn_unindent = QPushButton("|←")
        btn_unindent.setToolTip("تقليل الإزاحة")
        btn_unindent.clicked.connect(self._decrease_indent)
        toolbar2.addWidget(btn_unindent)

        main_layout.addWidget(toolbar2)

        toolbar3 = QToolBar()
        toolbar3.setStyleSheet("QToolBar { spacing: 3px; padding: 2px; background: #5a5a5a; }")
        toolbar3.setMovable(False)

        btn_insert_table = QPushButton("جدول")
        btn_insert_table.clicked.connect(self._insert_table)
        toolbar3.addWidget(btn_insert_table)

        btn_insert_image = QPushButton("صورة")
        btn_insert_image.clicked.connect(self._insert_image)
        toolbar3.addWidget(btn_insert_image)

        btn_insert_hr = QPushButton("——")
        btn_insert_hr.setToolTip("خط أفقي")
        btn_insert_hr.clicked.connect(self._insert_horizontal_line)
        toolbar3.addWidget(btn_insert_hr)

        btn_insert_date = QPushButton("التاريخ")
        btn_insert_date.clicked.connect(self._insert_date)
        toolbar3.addWidget(btn_insert_date)

        btn_clear = QPushButton("مسح الكل")
        btn_clear.clicked.connect(self._clear_all)
        toolbar3.addWidget(btn_clear)

        btn_style = QPushButton("تنسيق متقدم")
        btn_style.clicked.connect(self._open_style_dialog)
        toolbar3.addWidget(btn_style)

        toolbar3.addSeparator()

        btn_margin = QPushButton("هامش")
        btn_margin.setToolTip("ضبط الهامش (1-48)")
        btn_margin.clicked.connect(lambda: self._show_margin_menu(btn_margin))
        toolbar3.addWidget(btn_margin)

        toolbar3.addSeparator()

        btn_zoom_in = QPushButton("🔍+")
        btn_zoom_in.setToolTip("تكبير (Ctrl++)")
        btn_zoom_in.clicked.connect(lambda: self.editor.zoom_in())
        toolbar3.addWidget(btn_zoom_in)

        btn_zoom_out = QPushButton("🔍-")
        btn_zoom_out.setToolTip("تصغير (Ctrl+-)")
        btn_zoom_out.clicked.connect(lambda: self.editor.zoom_out())
        toolbar3.addWidget(btn_zoom_out)

        btn_zoom_reset = QPushButton("1:1")
        btn_zoom_reset.setToolTip("إعادة الحجم (Ctrl+0)")
        btn_zoom_reset.clicked.connect(lambda: self.editor.zoom_reset())
        toolbar3.addWidget(btn_zoom_reset)

        main_layout.addWidget(toolbar3)

        for tb in (toolbar1, toolbar2, toolbar3):
            for btn in tb.findChildren(QPushButton):
                btn.setFocusPolicy(Qt.NoFocus)

        self.editor = _ZoomableTextEdit()
        self.editor.setAcceptRichText(True)
        self.editor.setPlaceholderText("")
        self.editor.setStyleSheet("""
            QTextEdit {
                font-size: 14px;
                border: none;
                background: #E7E6E6;
                color: black;
                selection-background-color: #1a73e8;
                selection-color: white;
            }
        """)
        self.editor.setCursorWidth(0)
        self.editor.setTabStopDistance(40)
        self._margin = 5
        self.editor.document().setDocumentMargin(self._margin)
        self.editor.setHtml('<div dir="rtl"></div>')
        self.editor.set_align(Qt.AlignRight)

        self._paper_w = 595
        self._paper_h = 842

        self.paper_frame = QFrame()
        self.paper_frame.setFixedSize(self._paper_w, self._paper_h)
        self.paper_frame.setStyleSheet("""
            QFrame {
                background: #E7E6E6;
                border: 1px solid #ccc;
                border-radius: 2px;
            }
        """)
        paper_layout = QVBoxLayout(self.paper_frame)
        paper_layout.setContentsMargins(0, 0, 0, 0)
        paper_layout.setSpacing(0)
        paper_layout.setAlignment(Qt.AlignCenter)
        self.editor.setFixedSize(self._paper_w, self._paper_h)
        paper_layout.addWidget(self.editor, 0, Qt.AlignCenter)

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setOffset(4, 4)
        shadow.setColor(QColor(0, 0, 0, 80))
        self.paper_frame.setGraphicsEffect(shadow)

        self._scene = QGraphicsScene()
        self._view = QGraphicsView(self._scene)
        self._view.setRenderHint(QPainter.Antialiasing)
        self._view.setRenderHint(QPainter.SmoothPixmapTransform)
        self._view.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._view.setStyleSheet("QGraphicsView { background: #4a4a4a; border: none; }")

        bg_rect = QGraphicsRectItem(-5000, -5000, 10000, 10000)
        bg_rect.setBrush(QColor("#4a4a4a"))
        bg_rect.setPen(Qt.PenStyle.NoPen)
        bg_rect.setZValue(-1)
        self._scene.addItem(bg_rect)

        self._proxy = self._scene.addWidget(self.paper_frame)
        self._proxy.setPos(0, 0)
        self._scene.setSceneRect(self._proxy.boundingRect())

        self.editor.set_paper_frame(self.paper_frame)
        self.editor.set_graphics_view(self._view, self._proxy)
        self._view.installEventFilter(self)

        zoom_in_sc = QShortcut(QKeySequence("Ctrl+Plus"), self)
        zoom_in_sc.activated.connect(lambda: self.editor.zoom_in())
        zoom_out_sc = QShortcut(QKeySequence("Ctrl+Minus"), self)
        zoom_out_sc.activated.connect(lambda: self.editor.zoom_out())
        zoom_reset_sc = QShortcut(QKeySequence("Ctrl+0"), self)
        zoom_reset_sc.activated.connect(lambda: self.editor.zoom_reset())

        main_layout.addWidget(self._view)

        QTimer.singleShot(0, lambda: self._view.fitInView(self._proxy, Qt.AspectRatioMode.KeepAspectRatio))

        bottom_bar = QHBoxLayout()

        btn_print = QPushButton("🖨 طباعة")
        btn_print.setStyleSheet("""
            QPushButton {
                background: #1a73e8; color: white; font-size: 14px;
                padding: 8px 20px; border-radius: 6px; border: none; font-weight: bold;
            }
            QPushButton:hover { background: #1557b0; }
        """)
        btn_print.clicked.connect(self._print)
        bottom_bar.addWidget(btn_print)

        btn_save_pdf = QPushButton("💾 حفظ PDF")
        btn_save_pdf.setStyleSheet("""
            QPushButton {
                background: #27ae60; color: white; font-size: 14px;
                padding: 8px 20px; border-radius: 6px; border: none; font-weight: bold;
            }
            QPushButton:hover { background: #229954; }
        """)
        btn_save_pdf.clicked.connect(self._save_pdf)
        bottom_bar.addWidget(btn_save_pdf)

        btn_save_wwk = QPushButton("💎 حفظ ورشة طباعة")
        btn_save_wwk.setStyleSheet("""
            QPushButton {
                background: #8e44ad; color: white; font-size: 14px;
                padding: 8px 20px; border-radius: 6px; border: none; font-weight: bold;
            }
            QPushButton:hover { background: #7d3c98; }
        """)
        btn_save_wwk.clicked.connect(self._save_wwk)
        bottom_bar.addWidget(btn_save_wwk)

        bottom_bar.addStretch()

        btn_open = QPushButton("📂 فتح ملف")
        btn_open.setStyleSheet("""
            QPushButton {
                background: #e67e22; color: white; font-size: 14px;
                padding: 8px 20px; border-radius: 6px; border: none; font-weight: bold;
            }
            QPushButton:hover { background: #d35400; }
        """)
        btn_open.clicked.connect(self._open_file)
        bottom_bar.addWidget(btn_open)

        main_layout.addLayout(bottom_bar)

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Wheel:
            if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
                delta = ev.angleDelta().y()
                if delta > 0:
                    self.editor.zoom_in()
                elif delta < 0:
                    self.editor.zoom_out()
                ev.accept()
                return True
        return super().eventFilter(obj, ev)

    def _show_margin_menu(self, btn):
        popup = QWidget(btn)
        popup.setWindowFlags(Qt.Popup)
        popup.setFixedWidth(120)
        popup.setFixedHeight(64)
        popup.setStyleSheet("QWidget { background: #5a5a5a; } QPushButton { background: #666; color: white; border: 1px solid #555; padding: 3px; border-radius: 3px; font-size: 12px; min-width: 36px; } QPushButton:hover { background: #888; }")
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; } QScrollBar:vertical { width: 8px; background: #5a5a5a; } QScrollBar::handle:vertical { background: #888; border-radius: 4px; min-height: 20px; }")
        container = QWidget()
        grid = QGridLayout(container)
        grid.setContentsMargins(2, 2, 2, 2)
        grid.setSpacing(2)
        for i in range(1, 71):
            b = QPushButton(str(i))
            b.setFixedSize(36, 24)
            b.clicked.connect(lambda checked, v=i: (self._set_margin(v), popup.close()))
            grid.addWidget(b, (i - 1) // 4, (i - 1) % 4)
        scroll.setWidget(container)
        layout.addWidget(scroll)
        popup.move(btn.mapToGlobal(btn.rect().bottomLeft()))
        popup.show()

    def _set_margin(self, val):
        self._margin = val
        self.editor.document().setDocumentMargin(self._margin)
        logger.info("تعيين الهوامش إلى %d", self._margin)

    def _on_font_changed(self, font):
        self._font_family = font.family()
        cursor = self.editor.textCursor()
        fmt = cursor.charFormat()
        fmt.setFontFamily(self._font_family)
        self.editor.setCurrentCharFormat(fmt)
        self.editor.setFocus()

    def _on_size_changed(self, text):
        try:
            self._font_size = int(text)
        except ValueError:
            return
        cursor = self.editor.textCursor()
        fmt = cursor.charFormat()
        fmt.setFontPointSize(self._font_size)
        self.editor.setCurrentCharFormat(fmt)
        self.editor.setFocus()

    def _toggle_bold(self):
        cursor = self.editor.textCursor()
        fmt = QTextCharFormat()
        current = cursor.charFormat().font().bold()
        fmt.setFontWeight(QFont.Bold if not current else QFont.Normal)
        cursor.mergeCharFormat(fmt)

    def _toggle_italic(self):
        cursor = self.editor.textCursor()
        fmt = QTextCharFormat()
        fmt.setFontItalic(not cursor.charFormat().font().italic())
        cursor.mergeCharFormat(fmt)

    def _toggle_underline(self):
        cursor = self.editor.textCursor()
        fmt = QTextCharFormat()
        fmt.setFontUnderline(not cursor.charFormat().font().underline())
        cursor.mergeCharFormat(fmt)

    def _toggle_strikeout(self):
        cursor = self.editor.textCursor()
        fmt = QTextCharFormat()
        fmt.setFontStrikeOut(not cursor.charFormat().font().strikeOut())
        cursor.mergeCharFormat(fmt)

    def _toggle_superscript(self):
        cursor = self.editor.textCursor()
        fmt = QTextCharFormat()
        vertical = cursor.charFormat().verticalAlignment()
        fmt.setVerticalAlignment(
            QTextCharFormat.VerticalAlignment.AlignSuperScript
            if vertical != QTextCharFormat.VerticalAlignment.AlignSuperScript
            else QTextCharFormat.VerticalAlignment.AlignNormal
        )
        cursor.mergeCharFormat(fmt)

    def _toggle_subscript(self):
        cursor = self.editor.textCursor()
        fmt = QTextCharFormat()
        vertical = cursor.charFormat().verticalAlignment()
        fmt.setVerticalAlignment(
            QTextCharFormat.VerticalAlignment.AlignSubScript
            if vertical != QTextCharFormat.VerticalAlignment.AlignSubScript
            else QTextCharFormat.VerticalAlignment.AlignNormal
        )
        cursor.mergeCharFormat(fmt)

    def _pick_text_color(self):
        c = QColorDialog.getColor(self._text_color, self, "لون الخط")
        if c.isValid():
            self._text_color = c
            cursor = self.editor.textCursor()
            fmt = QTextCharFormat()
            fmt.setForeground(c)
            cursor.mergeCharFormat(fmt)

    def _pick_highlight_color(self):
        c = QColorDialog.getColor(QColor("#ffff00"), self, "لون التظليل")
        if c.isValid():
            cursor = self.editor.textCursor()
            fmt = QTextCharFormat()
            fmt.setBackground(c)
            cursor.mergeCharFormat(fmt)

    def _set_alignment(self, alignment):
        cursor = self.editor.textCursor()
        block_fmt = QTextBlockFormat()
        if alignment == Qt.AlignRight:
            block_fmt.setAlignment(Qt.AlignLeft)
            block_fmt.setLayoutDirection(Qt.RightToLeft)
        elif alignment == Qt.AlignLeft:
            block_fmt.setAlignment(Qt.AlignRight)
            block_fmt.setLayoutDirection(Qt.RightToLeft)
        elif alignment == Qt.AlignCenter:
            block_fmt.setAlignment(Qt.AlignHCenter)
            block_fmt.setLayoutDirection(Qt.RightToLeft)
        else:
            block_fmt.setAlignment(Qt.AlignJustify)
            block_fmt.setLayoutDirection(Qt.RightToLeft)
        cursor.mergeBlockFormat(block_fmt)
        self.editor.set_align(alignment)
        self.editor.setFocus()

    def _increase_font_size(self):
        self._font_size = min(self._font_size + 2, 144)
        self._combo_size.setCurrentText(str(self._font_size))
        cursor = self.editor.textCursor()
        fmt = cursor.charFormat()
        fmt.setFontPointSize(self._font_size)
        self.editor.setCurrentCharFormat(fmt)

    def _decrease_font_size(self):
        self._font_size = max(self._font_size - 2, 6)
        self._combo_size.setCurrentText(str(self._font_size))
        cursor = self.editor.textCursor()
        fmt = cursor.charFormat()
        fmt.setFontPointSize(self._font_size)
        self.editor.setCurrentCharFormat(fmt)

    def _change_line_spacing(self):
        items = ["1.0", "1.15", "1.5", "2.0", "2.5", "3.0"]
        current, ok = QInputDialog.getItem(self, "التباعد السطري", "اختر التباعد:", items, 2, False)
        if ok:
            cursor = self.editor.textCursor()
            block_fmt = QTextBlockFormat()
            line_height = int(float(current) * 100)
            block_fmt.setLineHeight(line_height, 1)
            cursor.mergeBlockFormat(block_fmt)

    def _increase_indent(self):
        cursor = self.editor.textCursor()
        block_fmt = cursor.blockFormat()
        current = block_fmt.leftMargin()
        block_fmt.setLeftMargin(current + 20)
        cursor.mergeBlockFormat(block_fmt)

    def _decrease_indent(self):
        cursor = self.editor.textCursor()
        block_fmt = cursor.blockFormat()
        current = max(block_fmt.leftMargin() - 20, 0)
        block_fmt.setLeftMargin(current)
        cursor.mergeBlockFormat(block_fmt)

    def _insert_bullet_list(self):
        from PySide6.QtGui import QTextListFormat
        cursor = self.editor.textCursor()
        cursor.insertList(QTextListFormat.Style.ListDisc)

    def _insert_numbered_list(self):
        from PySide6.QtGui import QTextListFormat
        cursor = self.editor.textCursor()
        cursor.insertList(QTextListFormat.Style.ListDecimal)

    def _insert_table(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("إدراج جدول")
        layout = QVBoxLayout(dialog)
        row_layout = QHBoxLayout()
        row_layout.addWidget(QLabel("الصفوف:"))
        rows_spin = QSpinBox()
        rows_spin.setRange(1, 50)
        rows_spin.setValue(3)
        row_layout.addWidget(rows_spin)
        row_layout.addWidget(QLabel("الأعمدة:"))
        cols_spin = QSpinBox()
        cols_spin.setRange(1, 20)
        cols_spin.setValue(3)
        row_layout.addWidget(cols_spin)
        layout.addLayout(row_layout)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dialog.accept)
        btns.rejected.connect(dialog.reject)
        layout.addWidget(btns)
        if dialog.exec() == QDialog.Accepted:
            cursor = self.editor.textCursor()
            table_fmt = cursor.blockFormat()
            cursor.insertTable(rows_spin.value(), cols_spin.value())
            logger.info("إدراج جدول %dx%d", rows_spin.value(), cols_spin.value())

    def _insert_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "اختر صورة", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tiff);;All Files (*)"
        )
        if path:
            cursor = self.editor.textCursor()
            fmt = QTextCharFormat()
            fmt.setObjectType(1)
            image = QPixmap(path)
            if not image.isNull():
                scaled = image.scaled(400, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                cursor.insertImage(scaled.toImage())
                logger.info("إدراج صورة: %s", os.path.basename(path))

    def _insert_horizontal_line(self):
        cursor = self.editor.textCursor()
        cursor.insertHtml("<hr>")

    def _insert_date(self):
        from datetime import date
        cursor = self.editor.textCursor()
        cursor.insertText(date.today().isoformat())

    def _clear_all(self):
        reply = QMessageBox.question(self, "مسح الكل", "هل أنت متأكد من مسح جميع المحتوى؟",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.editor.clear()
            logger.info("مسح محتوى محرر النصوص")

    def _open_style_dialog(self):
        cursor = self.editor.textCursor()
        current_fmt = cursor.charFormat() if cursor.hasSelection() else QTextCharFormat()
        dlg = _StyleDialog(self, current_fmt)
        if dlg.exec() == QDialog.Accepted:
            cursor_fmt, block_fmt = dlg.get_formats()
            cursor = self.editor.textCursor()
            if cursor.hasSelection():
                cursor.mergeCharFormat(cursor_fmt)
                cursor.mergeBlockFormat(block_fmt)
            else:
                cursor.mergeCharFormat(cursor_fmt)
                cursor.mergeBlockFormat(block_fmt)

    def _print(self):
        if not getattr(self, 'subscription_check', lambda: True)():
            return
        from ui.a4_editor import PrintSetupDialog
        dialog = PrintSetupDialog(self, page_count=1, default_paper_type="ورق عادي")
        if dialog.exec() != QDialog.Accepted:
            return
        printer_name = dialog.selected_printer()
        if printer_name:
            set_printer_name(printer_name)
        pt = dialog.paper_type()
        if pt:
            set_last_paper_type(pt)
        copies = dialog.copies()
        duplex = dialog.duplex()
        printer = QPrinter(QPrinter.ScreenResolution)
        if printer_name:
            printer.setPrinterName(printer_name)
        printer.setPageSize(QPageSize(QPageSize.A4))
        printer.setCopyCount(max(1, int(copies)))
        printer.setDuplex(QPrinter.DuplexMode.DuplexLongSide if duplex else QPrinter.DuplexMode.DuplexNone)
        margin_mm = self._margin * 25.4 / 96
        if pt:
            from core.printer import _apply_paper_type, _is_photo_paper
            if _is_photo_paper(pt):
                printer.setResolution(300)
            _apply_paper_type(printer.printerName(), pt)
        vw = self.editor.viewport().width()
        vh = self.editor.viewport().height()
        _old_ss = self.editor.styleSheet()
        self.editor.setStyleSheet(_old_ss.replace("#E7E6E6", "white"))
        img = QImage(vw, vh, QImage.Format.Format_ARGB32)
        img.fill(QColor("white"))
        self.editor.viewport().render(img)
        self.editor.setStyleSheet(_old_ss)
        pw = printer.width()
        ph = printer.height()
        res = printer.resolution()
        margin_dots = int(margin_mm * res / 25.4)
        content_w = pw - 2 * margin_dots
        content_h = ph - 2 * margin_dots
        painter = QPainter()
        painter.begin(printer)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawImage(QRectF(margin_dots, margin_dots, content_w, content_h), img)
        painter.end()
        logger.info("طباعة من محرر النصوص على: %s", printer.printerName())

    def _save_pdf(self):
        if not getattr(self, 'subscription_check', lambda: True)():
            return
        path, _ = QFileDialog.getSaveFileName(self, "حفظ PDF", "", "PDF Files (*.pdf)")
        if path:
            if not path.endswith(".pdf"):
                path += ".pdf"
            try:
                printer = QPrinter(QPrinter.ScreenResolution)
                printer.setOutputFormat(QPrinter.PdfFormat)
                printer.setOutputFileName(path)
                printer.setPageSize(QPageSize(QPageSize.A4))
                printer.setResolution(96)
                vw = self.editor.viewport().width()
                vh = self.editor.viewport().height()
                _old_ss = self.editor.styleSheet()
                self.editor.setStyleSheet(_old_ss.replace("#E7E6E6", "white"))
                hi_w, hi_h = vw * 3, vh * 3
                img = QImage(hi_w, hi_h, QImage.Format.Format_ARGB32)
                img.fill(QColor("white"))
                self.editor.viewport().render(img)
                self.editor.setStyleSheet(_old_ss)
                pw = printer.width()
                ph = printer.height()
                margin_dots = self._margin
                content_w = pw - 2 * margin_dots
                content_h = ph - 2 * margin_dots
                painter = QPainter()
                painter.begin(printer)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.drawImage(QRectF(margin_dots, margin_dots, content_w, content_h), img)
                painter.end()
                QMessageBox.information(self, "تم", f"تم الحفظ: {path}")
                logger.info("حفظ PDF من محرر النصوص: %s", path)
            except Exception as e:
                logger.error("فشل حفظ PDF", exc_info=True)
                QMessageBox.critical(self, "خطأ", f"فشل حفظ PDF:\n{e}")

    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "فتح ملف", "",
            "All Supported (*.html *.htm *.txt *.wwk);;ورشة طباعة (*.wwk);;HTML (*.html *.htm);;Text (*.txt);;All (*)"
        )
        if path:
            self.load_from_file(path)

    def _save_wwk(self):
        if not getattr(self, 'subscription_check', lambda: True)():
            return
        path, _ = QFileDialog.getSaveFileName(self, "حفظ ملف ورشة طباعة", "", "ورشة طباعة (*.wwk)")
        if path:
            if not path.endswith(".wwk"):
                path += ".wwk"
            try:
                blocks_align = []
                cursor = self.editor.textCursor()
                cursor.movePosition(QTextCursor.Start)
                while True:
                    align = int(cursor.blockFormat().alignment())
                    blocks_align.append(align)
                    if not cursor.movePosition(QTextCursor.NextBlock):
                        break
                data = {
                    "format": "wwk",
                    "version": 2,
                    "html": self.editor.toHtml(),
                    "margin": self._margin,
                    "blocks_align": blocks_align,
                }
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                QMessageBox.information(self, "تم", f"تم الحفظ: {path}")
                logger.info("حفظ ملف wwk: %s", path)
            except Exception as e:
                logger.error("فشل حفظ wwk", exc_info=True)
                QMessageBox.critical(self, "خطأ", f"فشل الحفظ:\n{e}")

    def _restore_block_alignments(self, blocks_align):
        if not blocks_align:
            return
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.Start)
        last_align = Qt.AlignRight
        for i, align_int in enumerate(blocks_align):
            if i > 0:
                if not cursor.movePosition(QTextCursor.NextBlock):
                    break
            block_fmt = QTextBlockFormat()
            block_fmt.setAlignment(Qt.AlignmentFlag(align_int))
            cursor.mergeBlockFormat(block_fmt)
            last_align = Qt.AlignmentFlag(align_int)
        if last_align & Qt.AlignLeft:
            self.editor.set_align(Qt.AlignLeft)
        elif last_align & Qt.AlignCenter:
            self.editor.set_align(Qt.AlignCenter)
        else:
            self.editor.set_align(Qt.AlignRight)

    def load_from_file(self, path):
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".wwk":
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.editor.setHtml(data.get("html", ""))
                self._margin = data.get("margin", 20)
                self.editor.document().setDocumentMargin(self._margin)
                self._restore_block_alignments(data.get("blocks_align"))
            elif ext in (".html", ".htm"):
                with open(path, "r", encoding="utf-8") as f:
                    self.editor.setHtml(f.read())
            elif ext == ".txt":
                with open(path, "r", encoding="utf-8") as f:
                    self.editor.setPlainText(f.read())
            else:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                    if "<html" in content.lower():
                        self.editor.setHtml(content)
                    else:
                        self.editor.setPlainText(content)
            self._current_file = path
            logger.info("فتح ملف في محرر النصوص: %s", path)
        except Exception as e:
            QMessageBox.warning(self, "خطأ", f"تعذر فتح الملف: {e}")
