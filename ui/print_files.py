import logging
import io
import os
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                               QListWidget, QListWidgetItem, QLabel, QFileDialog,
                               QScrollArea, QMessageBox, QDialog, QComboBox,
                               QRadioButton, QFormLayout, QToolButton, QDialogButtonBox,
                               QCheckBox)
from PySide6.QtCore import Qt, Signal, QSizeF, QRect, QSettings
from PySide6.QtGui import QPixmap, QPainter, QPageSize, QShortcut, QKeySequence
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
from core.printer import (show_printer_properties, get_captured_devmode,
                          _apply_captured_devmode_to_printer,
                          _inject_devmode_to_spooler, _restore_printer_devmode)
from PIL import Image as PILImage

logger = logging.getLogger(__name__)

_PAGE_ZOOM_PREVIEW = 2.0
_PRINT_DPI = 300


def _fit_rect(src_size, page_rect):
    """مستطيل داخل الورقة يناسب محتوى بحجم src_size دون قصّ (Expanded في المنتصف)."""
    if page_rect.width() <= 0 or page_rect.height() <= 0:
        return page_rect
    scale = min(page_rect.width() / src_size.width(),
                page_rect.height() / src_size.height())
    tw = int(src_size.width() * scale)
    th = int(src_size.height() * scale)
    x = page_rect.x() + (page_rect.width() - tw) // 2
    y = page_rect.y() + (page_rect.height() - th) // 2
    return QRect(x, y, tw, th)


def _pdf_pages(path):
    """عدد صفحات ملف PDF (يرجع 0 عند الفشل)."""
    try:
        import fitz
    except Exception as e:
        logger.warning("pymupdf غير متاح في هذه النسخة (طباعة ملفات PDF ستفشل): %s", e)
        return 0
    try:
        with fitz.open(path) as doc:
            return len(doc)
    except Exception as e:
        logger.warning("تعذّر فتح PDF %s: %s", path, e)
        return 0


def _render_pdf_page(path, page_index, zoom):
    """يعيد QPixmap لصفحة PDF بعد تحويلها إلى صورة (لا يعدّل الملف).

    يرجع QPixmap() فارغة عند فشل pymupdf أو فشل قراءة الصفحة — بدل انهيار
    صامت كان يوقف قسم طباعة الملفات بالكامل على بعض الأجهزة.
    """
    try:
        import fitz
    except Exception as e:
        logger.warning("pymupdf غير متاح: %s", e)
        return QPixmap()
    try:
        with fitz.open(path) as doc:
            page = doc[page_index]
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom),
                                  colorspace=fitz.csRGB, alpha=False)
            img = PILImage.frombytes("RGB", [pix.width, pix.height], pix.samples)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
        qp = QPixmap()
        qp.loadFromData(buf.getvalue())
        return qp
    except Exception as e:
        logger.warning("فشل عرض صفحة PDF %s: %s", path, e)
        return QPixmap()


class _FileDropList(QListWidget):
    """قائمة ملفات تقبل إفلات ملفات PDF من الخارج وتعيد مساراتها."""
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.files_dropped.emit(
                [u.toLocalFile() for u in event.mimeData().urls()])
        else:
            super().dropEvent(event)


class _PrintDialog(QDialog):
    """نافذة طباعة عربية: اختيار الطابعة + وجه واحد/وجهين + خصائص الطابعة."""

    def __init__(self, printer, parent=None):
        super().__init__(parent)
        self.setWindowTitle("طباعة الملفات")
        self.setModal(True)
        self._printer = printer
        self.duplex_on = False

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self._combo = QComboBox()
        self._populate_printers()
        self._combo.currentIndexChanged.connect(self._on_printer_changed)
        form.addRow("الطابعة:", self._combo)
        layout.addLayout(form)

        btn_props = QPushButton("⚙ خصائص الطابعة")
        btn_props.setToolTip("اختيار نوع الورق الفعلي الذي تطبعه الطابعة، جودة الطباعة، وبقية خصائص الطابعة")
        btn_props.setStyleSheet("""
            QPushButton {
                background: #5a5a5a; color: white; font-size: 12px; font-weight: bold;
                padding: 6px 12px; border-radius: 5px; border: none;
            }
            QPushButton:hover { background: #e67e22; }
        """)
        btn_props.clicked.connect(self._open_properties)
        layout.addWidget(btn_props)

        dual_box = QVBoxLayout()
        dual_box.addWidget(QLabel("نوع الطباعة:"))
        self._rb_single = QRadioButton("وجه واحد")
        self._rb_duplex = QRadioButton("وجهين (طباعة على الوجهين)")
        duplex_last = QSettings("ورشة طباعة", "Printer").value(
            "print_files_duplex", False, type=bool)
        self._rb_duplex.setChecked(duplex_last)
        self._rb_single.setChecked(not duplex_last)
        dual_box.addWidget(self._rb_single)
        dual_box.addWidget(self._rb_duplex)
        layout.addLayout(dual_box)

        self._cb_reverse = QCheckBox("فعِّلها إذا تريد أن تطبع الملفات من آخر ملف")
        self._cb_reverse.setChecked(False)
        self._cb_reverse.setToolTip(
            "بعض الطابعات مظبوطة على \"الطباعة من الخلف إلى الأمام\". هذه الخانة "
            "تُرسل الصفحات بترتيب معكوس من البرنامج لتخرج في النهاية بالترتيب الصحيح 1، 2، 3...")
        layout.addWidget(self._cb_reverse)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("🖨️ طباعة")
        buttons.button(QDialogButtonBox.Cancel).setText("إلغاء")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _populate_printers(self):
        self._combo.blockSignals(True)
        self._combo.clear()
        default = QPrinterInfo.defaultPrinter()
        names = [info.printerName() for info in QPrinterInfo.availablePrinters()]
        if default and default.printerName() and default.printerName() not in names:
            names.insert(0, default.printerName())
        for nm in names:
            self._combo.addItem(nm)
        if not names:
            self._combo.addItem("(لا توجد طابعات)")
        cur = self._printer.printerName()
        if cur and cur in names:
            self._combo.setCurrentText(cur)
        self._combo.blockSignals(False)

    def _on_printer_changed(self, index):
        nm = self._combo.itemText(index)
        if nm and not nm.startswith("(") and nm != self._printer.printerName():
            try:
                self._printer.setPrinterName(nm)
            except Exception as e:
                logger.warning("تعذّر اختيار الطابعة %s: %s", nm, e)

    def _open_properties(self):
        printer_name = self._combo.currentText().strip()
        if not printer_name or printer_name.startswith("("):
            QMessageBox.information(self, "تنبيه", "اكتب أو اختر اسم الطابعة أولاً.")
            return
        logger.info("فتح خصائص الطابعة: %r", printer_name)
        ok, why = show_printer_properties(self, printer_name)
        if not ok:
            QMessageBox.warning(self, "تنبيه",
                                f"تعذر فتح خصائص الطابعة '{printer_name}'.\n{why or ''}")

    def accept(self):
        self.duplex_on = self._rb_duplex.isChecked()
        self.reverse_order = self._cb_reverse.isChecked()
        # حفظ آخر اختيار وجه واحد/وجهين ليظهر كما هو في المرات القادمة
        QSettings("ورشة طباعة", "Printer").setValue("print_files_duplex", self.duplex_on)
        super().accept()


class PrintFilesEditor(QWidget):
    go_back = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayoutDirection(Qt.RightToLeft)
        self._files = []      # [{path, name, pages}]
        self._preview_page = 1
        self._preview_pix = QPixmap()
        self._last_path = None
        self._printer = QPrinter()
        self._build_ui()
        QShortcut(QKeySequence("Ctrl+P"), self, activated=self._print_all)
        self._refresh_list()
        self._clear_preview()

    # ── بناء الواجهة ──────────────────────────────────────────────
    def _build_ui(self):
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        btn_back = QPushButton("↩ رجوع")
        btn_back.clicked.connect(self.go_back.emit)
        btn_add = QPushButton("+ إضافة ملفات PDF")
        btn_add.clicked.connect(self._add_files)
        btn_remove = QPushButton("🗑 إزالة المحدد")
        btn_remove.clicked.connect(self._remove_selected)
        btn_clear_all = QPushButton("🗑 حذف الكل")
        btn_clear_all.setToolTip("حذف جميع الملفات من قائمة الطباعة")
        btn_clear_all.clicked.connect(self._clear_all)
        btn_print = QPushButton("🖨️ طباعة")
        btn_print.setToolTip("طباعة جميع الملفات بالترتيب (Ctrl+P)")
        btn_print.clicked.connect(self._print_all)
        for b in (btn_back, btn_add, btn_remove, btn_clear_all):
            top.addWidget(b)
        top.addStretch()
        top.addWidget(btn_print)
        layout.addLayout(top)

        split = QHBoxLayout()

        # العمود الأيسر: قائمة الملفات المرتبة
        left_col = QVBoxLayout()
        list_label = QLabel("ملفات الطباعة (بالترتيب من الأول إلى الأخير):")
        left_col.addWidget(list_label)
        self._list = _FileDropList()
        self._list.setSelectionMode(QListWidget.SingleSelection)
        self._list.setToolTip("اضغط على ملف لمعاينته — اسحب ملفات PDF وأفلتها هنا لإضافتها")
        self._list.currentRowChanged.connect(self._on_select)
        self._list.files_dropped.connect(self._append_paths)
        left_col.addWidget(self._list, 1)
        hint = QLabel("نقل ملف: اضغط سهم ▲ أو ▼ أمام اسم الملف لتحريكه في الترتيب")
        hint.setStyleSheet("color: #666; font-size: 12px;")
        left_col.addWidget(hint)

        # العمود الأيمن: صندوق المعاينة
        right_col = QVBoxLayout()
        preview_label = QLabel("معاينة الملف:")
        right_col.addWidget(preview_label)
        self._preview_scroll = QScrollArea()
        self._preview_scroll.setWidgetResizable(False)
        self._preview_scroll.setAlignment(Qt.AlignCenter)
        self._preview_scroll.setMinimumSize(320, 400)
        self._preview_widget = QLabel()
        self._preview_widget.setAlignment(Qt.AlignCenter)
        self._preview_widget.setStyleSheet("background:#ffffff; border:1px solid #aaa;")
        self._preview_scroll.setWidget(self._preview_widget)
        right_col.addWidget(self._preview_scroll, 1)

        page_row = QHBoxLayout()
        btn_prev_page = QPushButton("◀ صفحة")
        btn_prev_page.clicked.connect(self._prev_page)
        btn_next_page = QPushButton("صفحة ▶")
        btn_next_page.clicked.connect(self._next_page)
        self._page_label = QLabel()
        self._file_label = QLabel()
        page_row.addWidget(btn_prev_page)
        page_row.addWidget(self._page_label)
        page_row.addWidget(btn_next_page)
        right_col.addLayout(page_row)
        right_col.addWidget(self._file_label)

        split.addLayout(left_col, 1)
        split.addLayout(right_col, 1)
        layout.addLayout(split, 1)

    # ── إدارة الملفات ─────────────────────────────────────────────
    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "اختر ملفات PDF", "", "PDF (*.pdf)")
        if paths:
            self._append_paths(paths)

    def _append_paths(self, paths):
        added = 0
        added_paths = []
        for path in paths:
            if not path.lower().endswith(".pdf"):
                continue
            if any(f["path"] == path for f in self._files):
                continue
            pages = _pdf_pages(path)
            if pages <= 0:
                QMessageBox.warning(self, "تنبيه", "تعذّر فتح الملف:\n%s" % path)
                continue
            self._files.append({
                "path": path,
                "name": os.path.basename(path),
                "pages": pages,
            })
            added += 1
            added_paths.append(path)
        if added:
            self._refresh_list()
            for k, f in enumerate(self._files):
                if f["path"] == added_paths[-1]:
                    self._list.setCurrentRow(k)
                    break

    def _clear_all(self):
        if not self._files:
            QMessageBox.information(self, "تنبيه", "لا توجد ملفات لحذفها.")
            return
        answer = QMessageBox.question(
            self, "تأكيد الحذف",
            "هل تريد حذف جميع الملفات من قائمة الطباعة؟",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self._files.clear()
        self._last_path = None
        self._preview_page = 1
        self._refresh_list()
        self._clear_preview()
        logger.info("تم حذف جميع الملفات من قائمة الطباعة")

    def _selected_index(self):
        row = self._list.currentRow()
        if 0 <= row < len(self._files):
            return row
        return -1

    def _make_row_widget(self, i, f):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(2, 1, 2, 1)
        h.setSpacing(4)
        col = QVBoxLayout()
        col.setSpacing(1)
        up = QToolButton()
        up.setText("▲")
        up.setAutoRaise(True)
        up.setFixedSize(20, 18)
        up.setToolTip("تحريك الملف إلى الأعلى (أقرب للأول)")
        down = QToolButton()
        down.setText("▼")
        down.setAutoRaise(True)
        down.setFixedSize(20, 18)
        down.setToolTip("تحريك الملف إلى الأسفل (أقرب للأخير)")
        up.clicked.connect(lambda checked=False, p=f["path"]: self._move_file(p, -1))
        down.clicked.connect(lambda checked=False, p=f["path"]: self._move_file(p, 1))
        col.addWidget(up)
        col.addWidget(down)
        h.addLayout(col)
        label = QLabel(f"{i}. {f['name']}  ({f['pages']} {'صفحة' if f['pages'] == 1 else 'صفحات'})")
        label.setToolTip(f["path"])
        h.addWidget(label, 1)
        return w

    def _refresh_list(self):
        was = self._last_path
        self._list.blockSignals(True)
        self._list.clear()
        for i, f in enumerate(self._files, start=1):
            item = QListWidgetItem()
            item.setToolTip(f["path"])
            row = self._make_row_widget(i, f)
            item.setSizeHint(row.sizeHint())
            self._list.addItem(item)
            self._list.setItemWidget(item, row)
        self._list.blockSignals(False)
        if was:
            for k, f in enumerate(self._files):
                if f["path"] == was:
                    self._list.setCurrentRow(k)
                    return
        if self._files:
            self._list.setCurrentRow(0)

    def _remove_selected(self):
        row = self._selected_index()
        if row < 0:
            QMessageBox.information(self, "تنبيه", "حدد ملفاً أولاً لإزالته.")
            return
        del self._files[row]
        self._last_path = None
        self._refresh_list()
        if not self._files:
            self._clear_preview()
        self._preview_page = 1

    # ── إعادة الترتيب بالأسهم ─────────────────────────────────────
    def _move_file(self, path, delta):
        idx = None
        for i, f in enumerate(self._files):
            if f["path"] == path:
                idx = i
                break
        if idx is None:
            return
        j = idx + delta
        if j < 0 or j >= len(self._files):
            return
        self._last_path = path
        self._files.insert(j, self._files.pop(idx))
        self._refresh_list()

    # ── المعاينة ──────────────────────────────────────────────────
    def _on_select(self, row):
        if row < 0 or row >= len(self._files):
            self._clear_preview()
            return
        f = self._files[row]
        self._last_path = f["path"]
        self._preview_page = 1
        self._show_file(row, 1)

    def _show_file(self, row, page):
        f = self._files[row]
        self._preview_page = max(1, min(page, f["pages"]))
        pix = _render_pdf_page(f["path"], self._preview_page - 1, _PAGE_ZOOM_PREVIEW)
        self._preview_pix = pix
        self._update_preview()
        self._file_label.setText(self._file_label_text(f))
        self._page_label.setText(f"صفحة {self._preview_page} / {f['pages']}")

    def _file_label_text(self, f):
        idx = self._files.index(f) + 1
        return f"الملف (الترتيب {idx}): {f['name']}"

    def _update_preview(self):
        if self._preview_pix.isNull():
            return
        avail = self._preview_scroll.viewport().width() - 10
        if avail > 20:
            scaled = self._preview_pix.scaledToWidth(
                min(avail, self._preview_pix.width()), Qt.SmoothTransformation)
        else:
            scaled = self._preview_pix
        self._preview_widget.setPixmap(scaled)
        self._preview_widget.adjustSize()

    def _prev_page(self):
        row = self._selected_index()
        if row < 0:
            return
        self._show_file(row, self._preview_page - 1)

    def _next_page(self):
        row = self._selected_index()
        if row < 0:
            return
        self._show_file(row, self._preview_page + 1)

    def _clear_preview(self):
        self._preview_pix = QPixmap()
        self._preview_widget.setPixmap(QPixmap())
        self._preview_widget.adjustSize()
        self._file_label.setText("")
        self._page_label.setText("")

    # ── الطباعة ───────────────────────────────────────────────────
    def _print_all(self):
        if not self._files:
            QMessageBox.information(self, "تنبيه", "أضف ملفات PDF أولاً.")
            return
        if not getattr(self, 'subscription_check', lambda: True)():
            return
        dlg = _PrintDialog(self._printer, self)
        if dlg.exec() != QDialog.Accepted:
            return
        printer = self._printer
        # تفضيلات خصائص الطابعة (نافذة "خصائص الطابعة" نفسها الموجودة في
        # أقسام الهويات والصور) إن سبق للمستخدم ضبطها في هذه الجلسة.
        devmode = None
        injected_dm = None
        printer_name = ""
        try:
            printer_name = printer.printerName() or ""
        except Exception:
            printer_name = ""
        if printer_name:
            try:
                devmode = get_captured_devmode(printer_name)
            except Exception:
                devmode = None
        if devmode is not None:
            _apply_captured_devmode_to_printer(printer, devmode)
            if os.name == 'nt':
                try:
                    injected_dm = _inject_devmode_to_spooler(printer_name, devmode)
                except Exception as e:
                    logger.warning("حقن DEVMODE فشل: %s", e)
                    injected_dm = None

        # اختيار وجه واحد/وجهين من النافذة يطغى دائماً
        if dlg.duplex_on:
            printer.setDuplex(QPrinter.DuplexMode.DuplexLongSide)
        else:
            printer.setDuplex(QPrinter.DuplexMode.DuplexNone)

        if devmode is not None:
            # ورق/جودة/لون/اتجاه من خصائص الطابعة.
            printer.setFullPage(True)
        else:
            # من دون تفضيلات خصائص: نثبّت المقاس مرة واحدة قبل بدء الطباعة من
            # الصفحة الأولى. لا نغيّر المقاس أثناء عمل الطابعة لأن ذلك يُخرج
            # أوراقاً بيضاء ويوقف المهمة على ويندوز. أي صفحة تختلف عن هذا المقاس
            # تُطبع داخل الورقة نفسها مع التوسيط والتكبير/التصغير حتى تتناسب.
            printer.setResolution(_PRINT_DPI)
            printer.setFullPage(True)
            try:
                import fitz
                first_doc = fitz.open(self._files[0]["path"])
                first_page = first_doc[0]
                paper_w_mm = first_page.rect.width * 25.4 / 72.0
                paper_h_mm = first_page.rect.height * 25.4 / 72.0
                first_doc.close()
            except Exception as e:
                logger.warning("تعذّر قراءة حجم أول ورقة، سنعتمد A4: %s", e)
                paper_w_mm, paper_h_mm = 210.0, 297.0
            printer.setPageSize(QPageSize(
                QSizeF(paper_w_mm, paper_h_mm), QPageSize.Millimeter))

        painter = QPainter(printer)
        if not painter.isActive():
            QMessageBox.warning(self, "خطأ", "فشل بدء الطباعة.")
            return
        printed = 0
        reverse_order = getattr(dlg, 'reverse_order', False)
        try:
            page_rect = printer.pageRect(QPrinter.DevicePixel)
            files_iter = reversed(self._files) if reverse_order else self._files
            for idx, f in enumerate(files_iter):
                doc = None
                try:
                    import fitz
                    doc = fitz.open(f["path"])
                    pages = len(doc)
                except Exception as e:
                    logger.warning("تعذّر فتح %s أثناء الطباعة: %s", f["path"], e)
                    continue
                try:
                    page_range = range(pages - 1, -1, -1) if reverse_order else range(pages)
                    for pi in page_range:
                        if printed > 0:
                            printer.newPage()
                        zoom = _PRINT_DPI / 72.0
                        page = doc[pi]
                        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom),
                                              colorspace=fitz.csRGB, alpha=False)
                        img = PILImage.frombytes("RGB", [pix.width, pix.height], pix.samples)
                        buf = io.BytesIO()
                        img.save(buf, format="PNG")
                        qp = QPixmap()
                        if qp.loadFromData(buf.getvalue()) and not qp.isNull():
                            target = _fit_rect(qp.size(), page_rect)
                            painter.drawPixmap(target, qp)
                            printed += 1
                finally:
                    if doc is not None:
                        doc.close()
        finally:
            painter.end()
            if injected_dm is not None and printer_name:
                try:
                    _restore_printer_devmode(printer_name, injected_dm)
                except Exception as e:
                    logger.warning("استعادة DEVMODE فشل: %s", e)
        if printed:
            QMessageBox.information(self, "تم",
                "طُبعت %d صفحة من %d ملف بالتسلسل المحدد." % (printed, len(self._files)))
        else:
            QMessageBox.warning(self, "تنبيه", "لم تُطبع أي صفحة.")

    # ── السحب والإفلات على المساحات الخالية ───────────────────────
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls()]
        event.acceptProposedAction()
        self._append_paths([p for p in paths if p.lower().endswith(".pdf")])