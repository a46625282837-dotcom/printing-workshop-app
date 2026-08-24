import logging
import os
import sys
import webbrowser
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout,
                               QPushButton, QLabel, QHBoxLayout,
                               QStackedWidget, QLineEdit, QMessageBox,
                               QTableWidget, QTableWidgetItem, QHeaderView,
                               QFileDialog, QInputDialog, QDateEdit, QFrame,
                               QDialog, QApplication, QProgressBar,
                               QComboBox, QPlainTextEdit)
from PySide6.QtGui import QAction
from PySide6.QtCore import Qt, QSize, Signal, QDate, QTimer
from PySide6.QtGui import QAction, QIcon, QColor, QPixmap, QPainter, QBrush, QFont
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QLayout, QLayoutItem, QScrollArea
from datetime import date, timedelta

from ui.a4_editor import A4Editor
from ui.photo_editor import PhotoEditor
from ui.pdf_editor import PdfEditor
from ui.text_editor import TextEditor
from ui.scanner_page import ScannerPage

def _img_path(rel):
    base = getattr(sys, '_MEIPASS', os.path.join(os.path.dirname(__file__), '..'))
    return os.path.join(base, 'img', rel)

IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff'}
_NO_SUB_MSG = "يجب أن تشترك قبل الاستخدام. تواصل مع المالك: واتساب 07865402819"

logger = logging.getLogger(__name__)

FUTURE_STYLE = """
    QPushButton { background: #f0f0f0; border-color: #ccc; color: #aaa; }
"""


class FlowLayout(QLayout):
    def __init__(self, parent=None, spacing=20):
        super().__init__(parent)
        self._spacing = spacing
        self._items = []

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(
            self.geometry().adjusted(0, 0, width - self.geometry().width(), 0),
            test_only=True,
        )

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        from PySide6.QtCore import QSize
        size = QSize(0, 0)
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        from PySide6.QtCore import QSize as QS
        size += QS(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only):
        from PySide6.QtCore import QRect
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0
        for item in self._items:
            space_x = self._spacing
            space_y = self._spacing
            next_x = x + item.sizeHint().width() + space_x
            if next_x - space_x > effective.right() + 1 and line_height > 0:
                x = effective.x()
                y = y + line_height + space_y
                next_x = x + item.sizeHint().width() + space_x
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(x, y, item.sizeHint().width(), item.sizeHint().height()))
            x = next_x
            line_height = max(line_height, item.sizeHint().height())
        return y + line_height - rect.y() + m.bottom()


def _make_avatar_pixmap(letter, size=48):
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QBrush(QColor("#1a73e8")))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(0, 0, size, size)
    painter.setPen(QColor(Qt.white))
    f = QFont("Arial", size // 2, QFont.Bold)
    painter.setFont(f)
    painter.drawText(pix.rect(), Qt.AlignCenter, letter)
    painter.end()
    return pix


def _make_circular_pixmap(source, size):
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QBrush(Qt.white))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(0, 0, size, size)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    src = source.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x = (src.width() - size) // 2
    y = (src.height() - size) // 2
    painter.drawPixmap(0, 0, src.copy(x, y, size, size))
    painter.end()
    return pix


class ClickableLabel(QLabel):
    clicked = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


class PlusButton(QPushButton):
    def __init__(self, label, tooltip, callback, enabled=True):
        super().__init__(label)
        self._sz = 120
        self.setFixedSize(self._sz, self._sz)
        self.setToolTip(tooltip)
        self.setStyleSheet("""
            QPushButton {
                background: #f0f0f0;
                border: 3px solid #ccc;
                border-radius: 20px;
            }
            QPushButton:hover {
                border-color: #1a73e8;
                border-width: 3px;
                background: #e8f0fe;
            }
            QPushButton:disabled {
                opacity: 0.4;
                border-color: #ddd;
            }
        """ + (FUTURE_STYLE if not enabled else ""))
        self.setEnabled(enabled)
        self.clicked.connect(callback)

    def enterEvent(self, event):
        inset = 6
        self.setIconSize(QSize(self._sz - inset, self._sz - inset))
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setIconSize(QSize(self._sz, self._sz))
        super().leaveEvent(event)


def _make_card(label_text, button, parent_layout):
    col = QVBoxLayout()
    col.setAlignment(Qt.AlignCenter)
    col.addWidget(button)
    lbl = QLabel(label_text)
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setStyleSheet("font-size: 12px; color: #555;")
    col.addWidget(lbl)
    parent_layout.addLayout(col)


def _make_eye_pixmap(open_eye=True):
    pix = QPixmap(24, 24)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    f = QFont("Segoe UI Emoji", 14)
    p.setFont(f)
    p.drawText(pix.rect(), Qt.AlignCenter, "👁" if open_eye else "🙈")
    p.end()
    return pix

def _make_password_field(placeholder_text, font_size="14px", padding="8px"):
    line = QLineEdit()
    line.setPlaceholderText(placeholder_text)
    line.setEchoMode(QLineEdit.Password)
    line.setAlignment(Qt.AlignCenter)
    line.setStyleSheet(f"""
        QLineEdit {{
            font-size: {font_size}; padding: {padding}; border: 2px solid #ddd;
            border-radius: 8px; max-width: 300px; min-width: 250px;
        }}
    """)
    action = QAction(QIcon(_make_eye_pixmap(True)), "")
    line.addAction(action, QLineEdit.TrailingPosition)
    def toggle():
        if line.echoMode() == QLineEdit.Password:
            line.setEchoMode(QLineEdit.Normal)
            action.setIcon(QIcon(_make_eye_pixmap(False)))
        else:
            line.setEchoMode(QLineEdit.Password)
            action.setIcon(QIcon(_make_eye_pixmap(True)))
    action.triggered.connect(toggle)
    return line, line


class MainWindow(QMainWindow):
    def __init__(self, use_api=False):
        super().__init__()
        self._use_api = use_api
        self.setWindowTitle("ورشة طباعة")
        self.setMinimumSize(600, 520)
        self.setLayoutDirection(Qt.RightToLeft)
        self.setAcceptDrops(True)
        screen = QApplication.primaryScreen().geometry()
        self._scale = min(screen.width() / 1366, screen.height() / 768, 1.5)
        desired_w = int(min(screen.width() * 0.85, 1400))
        desired_h = int(min(screen.height() * 0.85, 900))
        self.resize(max(600, int(desired_w)), max(520, int(desired_h)))
        self._banner_w = int(240 * self._scale)
        self._banner_h = int(320 * self._scale)
        if use_api:
            from core.database import set_api_mode
            from core.api_client import set_session_expired_callback
            set_api_mode(True)
            set_session_expired_callback(self._on_session_expired)
            self._users = {}
            self._api_data = None
        else:
            from core.database import init_db, load_users, save_user, get_subscription_required
            init_db()
            self._users = load_users()
            self._subscription_required = get_subscription_required()
        if not use_api:
            if "ahmed" not in self._users:
                self._users["ahmed"] = {
                    "password": "Aa511F511fa", "shop_name": "المالك",
                    "phone": "", "reg_date": date.today().strftime("%Y-%m-%d"),
                    "subscription_days": 9999, "is_admin": True,
                    "profile_pixmap": None,
                    "last_warn_date": "",
                    "subscriptions": [],
                }
                save_user("ahmed", self._users["ahmed"])
            admin = self._users.setdefault("ahmed", {})
            admin.setdefault("banner_left_pixmap", None)
            admin.setdefault("banner_right_pixmap", None)
            admin.setdefault("banner_left_link", "")
            admin.setdefault("banner_right_link", "")
        self._logged_in = False
        self._username = ""
        self._display_name = ""
        self._is_admin = False
        self._subscription_required = True
        self._prev_page_index = 0
        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)
        self._main_widget, self._welcome_label, self._profile_label, self._btn_login, self._btn_register = self._build_main_screen()
        self._welcome_label.clicked.connect(self._open_profile)
        self._profile_label.clicked.connect(self._open_profile)
        self._stack.addWidget(self._main_widget)
        self._editor = A4Editor()
        self._editor.subscription_check = lambda: self._require_subscription()
        self._editor.go_back.connect(self._switch_to_main)
        self._stack.addWidget(self._editor)
        self._photo_editor = PhotoEditor()
        self._photo_editor.subscription_check = lambda: self._require_subscription()
        self._photo_editor.go_back.connect(self._switch_to_main)
        self._stack.addWidget(self._photo_editor)
        self._pdf_editor = PdfEditor()
        self._pdf_editor.subscription_check = lambda: self._require_subscription()
        self._pdf_editor.go_back.connect(self._switch_to_main)
        self._stack.addWidget(self._pdf_editor)
        self._text_editor = TextEditor()
        self._text_editor.subscription_check = lambda: self._require_subscription()
        self._text_editor.go_back.connect(self._switch_to_main)
        self._pending_file = None
        self._stack.addWidget(self._text_editor)
        self._scanner_page = ScannerPage()
        self._scanner_page.subscription_check = lambda: self._require_subscription()
        self._scanner_page.go_back.connect(self._switch_to_main)
        self._stack.addWidget(self._scanner_page)
        self._login_widget = self._build_login_page()
        self._stack.addWidget(self._login_widget)
        self._register_widget = self._build_register_page()
        self._stack.addWidget(self._register_widget)
        self._dashboard_widget = self._build_dashboard_page()
        self._stack.addWidget(self._dashboard_widget)
        self._profile_widget = self._build_profile_page()
        self._stack.addWidget(self._profile_widget)
        self._subscription_widget = self._build_subscription_page()
        self._stack.addWidget(self._subscription_widget)
        self._my_subs_widget = self._build_my_subscriptions_page()
        self._stack.addWidget(self._my_subs_widget)
        self._notifications_widget = self._build_notifications_page()
        self._stack.addWidget(self._notifications_widget)
        self._replies_widget = self._build_notification_replies_page()
        self._stack.addWidget(self._replies_widget)
        self._user_stats_widget = self._build_user_stats_page()
        self._stack.addWidget(self._user_stats_widget)

        self._shown_notif_ids = set()
        self._notif_queue = []
        self._notif_list = []
        self._notif_unread = 0
        self._notif_processing = False
        self._notif_timer = QTimer(self)
        self._notif_timer.setInterval(60000)
        self._notif_timer.timeout.connect(self._on_notif_timer)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(15000)
        self._refresh_timer.timeout.connect(self._auto_refresh_data)

        self._update_banners()

        self._notif_timer.start()
        self._refresh_timer.start()

        self._try_restore_session()

    def _build_main_screen(self):
        widget = QWidget()
        widget.setObjectName("mainScreen")
        img_path = _img_path('i5.jpeg').replace('\\', '/')
        if os.path.exists(img_path):
            widget.setStyleSheet(f"""
                QWidget#mainScreen {{
                    border-image: url({img_path}) 0 0 0 0 stretch stretch;
                }}
            """)
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignCenter)
        layout.setContentsMargins(30, 15, 30, 15)
 
        top_row = QHBoxLayout()
        top_row.setAlignment(Qt.AlignLeft)

        self._notif_btn = QPushButton("🔔 إشعارات")
        self._notif_btn.setFixedHeight(34)
        self._notif_btn.setStyleSheet("""
            QPushButton {
                background: rgba(230, 126, 34, 220); color: white;
                font-size: 13px; font-weight: bold; border: none;
                border-radius: 17px; padding: 0 16px;
            }
            QPushButton:hover { background: rgba(211, 84, 0, 220); }
        """)
        self._notif_btn.clicked.connect(self._open_notifications_page)
        self._notif_btn.hide()
        top_row.addWidget(self._notif_btn)

        top_row.addStretch()

        profile_label = ClickableLabel()
        profile_label.setFixedSize(48, 48)
        profile_label.setAlignment(Qt.AlignCenter)
        profile_label.setStyleSheet("""
            QLabel { background: transparent; border-radius: 24px; }
        """)
        profile_label.hide()

        welcome_label = ClickableLabel()
        welcome_label.setAlignment(Qt.AlignCenter)
        welcome_label.setStyleSheet("""
            QLabel {
                font-size: 16px; font-weight: bold; color: white;
                background: rgba(0,0,0,0.3); border-radius: 8px;
                padding: 5px 15px;
            }
            QLabel:hover { background: rgba(0,0,0,0.5); }
        """)
        welcome_label.hide()

        user_info = QVBoxLayout()
        user_info.setAlignment(Qt.AlignTop)
        user_info.addWidget(welcome_label)
        top_row.addLayout(user_info)
        top_row.addSpacing(8)
        top_row.addWidget(profile_label)

        layout.addLayout(top_row)

        layout.addStretch(2)

        title = QLabel("ورشة طباعة")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 32px; font-weight: bold; color: white; margin-bottom: 10px;")
        shadow = QGraphicsDropShadowEffect()
        shadow.setColor(QColor(0, 0, 0, 200))
        shadow.setOffset(2, 2)
        shadow.setBlurRadius(12)
        title.setGraphicsEffect(shadow)
        layout.addWidget(title)

        subtitle = QLabel("اختر الخدمة للبدء")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("font-size: 16px; color: rgba(255,255,255,0.9); margin-bottom: 40px;")
        sub_shadow = QGraphicsDropShadowEffect()
        sub_shadow.setColor(QColor(0, 0, 0, 180))
        sub_shadow.setOffset(1, 1)
        sub_shadow.setBlurRadius(8)
        subtitle.setGraphicsEffect(sub_shadow)
        layout.addWidget(subtitle)

        center_row = QHBoxLayout()
        center_row.setAlignment(Qt.AlignCenter)
        center_row.setSpacing(20)

        left_banner_container = QVBoxLayout()
        left_banner_container.setAlignment(Qt.AlignCenter)
        left_banner_container.setSpacing(6)
        self._banner_left_label = QLabel()
        self._banner_left_label.setFixedSize(self._banner_w, self._banner_h)
        self._banner_left_label.setAlignment(Qt.AlignCenter)
        self._banner_left_label.setStyleSheet("""
            QLabel {
                background: rgba(135, 206, 235, 50);
                border: none; border-radius: 10px;
            }
        """)
        self._banner_left_link = QLabel()
        self._banner_left_link.setAlignment(Qt.AlignCenter)
        self._banner_left_link.setOpenExternalLinks(True)
        self._banner_left_link.setStyleSheet("font-size: 12px; color: white; background: transparent; padding: 2px;")
        left_banner_container.addWidget(self._banner_left_link, 0, Qt.AlignCenter)
        left_banner_container.addWidget(self._banner_left_label, 0, Qt.AlignCenter)
        center_row.addLayout(left_banner_container)

        btn_row = QHBoxLayout()
        btn_row.setAlignment(Qt.AlignCenter)
        btn_row.setSpacing(40)

        btn_id = PlusButton("", "ترتيب وطباعة بطاقات الهوية", self.open_id_editor)
        btn_id.setIcon(QIcon(_img_path('i2.jpeg')))
        btn_id.setIconSize(btn_id.size())
        _make_card("بطاقات الهوية", btn_id, btn_row)

        btn_photo = PlusButton("", "قص وطباعة صور شخصية", self.open_photo_editor)
        btn_photo.setIcon(QIcon(_img_path('i3.jpeg')))
        btn_photo.setIconSize(btn_photo.size())
        _make_card("صور شخصية", btn_photo, btn_row)

        btn_future2 = PlusButton("", "تحرير ملفات PDF", self.open_pdf_editor)
        btn_future2.setIcon(QIcon(_img_path('i4.jpeg')))
        btn_future2.setIconSize(btn_future2.size())
        _make_card("تحرير PDF", btn_future2, btn_row)

        btn_text = PlusButton("", "كتابة وتحرير النصوص", self.open_text_editor)
        btn_text.setIcon(QIcon(_img_path('i8.jpeg')))
        btn_text.setIconSize(btn_text.size())
        _make_card("محرر النصوص", btn_text, btn_row)

        btn_scanner = PlusButton("", "مسح ضوئي واستيراد صور", self.open_scanner)
        btn_scanner.setIcon(QIcon(_img_path('i9.jpeg')))
        btn_scanner.setIconSize(btn_scanner.size())
        _make_card("سكنر", btn_scanner, btn_row)

        center_row.addLayout(btn_row)

        right_banner_container = QVBoxLayout()
        right_banner_container.setAlignment(Qt.AlignCenter)
        right_banner_container.setSpacing(6)
        self._banner_right_label = QLabel()
        self._banner_right_label.setFixedSize(self._banner_w, self._banner_h)
        self._banner_right_label.setAlignment(Qt.AlignCenter)
        self._banner_right_label.setStyleSheet("""
            QLabel {
                background: rgba(135, 206, 235, 50);
                border: none; border-radius: 10px;
            }
        """)
        self._banner_right_link = QLabel()
        self._banner_right_link.setAlignment(Qt.AlignCenter)
        self._banner_right_link.setOpenExternalLinks(True)
        self._banner_right_link.setStyleSheet("font-size: 12px; color: white; background: transparent; padding: 2px;")
        right_banner_container.addWidget(self._banner_right_link, 0, Qt.AlignCenter)
        right_banner_container.addWidget(self._banner_right_label, 0, Qt.AlignCenter)
        center_row.addLayout(right_banner_container)

        layout.addLayout(center_row)

        auth_row = QHBoxLayout()
        auth_row.setAlignment(Qt.AlignCenter)
        auth_row.setSpacing(20)

        btn_login = QPushButton("تسجيل دخول")
        btn_login.setStyleSheet("""
            QPushButton {
                background: #1a73e8; color: white; font-size: 14px;
                padding: 10px 30px; border-radius: 8px; border: none;
                font-weight: bold;
            }
            QPushButton:hover { background: #1557b0; }
        """)
        btn_login.clicked.connect(self._open_login)
        auth_row.addWidget(btn_login)

        btn_register = QPushButton("تسجيل جديد")
        btn_register.setStyleSheet("""
            QPushButton {
                background: #34a853; color: white; font-size: 14px;
                padding: 10px 30px; border-radius: 8px; border: none;
                font-weight: bold;
            }
            QPushButton:hover { background: #2d8f47; }
        """)
        btn_register.clicked.connect(self._open_register)
        auth_row.addWidget(btn_register)

        self._btn_refresh = QPushButton("🔄 تحديث")
        self._btn_refresh.setStyleSheet("""
            QPushButton {
                background: #6c757d; color: white; font-size: 14px;
                padding: 10px 20px; border-radius: 8px; border: none;
                font-weight: bold;
            }
            QPushButton:hover { background: #5a6268; }
        """)
        self._btn_refresh.clicked.connect(self._refresh_user_data)
        auth_row.addWidget(self._btn_refresh)

        auth_margin = QVBoxLayout()
        auth_margin.setAlignment(Qt.AlignCenter)
        auth_margin.addSpacing(30)
        auth_margin.addLayout(auth_row)

        self._dashboard_main_btn = QPushButton("📊 لوحة التحكم")
        self._dashboard_main_btn.setStyleSheet("""
            QPushButton {
                background: #e67e22; color: white; font-size: 14px;
                padding: 8px 30px; border-radius: 8px; border: none;
                font-weight: bold; max-width: 200px;
            }
            QPushButton:hover { background: #d35400; }
        """)
        self._dashboard_main_btn.clicked.connect(self._open_dashboard)
        self._dashboard_main_btn.hide()
        auth_margin.addWidget(self._dashboard_main_btn, 0, Qt.AlignCenter)

        layout.addLayout(auth_margin)

        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(20, 10, 20, 20)

        box = QWidget()
        box.setStyleSheet("""
            QWidget {
                background-color: rgba(135, 206, 235, 180);
                border-radius: 14px;
            }
        """)
        img_dir = os.path.join(sys._MEIPASS if getattr(sys, 'frozen', False) else os.path.dirname(os.path.dirname(__file__)), 'img')
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(20, 16, 20, 16)
        box_layout.setSpacing(10)
        header_label = QLabel("للتواصل معنا للاستفسار")
        header_label.setStyleSheet("font-size: 15px; font-weight: bold; color: #fff; background: transparent;")
        header_label.setAlignment(Qt.AlignRight)
        box_layout.addWidget(header_label)

        def _make_contact_row(text, img_file):
            row_w = QWidget()
            row_w.setStyleSheet("background: transparent;")
            rl = QHBoxLayout(row_w)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(10)
            txt = QLabel(text)
            txt.setStyleSheet("font-size: 13px; color: #fff; background: transparent;")
            rl.addWidget(txt)
            path = os.path.join(img_dir, img_file)
            if os.path.exists(path):
                px = QPixmap(path).scaled(24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                icon = QLabel()
                icon.setPixmap(px)
                icon.setFixedSize(24, 24)
                icon.setStyleSheet("background: transparent;")
                rl.addWidget(icon)
            return row_w

        box_layout.addWidget(_make_contact_row("1wrsha", "i6.png"))
        box_layout.addWidget(_make_contact_row("07865402819", "i7.png"))

        bottom_row.addStretch()
        bottom_row.addWidget(box)
        bottom_row.addStretch()
        layout.addLayout(bottom_row)

        return widget, welcome_label, profile_label, btn_login, btn_register

    def _build_login_page(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignCenter)

        back_btn = QPushButton("← رجوع")
        back_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #1a73e8; font-size: 14px;
                border: none; font-weight: bold; padding: 5px;
            }
            QPushButton:hover { color: #1557b0; }
        """)
        back_btn.clicked.connect(self._switch_to_main)
        top = QHBoxLayout()
        top.addWidget(back_btn)
        top.addStretch()
        layout.addLayout(top)

        header = QLabel("تسجيل دخول")
        header.setAlignment(Qt.AlignCenter)
        header.setStyleSheet("font-size: 28px; font-weight: bold; color: #1a73e8; margin-bottom: 20px;")
        layout.addWidget(header)

        eng_user = QLineEdit()
        eng_user.setPlaceholderText("اسم بالانكليزي")
        eng_user.setStyleSheet("""
            QLineEdit {
                font-size: 16px; padding: 10px; border: 2px solid #ddd;
                border-radius: 8px; max-width: 300px; min-width: 250px;
            }
        """)
        eng_user.setAlignment(Qt.AlignCenter)
        layout.addWidget(eng_user, 0, Qt.AlignCenter)

        pw_container, password = _make_password_field("الرقم السري", "16px", "10px")
        layout.addWidget(pw_container, 0, Qt.AlignCenter)

        submit = QPushButton("تسجيل دخول")
        submit.setStyleSheet("""
            QPushButton {
                background: #1a73e8; color: white; font-size: 16px;
                padding: 10px 40px; border-radius: 8px; border: none;
                font-weight: bold; max-width: 200px;
            }
            QPushButton:hover { background: #1557b0; }
        """)
        submit.clicked.connect(lambda: self._login_submit(eng_user, password))
        layout.addWidget(submit, 0, Qt.AlignCenter)

        self._login_spinner = QProgressBar()
        self._login_spinner.setRange(0, 0)
        self._login_spinner.setFixedSize(200, 20)
        self._login_spinner.setTextVisible(False)
        self._login_spinner.hide()
        layout.addWidget(self._login_spinner, 0, Qt.AlignCenter)

        switch_btn = QPushButton("ليس لديك حساب؟ سجل الآن")
        switch_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #1a73e8; font-size: 13px;
                border: none; font-weight: bold; padding: 10px;
            }
            QPushButton:hover { color: #1557b0; text-decoration: underline; }
        """)
        switch_btn.clicked.connect(lambda: self._clear_and_switch([eng_user, password], self._open_register))
        layout.addWidget(switch_btn, 0, Qt.AlignCenter)

        widget.setObjectName("loginPage")
        widget.setStyleSheet("#loginPage { background: #cceeff; }")
        return widget

    def _build_register_page(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignCenter)

        back_btn = QPushButton("← رجوع")
        back_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #34a853; font-size: 14px;
                border: none; font-weight: bold; padding: 5px;
            }
            QPushButton:hover { color: #2d8f47; }
        """)
        back_btn.clicked.connect(self._switch_to_main)
        top = QHBoxLayout()
        top.addWidget(back_btn)
        top.addStretch()
        layout.addLayout(top)

        header = QLabel("تسجيل جديد")
        header.setAlignment(Qt.AlignCenter)
        header.setStyleSheet("font-size: 28px; font-weight: bold; color: #34a853; margin-bottom: 20px;")
        layout.addWidget(header)

        fields = {}
        field_configs = [
            ("shop_name", "اسم المكتبة", False),
            ("english_name", "اسم بالانكليزي", False),
            ("password", "الرقم السري", True),
            ("phone", "رقم الهاتف", False),
        ]
        for key, placeholder, is_pass in field_configs:
            if is_pass:
                pw_container, line = _make_password_field(placeholder)
                layout.addWidget(pw_container, 0, Qt.AlignCenter)
            else:
                line = QLineEdit()
                line.setPlaceholderText(placeholder)
                line.setStyleSheet("""
                    QLineEdit {
                        font-size: 14px; padding: 8px; border: 2px solid #ddd;
                        border-radius: 8px; max-width: 300px; min-width: 250px;
                    }
                """)
                line.setAlignment(Qt.AlignCenter)
                layout.addWidget(line, 0, Qt.AlignCenter)
            fields[key] = line

        from datetime import date
        reg_date = QLabel(f"تاريخ التسجيل: {date.today().strftime('%Y-%m-%d')}")
        reg_date.setAlignment(Qt.AlignCenter)
        reg_date.setStyleSheet("font-size: 13px; color: #666; margin: 5px;")
        layout.addWidget(reg_date)

        submit = QPushButton("تسجيل")
        submit.setStyleSheet("""
            QPushButton {
                background: #34a853; color: white; font-size: 16px;
                padding: 10px 40px; border-radius: 8px; border: none;
                font-weight: bold; max-width: 200px;
            }
            QPushButton:hover { background: #2d8f47; }
        """)
        submit.clicked.connect(lambda: self._register_submit(fields))
        layout.addWidget(submit, 0, Qt.AlignCenter)

        self._register_spinner = QProgressBar()
        self._register_spinner.setRange(0, 0)
        self._register_spinner.setFixedSize(200, 20)
        self._register_spinner.setTextVisible(False)
        self._register_spinner.hide()
        layout.addWidget(self._register_spinner, 0, Qt.AlignCenter)

        switch_btn = QPushButton("لديك حساب؟ سجل دخول")
        switch_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #34a853; font-size: 13px;
                border: none; font-weight: bold; padding: 10px;
            }
            QPushButton:hover { color: #2d8f47; text-decoration: underline; }
        """)
        switch_btn.clicked.connect(lambda: self._clear_and_switch(list(fields.values()), self._open_login))
        layout.addWidget(switch_btn, 0, Qt.AlignCenter)

        widget.setObjectName("registerPage")
        widget.setStyleSheet("#registerPage { background: #cceeff; }")
        return widget

    def _session_path(self):
        from core.database import DATA_DIR
        return os.path.join(DATA_DIR, "session.json")

    def _save_session(self):
        import json
        sess_data = {
            "username": self._username,
            "display_name": self._display_name,
            "is_admin": self._is_admin,
            "subscription_required": self._subscription_required,
        }
        if self._use_api:
            from core import api_client
            token = api_client.get_token()
            if not token:
                return
            sess_data["token"] = token
            sess_data["token_id"] = api_client.get_token_id()
            sess_data["mode"] = "api"
        else:
            sess_data["mode"] = "local"
        with open(self._session_path(), "w", encoding="utf-8") as f:
            json.dump(sess_data, f)

    def _clear_session(self):
        p = self._session_path()
        if os.path.exists(p):
            os.remove(p)

    def _try_restore_session(self):
        p = self._session_path()
        if not os.path.exists(p):
            return False
        import json
        try:
            with open(p, encoding="utf-8") as f:
                sess = json.load(f)
        except Exception:
            self._clear_session()
            return False
        username = sess.get("username")
        if not username:
            self._clear_session()
            return False
        mode = sess.get("mode", "api")
        if mode == "local" or (not self._use_api):
            if username not in self._users:
                self._clear_session()
                return False
            self._logged_in = True
            self._username = username
            self._display_name = sess.get("display_name", username)
            self._is_admin = sess.get("is_admin", False)
            self._subscription_required = bool(sess.get("subscription_required", True))
            self._update_auth_ui()
            self._switch_to_main()
            self._show_subscription_warning()
            logger.info("استعادة جلسة محلية: %s", username)
            return True
        token = sess.get("token")
        if not token:
            self._clear_session()
            return False
        from core import api_client
        api_client.set_token(token)
        api_client.set_token_id(sess.get("token_id"))
        api_client.set_username(username)
        from core.database import api_check_auth
        qdata, qerr = api_check_auth()
        if qerr:
            is_network = any(k in (qerr or "") for k in [
                "لا يمكن الاتصال", "ConnectionError", "Timeout",
                "timed out", "RemoteDisconnected",
            ])
            if is_network:
                self._logged_in = True
                self._username = username
                self._display_name = sess.get("display_name", username)
                self._is_admin = sess.get("is_admin", False)
                self._subscription_required = bool(sess.get("subscription_required", True))
                self._api_data = {}
                self._update_auth_ui()
                self._switch_to_main()
                logger.info("استعادة جلسة سابقة (بدون تحقق من السيرفر): %s", username)
                QTimer.singleShot(3000, self._load_notifications)
                return True
            self._clear_session()
            return False
        self._logged_in = True
        self._username = username
        self._display_name = sess.get("display_name", username)
        self._is_admin = sess.get("is_admin", False)
        self._subscription_required = bool(qdata.get("subscription_required", True))
        self._api_data = qdata
        self._update_auth_ui()
        self._switch_to_main()
        self._update_banners()
        if not self._is_admin:
            pend = qdata.get("pending_messages", [])
            if pend:
                rem = qdata.get("remaining_days", 0)
                QMessageBox.information(self, "تم التجديد",
                    f"تم زيادة عدد أيام اشتراكك وأصبحت {rem} يوم")
                from core.database import api_clear_pending
                api_clear_pending()
        QTimer.singleShot(500, self._load_notifications)
        logger.info("استعادة جلسة سابقة: %s", username)
        return True

    def _on_session_expired(self):
        self._clear_session()
        self._logged_in = False
        self._username = ""
        self._is_admin = False
        self._update_auth_ui()
        self._switch_to_main()
        QMessageBox.warning(self, "تنبيه", "انتهت صلاحية الجلسة، يرجى تسجيل الدخول مرة أخرى")

    def _refresh_user_data(self):
        if not self._logged_in:
            return
        if not self._use_api:
            from core.database import load_users
            self._users = load_users()
            self._switch_to_main()
            self._load_notifications()
            logger.info("تحديث بيانات المستخدمين من قاعدة البيانات المحلية")
            return
        from core.database import api_check_auth
        qdata, qerr = api_check_auth()
        if qerr:
            QMessageBox.warning(self, "خطأ", qerr)
            return
        self._api_data = qdata
        self._display_name = qdata.get("shop_name", self._username)
        self._is_admin = qdata.get("is_admin", False)
        self._subscription_required = bool(qdata.get("subscription_required", True))
        self._update_auth_ui()
        self._switch_to_main()
        self._update_banners()
        if not self._is_admin:
            pend = qdata.get("pending_messages", [])
            if pend:
                rem = qdata.get("remaining_days", 0)
                QMessageBox.information(self, "تم التجديد",
                    f"تم زيادة عدد أيام اشتراكك وأصبحت {rem} يوم")
                from core.database import api_clear_pending
                api_clear_pending()
        self._save_session()
        self._load_notifications()
        logger.info("تحديث بيانات المستخدم: %s", self._username)

    def _auto_refresh_data(self):
        if not self._logged_in:
            return
        if self._use_api:
            from core.database import api_check_auth
            qdata, qerr = api_check_auth()
            if qerr:
                return
            if qdata:
                old_sub = self._subscription_required
                old_admin = self._is_admin
                self._api_data = qdata
                self._display_name = qdata.get("shop_name", self._username)
                self._is_admin = qdata.get("is_admin", False)
                self._subscription_required = bool(qdata.get("subscription_required", True))
                if self._subscription_required != old_sub or self._is_admin != old_admin:
                    self._update_auth_ui()
                    self._update_banners()
                    logger.info("تم تحديث تلقائي للبيانات: %s", self._username)
            if self._is_admin and self._stack.currentWidget() is self._dashboard_widget:
                self._refresh_dashboard()
        else:
            from core.database import load_users
            self._users = load_users()
            if self._is_admin and self._stack.currentWidget() is self._dashboard_widget:
                self._refresh_dashboard()
            logger.info("تم تحديث تلقائي للبيانات المحلية")

    def _login_submit(self, eng_user, password):
        username = eng_user.text().strip()
        pw = password.text().strip()
        password.clear()
        if not username or not pw:
            QMessageBox.warning(self, "تنبيه", "يرجى إدخال اسم المستخدم والرقم السري")
            return
        self._login_spinner.show()
        QApplication.processEvents()
        if self._use_api:
            from core.database import api_login, api_login_force_check, api_check_auth, api_clear_pending
            data, err, need_force = api_login_force_check(username, pw)
            if need_force:
                ret = QMessageBox.question(self, "تسجيل الدخول قسرياً",
                    "هذا الحساب مسجل على جهاز آخر\nهل تريد تسجيل الدخول قسرياً وطرد الجلسات القديمة؟",
                    QMessageBox.Yes | QMessageBox.No)
                if ret == QMessageBox.Yes:
                    data, err = api_login(username, pw, force_login=True)
                else:
                    self._login_spinner.hide()
                    return
            if err:
                self._login_spinner.hide()
                QMessageBox.warning(self, "خطأ", err)
                return
            self._logged_in = True
            self._username = data["username"]
            self._display_name = data.get("shop_name", username)
            self._is_admin = data.get("is_admin", False)
            self._api_data = data
            qdata, qerr = api_check_auth()
            if qdata:
                self._api_data = qdata
                self._subscription_required = bool(qdata.get("subscription_required", True))
            self._update_auth_ui()
            self._switch_to_main()
            self._update_banners()
            if not self._is_admin and qdata:
                pend = qdata.get("pending_messages", [])
                if pend:
                    rem = qdata.get("remaining_days", 0)
                    QMessageBox.information(self, "تم التجديد",
                        f"تم زيادة عدد أيام اشتراكك وأصبحت {rem} يوم")
                    api_clear_pending()
            self._save_session()
            QTimer.singleShot(500, self._load_notifications)
            logger.info("تسجيل دخول API: %s", username)
            return
        if username not in self._users:
            self._login_spinner.hide()
            QMessageBox.warning(self, "خطأ", "اسم المستخدم غير موجود")
            return
        user = self._users[username]
        if user["password"] != pw:
            self._login_spinner.hide()
            QMessageBox.warning(self, "خطأ", "الرقم السري غير صحيح")
            return
        self._login_spinner.hide()
        self._logged_in = True
        self._username = username
        self._display_name = user["shop_name"]
        self._is_admin = False
        self._update_auth_ui()
        self._switch_to_main()
        self._show_subscription_warning()
        self._check_pending_notifications()
        self._load_notifications()
        self._save_session()
        logger.info("تسجيل دخول ناجح: %s", username)

    def _register_submit(self, fields):
        data = {k: fields[k].text().strip() for k in fields}
        for k, v in data.items():
            if not v:
                field_names = {
                    "shop_name": "اسم المكتبة", "english_name": "اسم بالانكليزي",
                    "password": "الرقم السري",
                    "phone": "رقم الهاتف"
                }
                QMessageBox.warning(self, "تنبيه", f"يرجى إدخال {field_names[k]}")
                return
        import re
        pw = data["password"]
        if len(pw) < 8 or not re.search(r'[a-zA-Z]', pw) or not re.search(r'[0-9]', pw):
            QMessageBox.warning(self, "خطأ", "الرقم السري يجب أن لا يقل عن 8 أحرف ويحتوي على حروف وأرقام")
            return
        if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]*$', data["english_name"]):
            QMessageBox.warning(self, "خطأ", "اسم بالانكليزي يجب أن يبدأ بحرف ويحتوي على أحرف إنجليزية وأرقام فقط")
            return
        if data["english_name"] == "ahmed":
            QMessageBox.warning(self, "خطأ", "لا يمكن استخدام هذا الاسم")
            return
        phone = data["phone"]
        if not phone.isdigit() or len(phone) != 11:
            QMessageBox.warning(self, "خطأ", "رقم الهاتف يجب أن يكون 11 رقماً")
            return
        self._register_spinner.show()
        QApplication.processEvents()
        if self._use_api:
            from core.database import api_register
            rdata, err = api_register(data["english_name"], data["password"],
                                       data["shop_name"], phone)
            if err:
                self._register_spinner.hide()
                QMessageBox.warning(self, "خطأ", err)
                return
            for line in fields.values():
                line.clear()
            self._logged_in = True
            self._username = rdata["username"]
            self._display_name = data["shop_name"]
            self._is_admin = False
            from core.database import api_check_auth
            qdata, _ = api_check_auth()
            if qdata:
                self._api_data = qdata
                self._subscription_required = bool(qdata.get("subscription_required", True))
            self._update_auth_ui()
            self._switch_to_main()
            self._save_session()
            logger.info("تم تسجيل مستخدم API: %s", rdata["username"])
            return
        if data["english_name"] in self._users:
            self._register_spinner.hide()
            QMessageBox.warning(self, "خطأ", "اسم بالانكليزي موجود مسبقاً")
            return
        for u in self._users.values():
            if u.get("phone") == phone:
                self._register_spinner.hide()
                QMessageBox.warning(self, "خطأ", "رقم الهاتف مسجل مسبقاً لحساب آخر")
                return
        self._users[data["english_name"]] = {
            "password": data["password"],
            "shop_name": data["shop_name"],
            "phone": phone,
            "reg_date": date.today().strftime("%Y-%m-%d"),
            "subscription_days": 0,
            "is_admin": False,
            "profile_pixmap": None,
            "section_trials": {"id": 3, "photo": 3, "pdf": 3},
            "last_warn_date": "",
            "subscriptions": [],
        }
        self._save_user(data["english_name"])
        from core.database import mark_all_notifications_read
        mark_all_notifications_read(data["english_name"])
        for line in fields.values():
            line.clear()
        self._register_spinner.hide()
        self._logged_in = True
        self._username = data["english_name"]
        self._display_name = data["shop_name"]
        self._is_admin = False
        self._update_auth_ui()
        self._switch_to_main()
        logger.info("تم تسجيل مستخدم جديد: %s (%s)", data["english_name"], data["shop_name"])

    def _clear_and_switch(self, lines, callback):
        for line in lines:
            line.clear()
        callback()

    def _set_profile_image(self, size=48):
        if self._use_api:
            if self._api_data and self._api_data.get("profile_pixmap"):
                import base64
                pix_data = base64.b64decode(self._api_data["profile_pixmap"])
                pix = QPixmap()
                if pix.loadFromData(pix_data):
                    self._profile_label.setPixmap(pix.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
                    self._profile_label.show()
                    return
        elif self._username in self._users:
            pix_data = self._users[self._username].get("profile_pixmap")
            if pix_data:
                pix = QPixmap()
                if pix.loadFromData(pix_data):
                    self._profile_label.setPixmap(pix.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
                    self._profile_label.show()
                    return
        letter = self._display_name[0] if self._display_name else "?"
        avatar = _make_avatar_pixmap(letter, size=size)
        self._profile_label.setPixmap(avatar)

    def _update_auth_ui(self):
        self._welcome_label.setText(f"مرحباً، {self._display_name}")
        self._welcome_label.show()
        self._set_profile_image(48)
        self._profile_label.show()
        self._btn_login.hide()
        self._btn_register.hide()

    def _update_banners(self):
        if self._use_api:
            from core.database import api_get_banners
            banners, err = api_get_banners()
            if err or not banners:
                return
        else:
            banners = self._users.get("ahmed", {})
        for side, label, link_label in [
            ("left", self._banner_left_label, self._banner_left_link),
            ("right", self._banner_right_label, self._banner_right_link),
        ]:
            if self._use_api:
                info = banners.get(side, {})
                pix_data_b64 = info.get("pixmap")
                pix_data = None
                if pix_data_b64:
                    import base64
                    pix_data = base64.b64decode(pix_data_b64)
                link_url = info.get("link", "")
            else:
                pix_data = banners.get(f"banner_{side}_pixmap")
                link_url = banners.get(f"banner_{side}_link", "")
            if pix_data:
                pix = QPixmap()
                if pix.loadFromData(pix_data):
                    label.setPixmap(pix.scaled(self._banner_w - 10, self._banner_h - 10, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                label.clear()
            if link_url:
                link_label.setText(f'<a href="{link_url}" style="color: white; text-decoration: none;">{link_url}</a>')
                link_label.show()
            else:
                link_label.clear()
                link_label.hide()

    def _switch_to_main(self):
        self._stack.setCurrentIndex(0)
        self.setWindowTitle("ورشة طباعة")
        if self._is_admin:
            self._btn_login.hide()
            self._btn_register.hide()
            self._dashboard_main_btn.show()
            self._welcome_label.setText(f"مرحباً، {self._display_name}")
            self._welcome_label.show()
            self._set_profile_image(48)
            self._profile_label.show()
        elif self._logged_in:
            self._btn_login.hide()
            self._btn_register.hide()
            self._dashboard_main_btn.hide()
            self._welcome_label.show()
            self._profile_label.show()
        else:
            self._btn_login.show()
            self._btn_register.show()
            self._dashboard_main_btn.hide()
            self._welcome_label.hide()
            self._profile_label.hide()
        self._update_notif_badge()
        logger.info("العودة إلى الشاشة الرئيسية")

    def _compute_subscription_days(self, user=None, username=None):
        if self._use_api:
            if self._api_data:
                return self._api_data.get("remaining_days", 0)
            return 0
        from core.database import compute_subscription_days
        if username:
            return compute_subscription_days(username)
        if user is not None:
            for uname, u in self._users.items():
                if u is user:
                    return compute_subscription_days(uname)
        if self._username and self._username in self._users:
            return compute_subscription_days(self._username)
        return 0

    def _check_pending_notifications(self):
        if self._is_admin:
            return
        if self._use_api:
            if self._api_data:
                pend = self._api_data.get("pending_messages", [])
                if pend:
                    rem = self._api_data.get("remaining_days", 0)
                    QMessageBox.information(self, "تم التجديد",
                        f"تم زيادة عدد أيام اشتراكك وأصبحت {rem} يوم")
                    from core.database import api_clear_pending
                    api_clear_pending()
            return
        if self._username not in self._users:
            return
        user = self._users[self._username]
        pending = user.get("pending_subs", [])
        if not pending:
            return
        total_days = self._compute_subscription_days()
        QMessageBox.information(self, "تم التجديد",
            f"تم زيادة عدد أيام اشتراكك وأصبحت {total_days} يوم")
        user["pending_subs"] = []
        from core.database import clear_pending
        clear_pending(self._username)

    def _save_user(self, username):
        from core.database import save_user
        if username in self._users:
            save_user(username, self._users[username])

    def _delete_user(self, username):
        if self._use_api:
            from core.database import api_delete_user
            api_delete_user(username)
            return
        from core.database import delete_user
        delete_user(username)

    def _require_auth(self, target_callback):
        if not self._logged_in:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Information)
            msg.setWindowTitle("تنبيه")
            msg.setText("يجب تسجيل الدخول أولاً")
            btn = msg.addButton("تسجيل دخول", QMessageBox.ActionRole)
            msg.addButton(QMessageBox.Cancel)
            msg.exec()
            if msg.clickedButton() == btn:
                self._open_login()
            return
        target_callback()

    def _require_subscription(self):
        if self._is_admin:
            return True
        if not self._subscription_required:
            return True
        if self._use_api:
            if self._compute_subscription_days() <= 0:
                QMessageBox.warning(self, "تنبيه", _NO_SUB_MSG)
                logger.warning("محاولة طباعة/حفظ من مستخدم بدون اشتراك: %s", self._username)
                return False
            return True
        if self._username in self._users:
            user = self._users[self._username]
            if self._compute_subscription_days(user) <= 0:
                QMessageBox.warning(self, "تنبيه", _NO_SUB_MSG)
                logger.warning("محاولة طباعة/حفظ من مستخدم بدون اشتراك: %s", self._username)
                return False
        return True

    def _check_section_access(self):
        if self._is_admin:
            return True
        if not self._subscription_required:
            return True
        if self._use_api:
            remaining = self._compute_subscription_days()
            if remaining <= 0:
                QMessageBox.warning(self, "تنبيه", _NO_SUB_MSG)
                logger.warning("محاولة دخول قسم مع اشتراك منتهي: %s", self._username)
                return False
            return True
        if self._username in self._users:
            user = self._users[self._username]
            remaining = self._compute_subscription_days(user)
            if remaining <= 0:
                QMessageBox.warning(self, "تنبيه", _NO_SUB_MSG)
                logger.warning("محاولة دخول قسم مع اشتراك منتهي: %s", self._username)
                return False
        return True

    def _show_subscription_warning(self):
        if self._is_admin:
            return
        if not self._subscription_required:
            return
        if self._use_api:
            remaining = self._compute_subscription_days()
            if 0 < remaining <= 3:
                msgs = {1: "تبقى لانتهاء اشتراكك يوم واحد", 2: "تبقى لانتهاء اشتراكك يومان"}
                msg = msgs.get(remaining, f"تبقى لانتهاء اشتراكك {remaining} أيام")
                QMessageBox.information(self, "تنبيه", msg)
                logger.info("عرض تحذير اشتراك لـ %s: %s", self._username, msg)
            return
        if self._username in self._users:
            user = self._users[self._username]
            remaining = self._compute_subscription_days(user)
            today_str = date.today().strftime("%Y-%m-%d")
            if user.get("last_warn_date") == today_str:
                return
            if remaining > 3 or remaining <= 0:
                return
            if remaining == 1:
                msg = "تبقى لانتهاء اشتراكك يوم واحد"
            elif remaining == 2:
                msg = "تبقى لانتهاء اشتراكك يومان"
            else:
                msg = f"تبقى لانتهاء اشتراكك {remaining} أيام"
            QMessageBox.information(self, "تنبيه", msg)
            user["last_warn_date"] = today_str
            logger.info("عرض تحذير اشتراك لـ %s: %s", self._username, msg)

    def _check_trial(self, section):
        if self._is_admin:
            return True
        if self._use_api:
            return True
        if self._username in self._users:
            user = self._users[self._username]
            trials = user.setdefault("section_trials", {})
            remaining = trials.get(section, 3)
            if remaining <= 0:
                QMessageBox.warning(self, "تنبيه",
                    "انتهت مرات التجربة لهذا القسم. يرجى التواصل مع المالك.")
                logger.warning("انتهت تجارب القسم %s للمستخدم %s", section, self._username)
                return False
            trials[section] = remaining - 1
            logger.info("تجربة قسم %s للمستخدم %s: %d متبقية", section, self._username, remaining - 1)
        return True

    def open_id_editor(self):
        self._require_auth(lambda: self._check_section_access() and self._check_trial("id") and self._open_id_editor())

    def open_photo_editor(self):
        self._require_auth(lambda: self._check_section_access() and self._check_trial("photo") and self._open_photo_editor())

    def open_pdf_editor(self):
        QMessageBox.information(self, "تنبيه", "قسم تحرير PDF قيد الصيانة حاليًا. سيتم تفعيله قريبًا.")
        logger.info("محاولة فتح PDF editor - معطل للصيانة")

    def _open_id_editor(self):
        self._stack.setCurrentIndex(1)
        self.setWindowTitle("ورشة طباعة - بطاقات الهوية")
        logger.info("فتح محرر بطاقات الهوية")

    def _open_photo_editor(self):
        self._stack.setCurrentIndex(2)
        self.setWindowTitle("ورشة طباعة - الصور الشخصية")
        logger.info("فتح محرر الصور الشخصية")

    def _open_pdf_editor(self):
        self._stack.setCurrentIndex(3)
        self.setWindowTitle("ورشة طباعة - تحرير PDF")
        logger.info("فتح محرر PDF")

    def open_text_editor(self):
        self._require_auth(lambda: self._check_section_access() and self._check_trial("text") and self._open_text_editor())

    def _open_text_editor(self):
        QMessageBox.information(self, "تحت الصيانة", "محرر النصوص حالياً تحت الصيانة.\nسيتم تشغيله قريباً.")

    def open_file_arg(self, path):
        self._pending_file = path
        self.open_text_editor()

    def open_scanner(self):
        self._require_auth(lambda: self._check_section_access() and self._open_scanner())

    def _open_scanner(self):
        self._stack.setCurrentWidget(self._scanner_page)
        self.setWindowTitle("ورشة طباعة - ماسح ضوئي")
        logger.info("فتح السكنر")

    def _build_dashboard_page(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        top = QHBoxLayout()
        back_btn = QPushButton("← رجوع")
        back_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #e67e22; font-size: 14px;
                border: none; font-weight: bold; padding: 5px;
            }
            QPushButton:hover { color: #d35400; }
        """)
        back_btn.clicked.connect(self._dashboard_back)
        top.addWidget(back_btn)
        top.addStretch()
        home_btn = QPushButton("🏠 الرئيسية")
        home_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #e67e22; font-size: 14px;
                border: none; font-weight: bold; padding: 5px;
            }
            QPushButton:hover { color: #d35400; }
        """)
        home_btn.clicked.connect(self._switch_to_main)
        top.addWidget(home_btn)
        layout.addLayout(top)

        header = QLabel("الرئيسية")
        header.setAlignment(Qt.AlignCenter)
        header.setStyleSheet("font-size: 24px; font-weight: bold; color: #e67e22; margin-bottom: 10px;")
        layout.addWidget(header)

        stats_row = QHBoxLayout()
        stats_row.setSpacing(15)
        self._dash_stat_total = self._make_stat_card("إجمالي المستخدمين", "0", "#1a73e8")
        stats_row.addWidget(self._dash_stat_total)
        self._dash_stat_today = self._make_stat_card("نشطون اليوم", "0", "#27ae60")
        stats_row.addWidget(self._dash_stat_today)
        self._dash_stat_inactive = self._make_stat_card("غير نشطين +30 يوم", "0", "#f39c12")
        stats_row.addWidget(self._dash_stat_inactive)
        self._dash_stat_notlogged = self._make_stat_card("لم يسجل دخول اليوم", "0", "#95a5a6")
        stats_row.addWidget(self._dash_stat_notlogged)
        layout.addLayout(stats_row)

        self._dash_online_label = QLabel("المتصلون حالياً (0)")
        self._dash_online_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #333; margin-top: 10px;")
        layout.addWidget(self._dash_online_label)

        self._dash_online_table = QTableWidget()
        self._dash_online_table.setColumnCount(4)
        self._dash_online_table.setHorizontalHeaderLabels([
            "المستخدم", "اسم المتجر", "الجلسات النشطة", "آخر دخول"
        ])
        self._dash_online_table.horizontalHeader().setStretchLastSection(True)
        self._dash_online_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._dash_online_table.setAlternatingRowColors(True)
        self._dash_online_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._dash_online_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._dash_online_table.setStyleSheet("""
            QTableWidget {
                font-size: 13px; border: 1px solid #ddd; border-radius: 8px;
                alternate-background-color: #f9f9f9;
            }
            QHeaderView::section {
                background: #e67e22; color: white; font-weight: bold;
                padding: 6px; border: none;
            }
        """)
        layout.addWidget(self._dash_online_table)

        admin_notif_row = QHBoxLayout()
        admin_notif_row.setAlignment(Qt.AlignCenter)
        admin_notif_row.setSpacing(12)

        btn_settings = QPushButton("⚙️ إعدادات واتساب")
        btn_settings.setStyleSheet("""
            QPushButton {
                background: #8e44ad; color: white; font-size: 14px;
                padding: 8px 25px; border-radius: 6px; border: none;
                font-weight: bold;
            }
            QPushButton:hover { background: #7d3c98; }
        """)
        btn_settings.clicked.connect(self._open_notifier_settings)
        admin_notif_row.addWidget(btn_settings)

        self._sub_toggle_btn = QPushButton()
        self._sub_toggle_btn.setCheckable(True)
        self._sub_toggle_btn.clicked.connect(self._toggle_subscription_required)
        admin_notif_row.addWidget(self._sub_toggle_btn)
        self._update_sub_toggle_btn()

        btn_send_notif = QPushButton("📢 إرسال اشعار")
        btn_send_notif.setStyleSheet("""
            QPushButton {
                background: #27ae60; color: white; font-size: 14px;
                padding: 8px 25px; border-radius: 6px; border: none;
                font-weight: bold;
            }
            QPushButton:hover { background: #229954; }
        """)
        btn_send_notif.clicked.connect(self._open_send_notification_dialog)
        admin_notif_row.addWidget(btn_send_notif)

        self._btn_replies = QPushButton("💬 ردود الاشعارات")
        self._btn_replies.setStyleSheet("""
            QPushButton {
                background: #e67e22; color: white; font-size: 14px;
                padding: 8px 25px; border-radius: 6px; border: none;
                font-weight: bold;
            }
            QPushButton:hover { background: #d35400; }
        """)
        self._btn_replies.clicked.connect(self._open_notification_replies_page)
        admin_notif_row.addWidget(self._btn_replies)

        layout.addLayout(admin_notif_row)

        btn_user_stats = QPushButton("👥 إدارة المستخدمين")
        btn_user_stats.setStyleSheet("""
            QPushButton {
                background: #1a73e8; color: white; font-size: 14px;
                padding: 8px 25px; border-radius: 6px; border: none;
                font-weight: bold;
            }
            QPushButton:hover { background: #1557b0; }
        """)
        btn_user_stats.clicked.connect(self._open_user_stats_page)
        layout.addWidget(btn_user_stats, 0, Qt.AlignCenter)

        widget.setObjectName("dashboardPage")
        widget.setStyleSheet("#dashboardPage { background: #cceeff; }")
        return widget

    def _update_sub_toggle_btn(self):
        if not hasattr(self, "_sub_toggle_btn"):
            return
        if self._subscription_required:
            self._sub_toggle_btn.setText("🔒 إلزامية الاشتراك: مفعّلة")
            self._sub_toggle_btn.setChecked(True)
            self._sub_toggle_btn.setStyleSheet("""
                QPushButton {
                    background: #27ae60; color: white; font-size: 14px;
                    padding: 8px 25px; border-radius: 6px; border: none;
                    font-weight: bold;
                }
                QPushButton:hover { background: #229954; }
            """)
        else:
            self._sub_toggle_btn.setText("🔓 إلزامية الاشتراك: معطّلة")
            self._sub_toggle_btn.setChecked(False)
            self._sub_toggle_btn.setStyleSheet("""
                QPushButton {
                    background: #95a5a6; color: white; font-size: 14px;
                    padding: 8px 25px; border-radius: 6px; border: none;
                    font-weight: bold;
                }
                QPushButton:hover { background: #7f8c8d; }
            """)

    def _toggle_subscription_required(self):
        target = not self._subscription_required
        if self._use_api:
            from core.database import api_set_subscription_required
            data, err = api_set_subscription_required(target)
            if err:
                QMessageBox.critical(self, "خطأ", f"تعذر الحفظ: {err}")
                self._update_sub_toggle_btn()
                return
            logger.info("تم تحديث إلزامية الاشتراك (سيرفر): %s", target)
        else:
            from core.database import set_subscription_required
            set_subscription_required(target)
            logger.info("تم تحديث إلزامية الاشتراك (محلي): %s", target)
        self._subscription_required = target
        self._update_sub_toggle_btn()
        self._save_session()
        if not target:
            QMessageBox.information(self, "تم", "تم إلغاء إلزامية الاشتراك. جميع الأقسام متاحة الآن للجميع.")
        else:
            QMessageBox.information(self, "تم", "تم تفعيل إلزامية الاشتراك.")

    def _update_notif_badge(self):
        if not hasattr(self, "_notif_btn"):
            return
        if self._logged_in:
            self._notif_btn.show()
            if self._notif_unread > 0:
                self._notif_btn.setText(f"🔔 إشعارات ({self._notif_unread})")
            else:
                self._notif_btn.setText("🔔 إشعارات")
        else:
            self._notif_btn.hide()

    def _load_notifications(self):
        if not self._logged_in:
            return
        if self._use_api:
            from core.database import api_get_notifications
            data, err = api_get_notifications()
            if err or data is None:
                return
            self._notif_list = data.get("notifications", [])
            self._notif_unread = data.get("unread_count", 0)
        else:
            from core.database import get_notifications_for_user, get_unread_notifications_count
            self._notif_list = get_notifications_for_user(self._username)
            self._notif_unread = get_unread_notifications_count(self._username)
        self._update_notif_badge()
        if not self._is_admin:
            self._enqueue_new_notifications()

    def _enqueue_new_notifications(self):
        new = [n for n in self._notif_list
               if not n.get("is_read") and n.get("id") not in self._shown_notif_ids]
        if new:
            self._notif_queue.extend(new)
            if not getattr(self, "_notif_processing", False):
                self._notif_processing = True
                try:
                    self._process_notif_queue()
                finally:
                    self._notif_processing = False

    def _process_notif_queue(self):
        while self._notif_queue:
            notif = self._notif_queue.pop(0)
            self._show_notification_popup(notif)

    def _mark_notif_read(self, nid):
        for n in self._notif_list:
            if n.get("id") == nid and not n.get("is_read"):
                n["is_read"] = True
                if self._use_api:
                    from core.database import api_mark_notifications_read
                    api_mark_notifications_read(notification_id=nid)
                else:
                    from core.database import mark_notification_read
                    mark_notification_read(nid, self._username)
                break
        self._notif_unread = sum(1 for n in self._notif_list if not n.get("is_read"))
        self._update_notif_badge()

    def _mark_all_read(self):
        if self._use_api:
            from core.database import api_mark_notifications_read
            api_mark_notifications_read(mark_all=True)
        else:
            from core.database import mark_all_notifications_read
            mark_all_notifications_read(self._username)
        for n in self._notif_list:
            n["is_read"] = True
        self._notif_unread = 0
        self._update_notif_badge()
        self._refresh_notifications_list()

    def _on_notif_timer(self):
        if not self._logged_in or self._is_admin:
            return
        self._load_notifications()

    def _show_notification_popup(self, notif):
        nid = notif.get("id")
        ntype = notif.get("type", "plain")
        self._shown_notif_ids.add(nid)
        self._mark_notif_read(nid)

        dialog = QDialog(self)
        dialog.setWindowTitle("اشعار جديد")
        dialog.setMinimumWidth(440)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(12)

        title = QLabel("📢 اشعار جديد")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #e67e22;")
        layout.addWidget(title)

        text = QLabel(notif.get("text", ""))
        text.setWordWrap(True)
        text.setAlignment(Qt.AlignCenter)
        text.setStyleSheet("font-size: 15px; color: #333; padding: 8px;")
        layout.addWidget(text)

        if ntype == "link":
            link_label = notif.get("link_label") or "اضغط هنا"
            link_url = notif.get("link_url", "")
            link_btn = QPushButton(link_label)
            link_btn.setStyleSheet("""
                QPushButton {
                    background: #1a73e8; color: white; font-size: 15px;
                    padding: 8px 30px; border-radius: 8px; border: none;
                    font-weight: bold;
                }
                QPushButton:hover { background: #1557b0; }
            """)
            link_btn.clicked.connect(lambda: webbrowser.open(link_url or ""))
            layout.addWidget(link_btn, 0, Qt.AlignCenter)

        if ntype == "question":
            question = notif.get("question") or "أجب على السؤال التالي"
            q_label = QLabel(question)
            q_label.setWordWrap(True)
            q_label.setAlignment(Qt.AlignCenter)
            q_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #8e44ad; padding: 6px;")
            layout.addWidget(q_label)

            reply_edit = QLineEdit()
            reply_edit.setPlaceholderText("اكتب ردك هنا...")
            reply_edit.setStyleSheet("""
                QLineEdit {
                    font-size: 14px; padding: 8px; border: 2px solid #ddd;
                    border-radius: 8px;
                }
            """)
            layout.addWidget(reply_edit)

            send_btn = QPushButton("إرسال الرد")
            send_btn.setStyleSheet("""
                QPushButton {
                    background: #27ae60; color: white; font-size: 14px;
                    padding: 8px 30px; border-radius: 8px; border: none;
                    font-weight: bold;
                }
                QPushButton:hover { background: #229954; }
            """)
            send_btn.clicked.connect(lambda: self._submit_notification_reply(nid, dialog, reply_edit))
            layout.addWidget(send_btn, 0, Qt.AlignCenter)

        close_btn = QPushButton("إغلاق")
        close_btn.setStyleSheet("""
            QPushButton {
                background: #95a5a6; color: white; font-size: 13px;
                padding: 6px 25px; border-radius: 8px; border: none;
            }
            QPushButton:hover { background: #7f8c8d; }
        """)
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn, 0, Qt.AlignCenter)

        logger.info("عرض اشعار منبثق id=%s type=%s", nid, ntype)
        dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowStaysOnTopHint)
        dialog.raise_()
        dialog.activateWindow()
        dialog.exec()

    def _submit_notification_reply(self, nid, dialog, reply_edit):
        text = reply_edit.text().strip()
        if not text:
            QMessageBox.warning(dialog, "تنبيه", "اكتب ردك قبل الإرسال")
            return
        if self._use_api:
            from core.database import api_reply_notification
            _, err = api_reply_notification(nid, text)
            if err:
                QMessageBox.critical(dialog, "خطأ", err)
                return
        else:
            from core.database import add_notification_reply
            add_notification_reply(nid, self._username, text)
        QMessageBox.information(dialog, "تم", "تم إرسال ردك بنجاح")
        dialog.accept()

    def _build_notifications_page(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        top = QHBoxLayout()
        back_btn = QPushButton("← رجوع")
        back_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #e67e22; font-size: 14px;
                border: none; font-weight: bold; padding: 5px;
            }
            QPushButton:hover { color: #d35400; }
        """)
        back_btn.clicked.connect(self._notifications_back)
        top.addWidget(back_btn)
        top.addStretch()
        layout.addLayout(top)

        header = QLabel("الاشعارات")
        header.setAlignment(Qt.AlignCenter)
        header.setStyleSheet("font-size: 22px; font-weight: bold; color: #e67e22; margin-bottom: 10px;")
        layout.addWidget(header)

        self._notif_table = QTableWidget()
        self._notif_table.setColumnCount(5)
        self._notif_table.setHorizontalHeaderLabels(["النوع", "النص", "التاريخ", "الحالة", "إجراء"])
        self._notif_table.horizontalHeader().setStretchLastSection(True)
        self._notif_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._notif_table.setAlternatingRowColors(True)
        self._notif_table.setStyleSheet("""
            QTableWidget { font-size: 13px; border: 1px solid #ddd; border-radius: 8px; }
            QHeaderView::section { background: #e67e22; color: white; font-weight: bold; padding: 6px; border: none; }
        """)
        layout.addWidget(self._notif_table)

        mark_btn = QPushButton("✅ تحديد الكل كمقروء")
        mark_btn.setStyleSheet("""
            QPushButton {
                background: #1a73e8; color: white; font-size: 14px;
                padding: 8px 25px; border-radius: 6px; border: none;
                font-weight: bold;
            }
            QPushButton:hover { background: #1557b0; }
        """)
        mark_btn.clicked.connect(self._mark_all_read)
        layout.addWidget(mark_btn, 0, Qt.AlignCenter)

        widget.setObjectName("notifPage")
        widget.setStyleSheet("#notifPage { background: #cceeff; }")
        return widget

    def _refresh_notifications_list(self):
        self._notif_table.setRowCount(0)
        type_names = {"link": "🔗 رابط", "question": "❓ سؤال", "plain": "📄 نص"}
        for i, n in enumerate(self._notif_list):
            self._notif_table.insertRow(i)
            self._notif_table.setItem(i, 0, QTableWidgetItem(type_names.get(n.get("type", ""), "")))
            self._notif_table.setItem(i, 1, QTableWidgetItem(n.get("text", "")))
            created = n.get("created_at", "")
            if len(created) >= 16:
                created = created[:16].replace("T", " ")
            self._notif_table.setItem(i, 2, QTableWidgetItem(created))
            status_item = QTableWidgetItem("مقروء" if n.get("is_read") else "جديد")
            status_item.setForeground(QColor(Qt.gray) if n.get("is_read") else QColor("#e67e22"))
            self._notif_table.setItem(i, 3, status_item)
            action_btns = []
            if n.get("type") == "link":
                btn = QPushButton("فتح الرابط")
                btn.setStyleSheet("""
                    QPushButton {
                        background: #1a73e8; color: white; font-size: 12px;
                        padding: 4px 12px; border-radius: 6px; border: none;
                    }
                    QPushButton:hover { background: #1557b0; }
                """)
                btn.clicked.connect(lambda checked, url=n.get("link_url", ""): webbrowser.open(url or ""))
                action_btns.append(btn)
            elif n.get("type") == "question":
                btn = QPushButton("رد")
                btn.setStyleSheet("""
                    QPushButton {
                        background: #27ae60; color: white; font-size: 12px;
                        padding: 4px 16px; border-radius: 6px; border: none;
                    }
                    QPushButton:hover { background: #229954; }
                """)
                btn.clicked.connect(lambda checked, nid=n.get("id"): self._open_reply_dialog(nid))
                action_btns.append(btn)
            if self._is_admin:
                del_btn = QPushButton("🗑️ حذف")
                del_btn.setStyleSheet("""
                    QPushButton {
                        background: #e74c3c; color: white; font-size: 12px;
                        padding: 4px 12px; border-radius: 6px; border: none;
                    }
                    QPushButton:hover { background: #c0392b; }
                """)
                del_btn.clicked.connect(lambda checked, nid=n.get("id"): self._delete_notification(nid))
                action_btns.append(del_btn)
            if action_btns:
                container = QWidget()
                row_layout = QHBoxLayout(container)
                row_layout.setContentsMargins(2, 2, 2, 2)
                row_layout.setSpacing(6)
                row_layout.addStretch()
                for b in action_btns:
                    row_layout.addWidget(b)
                row_layout.addStretch()
                self._notif_table.setCellWidget(i, 4, container)

    def _delete_notification(self, nid):
        reply = QMessageBox.question(
            self, "حذف الإشعار",
            "سيتم حذف هذا الإشعار نهائياً ولن يظهر لأي مستخدم. هل أنت متأكد؟",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        if self._use_api:
            from core.database import api_delete_notification
            _, err = api_delete_notification(nid)
            if err:
                QMessageBox.critical(self, "خطأ", f"تعذر الحذف: {err}")
                return
        else:
            from core.database import delete_notification
            delete_notification(nid)
        self._shown_notif_ids.discard(nid)
        self._notif_list = [n for n in self._notif_list if n.get("id") != nid]
        self._load_notifications()
        self._refresh_notifications_list()
        logger.info("حذف الإشعار %s", nid)

    def _open_reply_dialog(self, nid):
        notif = next((n for n in self._notif_list if n.get("id") == nid), None)
        if not notif:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("الرد على الإشعار")
        dialog.setMinimumWidth(440)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(12)
        q = QLabel(notif.get("question") or notif.get("text", ""))
        q.setWordWrap(True)
        q.setAlignment(Qt.AlignCenter)
        q.setStyleSheet("font-size: 15px; font-weight: bold; color: #8e44ad;")
        layout.addWidget(q)
        reply_edit = QLineEdit()
        reply_edit.setPlaceholderText("اكتب ردك هنا...")
        reply_edit.setStyleSheet("""
            QLineEdit {
                font-size: 14px; padding: 8px; border: 2px solid #ddd;
                border-radius: 8px;
            }
        """)
        layout.addWidget(reply_edit)
        send_btn = QPushButton("إرسال الرد")
        send_btn.setStyleSheet("""
            QPushButton {
                background: #27ae60; color: white; font-size: 14px;
                padding: 8px 30px; border-radius: 8px; border: none;
                font-weight: bold;
            }
            QPushButton:hover { background: #229954; }
        """)
        send_btn.clicked.connect(lambda: self._submit_notification_reply(nid, dialog, reply_edit))
        layout.addWidget(send_btn, 0, Qt.AlignCenter)
        cancel_btn = QPushButton("إلغاء")
        cancel_btn.setStyleSheet("""
            QPushButton {
                background: #95a5a6; color: white; font-size: 13px;
                padding: 6px 25px; border-radius: 8px; border: none;
            }
            QPushButton:hover { background: #7f8c8d; }
        """)
        cancel_btn.clicked.connect(dialog.reject)
        layout.addWidget(cancel_btn, 0, Qt.AlignCenter)
        dialog.exec()

    def _open_notifications_page(self):
        self._prev_page_index = self._stack.currentIndex()
        self._stack.setCurrentWidget(self._notifications_widget)
        self.setWindowTitle("ورشة طباعة - الاشعارات")
        self._refresh_notifications_page_data()
        logger.info("فتح صفحة الاشعارات")

    def _refresh_notifications_page_data(self):
        self._load_notifications()
        self._refresh_notifications_list()
        if not self._is_admin:
            self._mark_all_read()

    def _notifications_back(self):
        self._stack.setCurrentIndex(self._prev_page_index)
        self.setWindowTitle("ورشة طباعة")
        logger.info("رجوع من صفحة الاشعارات")

    def _open_send_notification_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("إرسال اشعار")
        dialog.setMinimumWidth(480)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(12)

        title = QLabel("📢 إرسال اشعار جديد")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #e67e22;")
        layout.addWidget(title)

        type_combo = QComboBox()
        type_combo.addItem("📄 نص بدون رابط أو سؤال", "plain")
        type_combo.addItem("🔗 نص مع رابط مخفي", "link")
        type_combo.addItem("❓ نص مع سؤال ورد", "question")
        layout.addWidget(QLabel("نوع الإشعار:"))
        layout.addWidget(type_combo)

        text_edit = QPlainTextEdit()
        text_edit.setPlaceholderText("نص الإشعار...")
        text_edit.setMaximumHeight(90)
        layout.addWidget(QLabel("النص:"))
        layout.addWidget(text_edit)

        link_label_edit = QLineEdit()
        link_label_edit.setPlaceholderText("الكلمة الظاهرة التي يضغط عليها المستخدم (مثال: اضغط هنا)")
        link_url_edit = QLineEdit()
        link_url_edit.setPlaceholderText("الرابط الفعلي (لا يظهر للمستخدم)")
        link_box = QWidget()
        lk = QVBoxLayout(link_box)
        lk.setContentsMargins(0, 0, 0, 0)
        lk.setSpacing(6)
        lk.addWidget(QLabel("الكلمة الظاهرة:"))
        lk.addWidget(link_label_edit)
        lk.addWidget(QLabel("الرابط (مخفي):"))
        lk.addWidget(link_url_edit)
        layout.addWidget(link_box)

        question_edit = QLineEdit()
        question_edit.setPlaceholderText("السؤال الذي سيُعرض مع حقل الرد")
        q_box = QWidget()
        qb = QVBoxLayout(q_box)
        qb.setContentsMargins(0, 0, 0, 0)
        qb.setSpacing(6)
        qb.addWidget(QLabel("السؤال:"))
        qb.addWidget(question_edit)
        layout.addWidget(q_box)

        link_box.hide()
        q_box.hide()

        def _on_type(idx):
            ntype = type_combo.itemData(idx)
            link_box.setVisible(ntype == "link")
            q_box.setVisible(ntype == "question")
        type_combo.currentIndexChanged.connect(_on_type)

        send_btn = QPushButton("📨 إرسال")
        send_btn.setStyleSheet("""
            QPushButton {
                background: #27ae60; color: white; font-size: 14px;
                padding: 8px 35px; border-radius: 8px; border: none;
                font-weight: bold;
            }
            QPushButton:hover { background: #229954; }
        """)
        send_btn.clicked.connect(lambda: self._send_notification(
            dialog, type_combo, text_edit, link_label_edit, link_url_edit, question_edit))
        layout.addWidget(send_btn, 0, Qt.AlignCenter)

        cancel_btn = QPushButton("إلغاء")
        cancel_btn.setStyleSheet("""
            QPushButton {
                background: #95a5a6; color: white; font-size: 13px;
                padding: 6px 25px; border-radius: 8px; border: none;
            }
            QPushButton:hover { background: #7f8c8d; }
        """)
        cancel_btn.clicked.connect(dialog.reject)
        layout.addWidget(cancel_btn, 0, Qt.AlignCenter)

        dialog.exec()

    def _send_notification(self, dialog, type_combo, text_edit, link_label_edit,
                           link_url_edit, question_edit):
        ntype = type_combo.itemData(type_combo.currentIndex())
        text = text_edit.toPlainText().strip()
        link_label = link_label_edit.text().strip()
        link_url = link_url_edit.text().strip()
        question = question_edit.text().strip()
        if not text:
            QMessageBox.warning(dialog, "تنبيه", "نص الإشعار مطلوب")
            return
        if ntype == "link" and not link_url:
            QMessageBox.warning(dialog, "تنبيه", "أدخل الرابط الفعلي (مخفي)")
            return
        if ntype == "link" and not link_label:
            link_label = "اضغط هنا"
        if self._use_api:
            from core.database import api_create_notification
            _, err = api_create_notification(ntype, text, link_url, link_label, question)
            if err:
                QMessageBox.critical(dialog, "خطأ", err)
                return
        else:
            from core.database import create_notification
            create_notification(ntype, text, link_url, link_label, question)
        QMessageBox.information(dialog, "تم", "تم إرسال الإشعار لجميع المستخدمين")
        dialog.accept()
        logger.info("إرسال اشعار نوع=%s", ntype)

    def _build_notification_replies_page(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        top = QHBoxLayout()
        back_btn = QPushButton("← رجوع")
        back_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #e67e22; font-size: 14px;
                border: none; font-weight: bold; padding: 5px;
            }
            QPushButton:hover { color: #d35400; }
        """)
        back_btn.clicked.connect(self._replies_back)
        top.addWidget(back_btn)
        top.addStretch()
        layout.addLayout(top)

        header = QLabel("ردود الاشعارات")
        header.setAlignment(Qt.AlignCenter)
        header.setStyleSheet("font-size: 22px; font-weight: bold; color: #e67e22; margin-bottom: 10px;")
        layout.addWidget(header)

        self._replies_table = QTableWidget()
        self._replies_table.setColumnCount(6)
        self._replies_table.setHorizontalHeaderLabels(["المستخدم", "اسم المكتبة", "الإشعار", "الرد", "التاريخ", "إجراء"])
        self._replies_table.horizontalHeader().setStretchLastSection(True)
        self._replies_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._replies_table.setAlternatingRowColors(True)
        self._replies_table.setStyleSheet("""
            QTableWidget { font-size: 13px; border: 1px solid #ddd; border-radius: 8px; }
            QHeaderView::section { background: #e67e22; color: white; font-weight: bold; padding: 6px; border: none; }
        """)
        self._replies_table.cellClicked.connect(self._on_replies_cell_clicked)
        layout.addWidget(self._replies_table)

        refresh_btn = QPushButton("🔄 تحديث")
        refresh_btn.setStyleSheet("""
            QPushButton {
                background: #e67e22; color: white; font-size: 14px;
                padding: 8px 25px; border-radius: 6px; border: none;
                font-weight: bold;
            }
            QPushButton:hover { background: #d35400; }
        """)
        refresh_btn.clicked.connect(self._refresh_notification_replies)
        layout.addWidget(refresh_btn, 0, Qt.AlignCenter)

        widget.setObjectName("repliesPage")
        widget.setStyleSheet("#repliesPage { background: #cceeff; }")
        return widget

    def _refresh_notification_replies(self):
        if self._use_api:
            from core.database import api_get_notification_replies
            data, err = api_get_notification_replies()
            if err:
                QMessageBox.warning(self, "خطأ", err)
                return
            replies = data if isinstance(data, list) else []
        else:
            from core.database import get_notification_replies
            replies = get_notification_replies()
        self._replies_table.setRowCount(0)
        for i, r in enumerate(replies):
            self._replies_table.insertRow(i)
            user_item = QTableWidgetItem(r.get("username", ""))
            user_item.setForeground(QColor("#1a73e8"))
            user_item.setToolTip("اضغط لعرض بيانات المستخدم")
            self._replies_table.setItem(i, 0, user_item)
            self._replies_table.setItem(i, 1, QTableWidgetItem(r.get("shop_name", "")))
            self._replies_table.setItem(i, 2, QTableWidgetItem(r.get("notification_text", "")))
            self._replies_table.setItem(i, 3, QTableWidgetItem(r.get("reply_text", "")))
            replied = r.get("replied_at", "")
            if len(replied) >= 16:
                replied = replied[:16].replace("T", " ")
            self._replies_table.setItem(i, 4, QTableWidgetItem(replied))
            del_btn = QPushButton("🗑")
            del_btn.setToolTip("حذف الرد")
            del_btn.setFixedSize(28, 28)
            del_btn.setStyleSheet("QPushButton { background: #e74c3c; color: white; border-radius: 14px; font-size: 12px; } QPushButton:hover { background: #c0392b; }")
            del_btn.clicked.connect(lambda checked, rid=r.get("id"): self._delete_notification_reply(rid))
            self._replies_table.setCellWidget(i, 5, del_btn)
        logger.info("تحديث صفحة ردود الاشعارات: %d رد", self._replies_table.rowCount())

    def _on_replies_cell_clicked(self, row, col):
        if col == 0:
            item = self._replies_table.item(row, col)
            if item:
                self._show_user_details_dialog(item.text())
        elif col == 3:
            item = self._replies_table.item(row, col)
            if item and item.text():
                reply_text = item.text()
                shop = self._replies_table.item(row, 1).text() if self._replies_table.item(row, 1) else ""
                notif_text = self._replies_table.item(row, 2).text() if self._replies_table.item(row, 2) else ""
                dialog = QDialog(self)
                dialog.setWindowTitle("تفاصيل الرد")
                dialog.setMinimumWidth(420)
                dialog_layout = QVBoxLayout(dialog)
                dialog_layout.setSpacing(12)
                title = QLabel("💬 رد المستخدم")
                title.setAlignment(Qt.AlignCenter)
                title.setStyleSheet("font-size: 18px; font-weight: bold; color: #27ae60;")
                dialog_layout.addWidget(title)
                if shop:
                    shop_lbl = QLabel(f"المكتبة: {shop}")
                    shop_lbl.setStyleSheet("font-size: 13px; color: #666;")
                    shop_lbl.setAlignment(Qt.AlignCenter)
                    dialog_layout.addWidget(shop_lbl)
                notif_frame = QFrame()
                notif_frame.setStyleSheet("QFrame { background: #f8f9fa; border: 1px solid #ddd; border-radius: 8px; padding: 8px; }")
                nf_layout = QVBoxLayout(notif_frame)
                nf_layout.setContentsMargins(8, 8, 8, 8)
                nf_layout.addWidget(QLabel("الإشعار:"))
                notif_lbl = QLabel(notif_text)
                notif_lbl.setWordWrap(True)
                notif_lbl.setStyleSheet("font-size: 13px; color: #555;")
                nf_layout.addWidget(notif_lbl)
                dialog_layout.addWidget(notif_frame)
                reply_frame = QFrame()
                reply_frame.setStyleSheet("QFrame { background: #e8f5e9; border: 1px solid #a5d6a7; border-radius: 8px; padding: 8px; }")
                rf_layout = QVBoxLayout(reply_frame)
                rf_layout.setContentsMargins(8, 8, 8, 8)
                rf_layout.addWidget(QLabel("الرد:"))
                reply_lbl = QLabel(reply_text)
                reply_lbl.setWordWrap(True)
                reply_lbl.setStyleSheet("font-size: 14px; font-weight: bold; color: #2e7d32;")
                rf_layout.addWidget(reply_lbl)
                dialog_layout.addWidget(reply_frame)
                close_btn = QPushButton("إغلاق")
                close_btn.setStyleSheet("""
                    QPushButton {
                        background: #95a5a6; color: white; font-size: 13px;
                        padding: 6px 25px; border-radius: 8px; border: none;
                    }
                    QPushButton:hover { background: #7f8c8d; }
                """)
                close_btn.clicked.connect(dialog.accept)
                dialog_layout.addWidget(close_btn, 0, Qt.AlignCenter)
                dialog.exec()

    def _delete_notification_reply(self, rid):
        confirm = QMessageBox.question(self, "حذف الرد",
            "هل أنت متأكد من حذف هذا الرد؟",
            QMessageBox.Yes | QMessageBox.No)
        if confirm != QMessageBox.Yes:
            return
        if self._use_api:
            from core.database import api_delete_notification_reply
            _, err = api_delete_notification_reply(rid)
            if err:
                QMessageBox.warning(self, "خطأ", err)
                return
        else:
            from core.database import delete_notification_reply
            delete_notification_reply(rid)
        self._refresh_notification_replies()
        logger.info("حذف رد الإشعار %s", rid)

    def _open_notification_replies_page(self):
        self._prev_page_index = self._stack.currentIndex()
        self._refresh_notification_replies()
        self._stack.setCurrentWidget(self._replies_widget)
        self.setWindowTitle("ورشة طباعة - ردود الاشعارات")
        logger.info("فتح صفحة ردود الاشعارات")

    def _replies_back(self):
        self._stack.setCurrentIndex(self._prev_page_index)
        self.setWindowTitle("ورشة طباعة - لوحة تحكم المالك")
        logger.info("رجوع من صفحة ردود الاشعارات")

    def _build_user_stats_page(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        top = QHBoxLayout()
        back_btn = QPushButton("← رجوع")
        back_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #e67e22; font-size: 14px;
                border: none; font-weight: bold; padding: 5px;
            }
            QPushButton:hover { color: #d35400; }
        """)
        back_btn.clicked.connect(self._user_stats_back)
        top.addWidget(back_btn)
        top.addStretch()
        layout.addLayout(top)

        header = QLabel("المستخدمين")
        header.setAlignment(Qt.AlignCenter)
        header.setStyleSheet("font-size: 22px; font-weight: bold; color: #e67e22; margin-bottom: 10px;")
        layout.addWidget(header)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)

        self._us_search = QLineEdit()
        self._us_search.setPlaceholderText("🔍 بحث بالاسم أو المتجر أو الهاتف...")
        self._us_search.setStyleSheet("""
            QLineEdit {
                font-size: 13px; padding: 8px; border: 2px solid #ddd;
                border-radius: 6px; min-width: 250px;
            }
        """)
        self._us_search.textChanged.connect(self._refresh_user_stats)
        filter_row.addWidget(self._us_search)

        self._us_status_filter = QComboBox()
        self._us_status_filter.addItems(["الكل", "متصل", "سجل اليوم", "غير متصل", "غير نشط 20+ يوم", "لم يسجل دخول"])
        self._us_status_filter.setStyleSheet("""
            QComboBox {
                font-size: 13px; padding: 8px; border: 2px solid #ddd;
                border-radius: 6px; min-width: 150px;
            }
        """)
        self._us_status_filter.currentIndexChanged.connect(self._refresh_user_stats)
        filter_row.addWidget(self._us_status_filter)

        filter_row.addStretch()
        layout.addLayout(filter_row)

        self._user_stats_table = QTableWidget()
        self._user_stats_table.setColumnCount(9)
        self._user_stats_table.setHorizontalHeaderLabels([
            "المستخدم", "المتجر", "الهاتف", "تاريخ الانضمام",
            "الحالة", "الجلسات", "الأجهزة", "المتبقي", "إجراءات"
        ])
        self._user_stats_table.horizontalHeader().setStretchLastSection(True)
        self._user_stats_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._user_stats_table.setAlternatingRowColors(True)
        self._user_stats_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._user_stats_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._user_stats_table.setStyleSheet("""
            QTableWidget {
                font-size: 13px; border: 1px solid #ddd; border-radius: 8px;
                alternate-background-color: #f9f9f9;
            }
            QHeaderView::section {
                background: #e67e22; color: white; font-weight: bold;
                padding: 6px; border: none;
            }
        """)
        self._user_stats_table.cellClicked.connect(self._on_user_stats_cell_clicked)
        layout.addWidget(self._user_stats_table)

        widget.setObjectName("userStatsPage")
        widget.setStyleSheet("#userStatsPage { background: #cceeff; }")
        return widget

    def _make_stat_card(self, title, value, color):
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background: white; border-radius: 12px;
                border: 2px solid {color}; padding: 15px;
            }}
        """)
        frame.setMinimumHeight(80)
        v = QVBoxLayout(frame)
        v.setAlignment(Qt.AlignCenter)
        lbl_title = QLabel(title)
        lbl_title.setAlignment(Qt.AlignCenter)
        lbl_title.setStyleSheet(f"font-size: 12px; color: {color}; font-weight: bold; border: none; background: transparent;")
        v.addWidget(lbl_title)
        lbl_value = QLabel(value)
        lbl_value.setAlignment(Qt.AlignCenter)
        lbl_value.setStyleSheet(f"font-size: 28px; font-weight: bold; color: {color}; border: none; background: transparent;")
        lbl_value.setObjectName("statValue")
        v.addWidget(lbl_value)
        frame.setProperty("statValueLabel", lbl_value)
        return frame

    def _update_stat_card(self, card, value):
        lbl = card.property("statValueLabel")
        if lbl:
            lbl.setText(str(value))

    def _open_user_stats_page(self):
        self._prev_page_index = self._stack.currentIndex()
        self._stack.setCurrentWidget(self._user_stats_widget)
        self.setWindowTitle("ورشة طباعة - المستخدمين")
        self._refresh_user_stats()
        logger.info("فتح صفحة المستخدمين")

    def _user_stats_back(self):
        self._stack.setCurrentIndex(self._prev_page_index)
        self.setWindowTitle("ورشة طباعة - الرئيسية")
        logger.info("رجوع من صفحة المستخدمين")

    @staticmethod
    def _get_user_status(u):
        today = date.today().isoformat()
        if u.get("active_sessions", 0) > 0:
            return "online"
        last_login = u.get("last_login", "") or ""
        if last_login.startswith(today):
            return "today"
        if not last_login:
            return "never"
        try:
            days_since = (date.today() - date.fromisoformat(last_login[:10])).days
        except Exception:
            days_since = 0
        if days_since > 20:
            return "inactive"
        return "offline"

    def _refresh_user_stats(self):
        self._user_stats_table.setRowCount(0)
        if self._use_api:
            from core.database import api_get_admin_all_users
            users, err = api_get_admin_all_users()
            if err:
                users = []
        else:
            users = []
            for uname, udata in self._users.items():
                if uname == "ahmed":
                    continue
                remaining = self._compute_subscription_days(username=uname)
                users.append({
                    "username": uname,
                    "shop_name": udata.get("shop_name", ""),
                    "phone": udata.get("phone", ""),
                    "reg_date": udata.get("reg_date", ""),
                    "last_login": "",
                    "active_sessions": 0,
                    "max_devices": 1,
                    "remaining_days": remaining,
                    "status": "active" if remaining > 0 else "inactive",
                })

        search = self._us_search.text().strip().lower() if hasattr(self, "_us_search") else ""
        status_idx = self._us_status_filter.currentIndex() if hasattr(self, "_us_status_filter") else 0
        status_map = {0: "all", 1: "online", 2: "today", 3: "offline", 4: "inactive", 5: "never"}
        status_filter = status_map.get(status_idx, "all")

        status_labels = {
            "online": "متصل", "today": "سجل اليوم", "offline": "غير متصل",
            "inactive": "غير نشط 20+ يوم", "never": "لم يسجل دخول",
        }
        status_colors = {
            "online": "#27ae60", "today": "#1a73e8", "offline": "#f39c12",
            "inactive": "#e74c3c", "never": "#95a5a6",
        }

        filtered = []
        for u in users:
            match_search = (not search or search in (u.get("username", "")).lower()
                            or search in (u.get("shop_name", "") or "").lower()
                            or search in (u.get("phone", "") or "").lower())
            if not match_search:
                continue
            user_status = self._get_user_status(u)
            if status_filter != "all" and user_status != status_filter:
                continue
            filtered.append((u, user_status))

        for i, (u, user_status) in enumerate(filtered):
            self._user_stats_table.insertRow(i)
            self._user_stats_table.setItem(i, 0, QTableWidgetItem(u.get("username", "")))
            self._user_stats_table.setItem(i, 1, QTableWidgetItem(u.get("shop_name", "")))
            self._user_stats_table.setItem(i, 2, QTableWidgetItem(u.get("phone", "")))
            reg_date = u.get("reg_date", "")
            self._user_stats_table.setItem(i, 3, QTableWidgetItem(reg_date if reg_date else "—"))
            status_item = QTableWidgetItem(status_labels.get(user_status, user_status))
            status_item.setForeground(QColor(status_colors.get(user_status, "#333")))
            self._user_stats_table.setItem(i, 4, status_item)
            active = u.get("active_sessions", 0)
            sessions_item = QTableWidgetItem(str(active))
            if active > 0:
                sessions_item.setForeground(QColor("#27ae60"))
            self._user_stats_table.setItem(i, 5, sessions_item)
            max_dev = u.get("max_devices", 1)
            self._user_stats_table.setItem(i, 6, QTableWidgetItem(str(max_dev)))
            remaining = u.get("remaining_days", 0)
            if remaining > 0:
                rem_item = QTableWidgetItem(f"{remaining} يوم")
                rem_item.setForeground(QColor("#27ae60"))
            else:
                rem_item = QTableWidgetItem("منتهي")
                rem_item.setForeground(QColor("#e74c3c"))
            self._user_stats_table.setItem(i, 7, rem_item)

            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(2, 2, 2, 2)
            actions_layout.setSpacing(4)

            btn_details = QPushButton("تفاصيل")
            btn_details.setStyleSheet("QPushButton { background: #3498db; color: white; border-radius: 4px; font-size: 11px; padding: 3px 8px; border: none; } QPushButton:hover { background: #2980b9; }")
            btn_details.clicked.connect(lambda checked, uname=u.get("username", ""): self._show_user_stats_detail_dialog(uname))
            actions_layout.addWidget(btn_details)

            btn_delete = QPushButton("حذف")
            btn_delete.setStyleSheet("QPushButton { background: #e74c3c; color: white; border-radius: 4px; font-size: 11px; padding: 3px 8px; border: none; } QPushButton:hover { background: #c0392b; }")
            btn_delete.clicked.connect(lambda checked, uname=u.get("username", ""): self._dashboard_delete_user(uname))
            actions_layout.addWidget(btn_delete)

            self._user_stats_table.setCellWidget(i, 8, actions_widget)

        logger.info("تحديث صفحة المستخدمين: %d مستخدم", self._user_stats_table.rowCount())

    def _on_user_stats_cell_clicked(self, row, col):
        if col == 8:
            return
        item = self._user_stats_table.item(row, 0)
        if item:
            username = item.text()
            self._show_user_stats_detail_dialog(username)

    def _show_user_stats_detail_dialog(self, username):
        if self._use_api:
            from core.database import api_get_user_details
            data, err = api_get_user_details(username)
            if err:
                QMessageBox.warning(self, "خطأ", err)
                return
        else:
            udata = self._users.get(username)
            if not udata:
                QMessageBox.warning(self, "خطأ", "المستخدم غير موجود")
                return
            data = {
                "username": username,
                "shop_name": udata.get("shop_name", username),
                "phone": udata.get("phone", ""),
                "reg_date": udata.get("reg_date", ""),
                "remaining_days": udata.get("subscription_days", 0),
                "subscriptions": [],
                "active_sessions": 0,
                "max_devices": 1,
                "last_login": "",
                "is_admin": False,
            }
        dialog = QDialog(self)
        dialog.setWindowTitle(f"بيانات المستخدم - {data.get('shop_name', username)}")
        dialog.setMinimumWidth(460)
        dl = QVBoxLayout(dialog)
        dl.setSpacing(10)

        title = QLabel(f"👤 بيانات المستخدم")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #1a73e8;")
        dl.addWidget(title)

        fields = [
            ("اسم المستخدم:", data.get("username", "")),
            ("اسم المكتبة:", data.get("shop_name", "")),
            ("رقم الهاتف:", data.get("phone", "")),
            ("تاريخ التسجيل:", data.get("reg_date", "")),
            ("آخر دخول:", data.get("last_login", "")[:16].replace("T", " ") if data.get("last_login") else "—"),
            ("الأيام المتبقية:", str(data.get("remaining_days", 0))),
            ("الأجهزة النشطة:", f"{data.get('active_sessions', 0)} / {data.get('max_devices', 1)}"),
        ]
        for label, value in fields:
            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setStyleSheet("font-size: 13px; font-weight: bold; color: #555; min-width: 120px;")
            val = QLabel(value)
            val.setStyleSheet("font-size: 13px; color: #333;")
            row.addWidget(lbl)
            row.addWidget(val)
            row.addStretch()
            dl.addLayout(row)

        subs = data.get("subscriptions", [])
        if subs:
            sub_title = QLabel("📋 سجل الاشتراكات:")
            sub_title.setStyleSheet("font-size: 14px; font-weight: bold; color: #e67e22; margin-top: 10px;")
            dl.addWidget(sub_title)
            for s in subs:
                sub_line = QLabel(f"  • من {s.get('start_date', s.get('start', ''))} إلى {s.get('end_date', s.get('end', ''))} ({s.get('days', 0)} يوم)")
                sub_line.setStyleSheet("font-size: 12px; color: #555;")
                dl.addWidget(sub_line)
        else:
            dl.addWidget(QLabel("لا توجد اشتراكات مسجلة"))

        close_btn = QPushButton("إغلاق")
        close_btn.setStyleSheet("""
            QPushButton {
                background: #95a5a6; color: white; font-size: 13px;
                padding: 6px 25px; border-radius: 8px; border: none;
            }
            QPushButton:hover { background: #7f8c8d; }
        """)
        close_btn.clicked.connect(dialog.accept)
        dl.addWidget(close_btn, 0, Qt.AlignCenter)

        dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowStaysOnTopHint)
        dialog.raise_()
        dialog.activateWindow()
        dialog.exec()

    def _show_user_details_dialog(self, username):
        if self._use_api:
            from core.database import api_get_user_details
            data, err = api_get_user_details(username)
            if err:
                QMessageBox.warning(self, "خطأ", err)
                return
        else:
            user = self._users.get(username)
            if not user:
                QMessageBox.warning(self, "خطأ", "المستخدم غير موجود")
                return
            data = {
                "username": username,
                "shop_name": user.get("shop_name", username),
                "phone": user.get("phone", ""),
                "reg_date": user.get("reg_date", ""),
                "remaining_days": self._compute_subscription_days(username=username),
                "subscriptions": user.get("subscriptions", []),
                "active_sessions": 0,
                "max_devices": 1,
                "is_admin": bool(user.get("is_admin")),
            }
        lines = [
            f"اسم المستخدم: {data.get('username')}",
            f"اسم المكتبة: {data.get('shop_name')}",
            f"رقم الهاتف: {data.get('phone')}",
            f"تاريخ التسجيل: {data.get('reg_date')}",
            f"الأيام المتبقية من الاشتراك: {data.get('remaining_days')}",
            f"الأجهزة النشطة: {data.get('active_sessions')} / {data.get('max_devices')}",
        ]
        subs = data.get("subscriptions", [])
        if subs:
            lines.append("")
            lines.append("سجل الاشتراكات:")
            for s in subs:
                lines.append(f"  - من {s.get('start')} إلى {s.get('end')} ({s.get('days')} يوم)")
        else:
            lines.append("")
            lines.append("لا توجد اشتراكات مسجلة")
        dialog = QDialog(self)
        dialog.setWindowTitle(f"بيانات المستخدم {username}")
        dialog.setMinimumWidth(460)
        layout = QVBoxLayout(dialog)
        label = QLabel("\n".join(lines))
        label.setAlignment(Qt.AlignRight)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setStyleSheet("font-size: 14px; color: #333; line-height: 1.6;")
        layout.addWidget(label)
        close_btn = QPushButton("إغلاق")
        close_btn.setStyleSheet("""
            QPushButton {
                background: #e67e22; color: white; font-size: 14px;
                padding: 8px 30px; border-radius: 8px; border: none;
            }
            QPushButton:hover { background: #d35400; }
        """)
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn, 0, Qt.AlignCenter)
        dialog.exec()

    def _refresh_dashboard(self, search_text="", filter_date=None):
        self._dash_online_table.setRowCount(0)
        if self._use_api:
            from core.database import api_get_admin_all_users
            users_data, err = api_get_admin_all_users()
            if err:
                users_data = []
            users = users_data or []
        else:
            users = []
            for uname, udata in self._users.items():
                if uname == "ahmed":
                    continue
                remaining = self._compute_subscription_days(username=uname)
                users.append({
                    "username": uname,
                    "shop_name": udata.get("shop_name", ""),
                    "phone": udata.get("phone", ""),
                    "reg_date": udata.get("reg_date", ""),
                    "last_login": "",
                    "active_sessions": 0,
                    "max_devices": 1,
                    "remaining_days": remaining,
                    "status": "active" if remaining > 0 else "inactive",
                })

        today = date.today().isoformat()
        today_active = sum(1 for u in users if (u.get("last_login", "").startswith(today) or u.get("active_sessions", 0) > 0))
        inactive_30d = sum(1 for u in users if u.get("status") == "inactive")
        not_logged_today = sum(1 for u in users if not (u.get("last_login", "").startswith(today)) and u.get("active_sessions", 0) == 0)

        self._update_stat_card(self._dash_stat_total, len(users))
        self._update_stat_card(self._dash_stat_today, today_active)
        self._update_stat_card(self._dash_stat_inactive, inactive_30d)
        self._update_stat_card(self._dash_stat_notlogged, not_logged_today)

        online_users = [u for u in users if u.get("active_sessions", 0) > 0]
        self._dash_online_label.setText(f"المتصلون حالياً ({len(online_users)})")

        for i, u in enumerate(online_users):
            self._dash_online_table.insertRow(i)
            self._dash_online_table.setItem(i, 0, QTableWidgetItem(u.get("username", "")))
            self._dash_online_table.setItem(i, 1, QTableWidgetItem(u.get("shop_name", "")))
            sessions_item = QTableWidgetItem(str(u.get("active_sessions", 0)))
            sessions_item.setForeground(QColor("#27ae60"))
            self._dash_online_table.setItem(i, 2, sessions_item)
            last_login = u.get("last_login", "")
            if len(last_login) >= 16:
                last_login = last_login[:16].replace("T", " ")
            self._dash_online_table.setItem(i, 3, QTableWidgetItem(last_login if last_login else "—"))
        logger.info("تحديث لوحة التحكم: %d مستخدم، %d متصل", len(users), len(online_users))

    def _open_dashboard(self):
        self._prev_page_index = self._stack.currentIndex()
        self._refresh_dashboard()
        self._stack.setCurrentWidget(self._dashboard_widget)
        self.setWindowTitle("ورشة طباعة - الرئيسية")
        logger.info("فتح لوحة تحكم المالك، الصفحة السابقة: %d", self._prev_page_index)

    def _dashboard_back(self):
        target = self._prev_page_index
        self._stack.setCurrentIndex(target)
        titles = {0: "ورشة طباعة", 1: "ورشة طباعة - بطاقات الهوية",
                  2: "ورشة طباعة - الصور الشخصية", 3: "ورشة طباعة - تحرير PDF",
                  4: "ورشة طباعة - تسجيل دخول", 5: "ورشة طباعة - تسجيل جديد",
                  7: "ورشة طباعة - الملف الشخصي"}
        self.setWindowTitle(titles.get(target, "ورشة طباعة"))
        logger.info("العودة من لوحة التحكم إلى الصفحة: %d", target)

    def _dashboard_add_days(self, eng_name):
        shop_name = eng_name
        if not self._use_api and eng_name in self._users:
            shop_name = self._users[eng_name].get('shop_name', eng_name)
        days, ok = QInputDialog.getText(self, "تعيين أيام الاشتراك",
            f"أدخل عدد أيام الاشتراك للمستخدم {shop_name} (0 = إلغاء الاشتراك):")
        if ok and days.strip().isdigit():
            d = int(days.strip())
            if d < 0:
                QMessageBox.warning(self, "تنبيه", "يرجى إدخال رقم صحيح")
                return
            if self._use_api:
                from core.database import api_set_subscription
                rdata, err = api_set_subscription(eng_name, d)
                if err:
                    QMessageBox.warning(self, "خطأ", err)
                    return
                QMessageBox.information(self, "تم", f"تم تعيين {d} أيام للمستخدم {shop_name}")
                self._refresh_dashboard()
                logger.info("تم تعيين %d أيام للمستخدم %s (API)", d, eng_name)
                return
            user = self._users[eng_name]
            today = date.today()
            user.setdefault("subscriptions", [])
            user["subscriptions"].clear()
            if d > 0:
                start = today.strftime("%Y-%m-%d")
                end = (today + timedelta(days=d)).strftime("%Y-%m-%d")
                from core.database import add_subscription, add_pending_message
                add_subscription(eng_name, start, end, d)
                add_pending_message(eng_name, f"تم تعيين اشتراكك {d} يوم")
                user["subscriptions"].append({"start": start, "end": end, "days": d})
            user["subscription_days"] = self._compute_subscription_days(user)
            QMessageBox.information(self, "تم", f"تم تعيين {d} أيام للمستخدم {user.get('shop_name', eng_name)}")
            self._refresh_dashboard()
            logger.info("تم تعيين %d أيام للمستخدم %s", d, eng_name)

    def _dashboard_set_max_devices(self, eng_name):
        if not self._use_api:
            return
        shop_name = eng_name
        from core.database import api_get_user_sessions
        sdata, serr = api_get_user_sessions(eng_name)
        if serr:
            QMessageBox.warning(self, "خطأ", serr)
            return
        current_max = sdata.get("max_devices", 1)
        active = sdata.get("active_sessions", 0)
        choice, ok = QInputDialog.getItem(self, "الحد الأقصى للأجهزة",
            f"المستخدم {shop_name}\nالأجهزة النشطة حالياً: {active}\nاختر الحد الأقصى:",
            ["1", "2"], current=int(current_max == 2), editable=False)
        if ok and choice:
            new_max = int(choice)
            if new_max == current_max:
                return
            from core.database import api_set_max_devices
            rdata, rerr = api_set_max_devices(eng_name, new_max)
            if rerr:
                QMessageBox.warning(self, "خطأ", rerr)
                return
            QMessageBox.information(self, "تم",
                f"تم تعيين الحد الأقصى للأجهزة إلى {new_max} للمستخدم {shop_name}")
            self._refresh_dashboard()
            logger.info("تم تعيين max_devices=%d للمستخدم %s", new_max, eng_name)

    def _dashboard_reset_password(self, eng_name):
        shop_name = eng_name
        if not self._use_api and eng_name in self._users:
            shop_name = self._users[eng_name].get('shop_name', eng_name)
        pw, ok = QInputDialog.getText(self, "استعادة الرقم السري",
            f"أدخل الرقم السري الجديد للمستخدم {shop_name} (8 أحرف وأرقام على الأقل):",
            text="")
        if ok and pw.strip():
            import re
            if len(pw.strip()) < 8 or not re.search(r'[a-zA-Z]', pw) or not re.search(r'[0-9]', pw):
                QMessageBox.warning(self, "خطأ", "الرقم السري يجب أن لا يقل عن 8 أحرف ويحتوي على حروف وأرقام")
                return
            if self._use_api:
                from core.database import api_reset_password
                rdata, err = api_reset_password(eng_name, pw.strip())
                if err:
                    QMessageBox.warning(self, "خطأ", err)
                    return
                QMessageBox.information(self, "تم", f"تم تغيير الرقم السري للمستخدم {shop_name}")
                logger.info("تم تغيير الرقم السري عبر لوحة التحكم لـ %s (API)", eng_name)
                return
            self._users[eng_name]["password"] = pw.strip()
            self._save_user(eng_name)
            QMessageBox.information(self, "تم", f"تم تغيير الرقم السري للمستخدم {self._users[eng_name].get('shop_name', eng_name)}")
            logger.info("تم تغيير الرقم السري عبر لوحة التحكم لـ %s", eng_name)

    def _dashboard_delete_user(self, eng_name):
        shop_name = eng_name
        if not self._use_api and eng_name in self._users:
            shop_name = self._users[eng_name].get('shop_name', eng_name)
        confirm = QMessageBox.question(self, "حذف الحساب",
            f"هل أنت متأكد من حذف حساب {shop_name}؟",
            QMessageBox.Yes | QMessageBox.No)
        if confirm != QMessageBox.Yes:
            return
        self._delete_user(eng_name)
        if not self._use_api:
            self._users.pop(eng_name, None)
        self._refresh_dashboard()
        logger.info("تم حذف المستخدم %s", eng_name)

    def _show_subscription_history_dialog(self, username):
        today = date.today()
        if self._use_api:
            from core.database import api_get_subscriptions
            data, err = api_get_subscriptions(username)
            if err:
                QMessageBox.warning(self, "خطأ", err)
                return
            subs = data.get("subscriptions", [])
            shop_name = username
        else:
            if username not in self._users:
                return
            user = self._users[username]
            subs = user.get("subscriptions", [])
            shop_name = user.get('shop_name', username)
        dialog = QDialog(self)
        dialog.setWindowTitle(f"اشتراكات {shop_name}")
        dialog.setMinimumSize(450, 300)
        layout = QVBoxLayout(dialog)
        title = QLabel(f"سجل الاشتراكات - {shop_name}")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #e67e22; margin-bottom: 10px;")
        layout.addWidget(title)
        if not subs:
            no_data = QLabel("لا توجد اشتراكات مسجلة")
            no_data.setAlignment(Qt.AlignCenter)
            no_data.setStyleSheet("font-size: 14px; color: #999;")
            layout.addWidget(no_data)
        else:
            table = QTableWidget()
            table.setColumnCount(4)
            table.setHorizontalHeaderLabels(["من تاريخ", "إلى تاريخ", "عدد الأيام", "الحالة"])
            table.horizontalHeader().setStretchLastSection(True)
            table.setRowCount(len(subs))
            table.setStyleSheet("""
                QTableWidget { font-size: 13px; }
                QHeaderView::section { background: #e67e22; color: white; font-weight: bold; padding: 4px; }
            """)
            for i, sub in enumerate(subs):
                table.setItem(i, 0, QTableWidgetItem(sub.get("start", "")))
                table.setItem(i, 1, QTableWidgetItem(sub.get("end", "")))
                table.setItem(i, 2, QTableWidgetItem(str(sub.get("days", 0))))
                try:
                    end = date.fromisoformat(sub["end"])
                    status = "ساري" if end > today else "منتهي"
                except Exception:
                    status = "---"
                status_item = QTableWidgetItem(status)
                status_item.setForeground(QColor(Qt.darkGreen) if status == "ساري" else QColor(Qt.red))
                table.setItem(i, 3, status_item)
            layout.addWidget(table)
        close_btn = QPushButton("إغلاق")
        close_btn.setStyleSheet("""
            QPushButton {
                background: #e67e22; color: white; font-size: 14px;
                padding: 8px 25px; border-radius: 6px; border: none;
            }
            QPushButton:hover { background: #d35400; }
        """)
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn, 0, Qt.AlignCenter)
        dialog.exec()

    def _build_profile_page(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignCenter)

        back_btn = QPushButton("← رجوع للرئيسية")
        back_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #1a73e8; font-size: 14px;
                border: none; font-weight: bold; padding: 5px;
            }
            QPushButton:hover { color: #1557b0; }
        """)
        back_btn.clicked.connect(self._switch_to_main)
        top = QHBoxLayout()
        top.addWidget(back_btn)
        top.addStretch()
        layout.addLayout(top)

        main_horizontal = QHBoxLayout()
        main_horizontal.setSpacing(30)
        main_horizontal.setContentsMargins(40, 10, 40, 10)

        # Left panel: action buttons
        left_panel = QVBoxLayout()
        left_panel.setAlignment(Qt.AlignCenter)
        left_panel.setSpacing(12)

        save_btn = QPushButton("💾 حفظ التغييرات")
        save_btn.setMinimumWidth(180)
        save_btn.setStyleSheet("""
            QPushButton {
                background: #1a73e8; color: white; font-size: 14px;
                padding: 10px 25px; border-radius: 8px; border: none;
                font-weight: bold;
            }
            QPushButton:hover { background: #1557b0; }
        """)
        save_btn.clicked.connect(self._save_profile)
        left_panel.addWidget(save_btn, 0, Qt.AlignCenter)

        change_pw_btn = QPushButton("🔑 تغيير الرقم السري")
        change_pw_btn.setMinimumWidth(180)
        change_pw_btn.setStyleSheet("""
            QPushButton {
                background: #e67e22; color: white; font-size: 14px;
                padding: 10px 25px; border-radius: 8px; border: none;
                font-weight: bold;
            }
            QPushButton:hover { background: #d35400; }
        """)
        change_pw_btn.clicked.connect(self._change_password)
        left_panel.addWidget(change_pw_btn, 0, Qt.AlignCenter)

        subs_btn = QPushButton("📋 اشتراكاتي")
        subs_btn.setMinimumWidth(180)
        subs_btn.setStyleSheet("""
            QPushButton {
                background: #8e44ad; color: white; font-size: 14px;
                padding: 10px 25px; border-radius: 8px; border: none;
                font-weight: bold;
            }
            QPushButton:hover { background: #7d3c98; }
        """)
        subs_btn.clicked.connect(self._open_my_subscriptions)
        left_panel.addWidget(subs_btn, 0, Qt.AlignCenter)

        self._banner_section = QWidget()
        self._banner_section.setStyleSheet("background: transparent;")
        bs_layout = QVBoxLayout(self._banner_section)
        bs_layout.setAlignment(Qt.AlignCenter)
        bs_layout.setSpacing(8)
        banner_header = QLabel("إدارة صور المستطيلات")
        banner_header.setAlignment(Qt.AlignCenter)
        banner_header.setStyleSheet("font-size: 14px; font-weight: bold; color: #8e44ad;")
        bs_layout.addWidget(banner_header)
        banner_btn_row = QHBoxLayout()
        banner_btn_row.setAlignment(Qt.AlignCenter)
        banner_btn_row.setSpacing(10)
        for side, label in [("الأيسر", "left"), ("الأيمن", "right")]:
            add_btn = QPushButton(f"🖼 إضافة {side}")
            add_btn.setStyleSheet("""
                QPushButton {
                    background: #8e44ad; color: white; font-size: 12px;
                    padding: 6px 16px; border-radius: 6px; border: none;
                }
                QPushButton:hover { background: #7d3c98; }
            """)
            add_btn.clicked.connect(lambda checked, s=label: self._set_banner_image(s))
            del_btn = QPushButton(f"🗑 حذف {side}")
            del_btn.setStyleSheet("""
                QPushButton {
                    background: #e74c3c; color: white; font-size: 12px;
                    padding: 6px 16px; border-radius: 6px; border: none;
                }
                QPushButton:hover { background: #c0392b; }
            """)
            del_btn.clicked.connect(lambda checked, s=label: self._delete_banner_image(s))
            group = QHBoxLayout()
            group.setSpacing(6)
            group.addWidget(add_btn)
            group.addWidget(del_btn)
            banner_btn_row.addLayout(group)
        bs_layout.addLayout(banner_btn_row)
        self._banner_section.hide()
        left_panel.addWidget(self._banner_section, 0, Qt.AlignCenter)

        self._dashboard_btn = QPushButton("📊 لوحة التحكم")
        self._dashboard_btn.setMinimumWidth(180)
        self._dashboard_btn.setStyleSheet("""
            QPushButton {
                background: #e67e22; color: white; font-size: 14px;
                padding: 8px 30px; border-radius: 8px; border: none;
                font-weight: bold;
            }
            QPushButton:hover { background: #d35400; }
        """)
        self._dashboard_btn.clicked.connect(self._open_dashboard)
        self._dashboard_btn.hide()
        left_panel.addWidget(self._dashboard_btn, 0, Qt.AlignCenter)

        logout_btn = QPushButton("تسجيل خروج")
        logout_btn.setMinimumWidth(180)
        logout_btn.setStyleSheet("""
            QPushButton {
                background: #e74c3c; color: white; font-size: 14px;
                padding: 8px 30px; border-radius: 8px; border: none;
                font-weight: bold;
            }
            QPushButton:hover { background: #c0392b; }
        """)
        logout_btn.clicked.connect(self._logout)
        left_panel.addWidget(logout_btn, 0, Qt.AlignCenter)

        left_panel.addStretch()

        left_container = QWidget()
        left_container.setLayout(left_panel)
        left_container.setStyleSheet("background: transparent; border-radius: 12px; padding: 10px;")
        main_horizontal.addWidget(left_container)

        # Center: profile picture
        center_panel = QVBoxLayout()
        center_panel.setAlignment(Qt.AlignCenter)
        center_panel.setSpacing(8)

        self._profile_pic_label = ClickableLabel()
        self._profile_pic_label.setFixedSize(120, 120)
        self._profile_pic_label.setAlignment(Qt.AlignCenter)
        self._profile_pic_label.setStyleSheet("""
            QLabel {
                background: #e0e0e0; border-radius: 60px;
                border: 3px solid #1a73e8;
            }
            QLabel:hover { border-color: #1557b0; }
        """)
        self._profile_pic_label.clicked.connect(self._pick_profile_picture)
        center_panel.addWidget(self._profile_pic_label, 0, Qt.AlignCenter)

        hint = QLabel("اضغط لتغيير الصورة")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("font-size: 11px; color: #999;")
        center_panel.addWidget(hint, 0, Qt.AlignCenter)

        main_horizontal.addLayout(center_panel)

        # Right panel: data fields
        right_panel = QVBoxLayout()
        right_panel.setAlignment(Qt.AlignCenter)
        right_panel.setSpacing(10)

        header = QLabel("الملف الشخصي")
        header.setAlignment(Qt.AlignCenter)
        header.setStyleSheet("font-size: 22px; font-weight: bold; color: #1a73e8; margin-bottom: 5px;")
        right_panel.addWidget(header)

        self._profile_shop = QLineEdit()
        self._profile_shop.setPlaceholderText("اسم المكتبة")
        self._profile_shop.setStyleSheet("font-size: 14px; padding: 8px; border: 2px solid #ddd; border-radius: 8px; min-width: 250px; max-width: 300px; color: #000;")
        self._profile_shop.setAlignment(Qt.AlignCenter)
        right_panel.addWidget(self._profile_shop, 0, Qt.AlignCenter)

        self._profile_eng = QLineEdit()
        self._profile_eng.setPlaceholderText("اسم بالانكليزي")
        self._profile_eng.setStyleSheet("font-size: 14px; padding: 8px; border: 2px solid #ddd; border-radius: 8px; min-width: 250px; max-width: 300px; color: #000;")
        self._profile_eng.setAlignment(Qt.AlignCenter)
        right_panel.addWidget(self._profile_eng, 0, Qt.AlignCenter)

        self._profile_phone = QLineEdit()
        self._profile_phone.setPlaceholderText("رقم الهاتف")
        self._profile_phone.setStyleSheet("font-size: 14px; padding: 8px; border: 2px solid #ddd; border-radius: 8px; min-width: 250px; max-width: 300px; color: #000;")
        self._profile_phone.setAlignment(Qt.AlignCenter)
        right_panel.addWidget(self._profile_phone, 0, Qt.AlignCenter)

        self._profile_date = QLabel()
        self._profile_date.setAlignment(Qt.AlignCenter)
        self._profile_date.setStyleSheet("font-size: 13px; color: #000; min-width: 250px; max-width: 300px;")
        right_panel.addWidget(self._profile_date, 0, Qt.AlignCenter)

        self._profile_sub = ClickableLabel()
        self._profile_sub.setAlignment(Qt.AlignCenter)
        self._profile_sub.setStyleSheet("font-size: 14px; font-weight: bold; color: #000; min-width: 250px; max-width: 300px;")
        self._profile_sub.clicked.connect(self._open_subscription_page)
        right_panel.addWidget(self._profile_sub, 0, Qt.AlignCenter)

        right_panel.addStretch()

        right_container = QWidget()
        right_container.setLayout(right_panel)
        right_container.setStyleSheet("background: transparent; border-radius: 12px; padding: 10px;")
        main_horizontal.addWidget(right_container)

        layout.addLayout(main_horizontal)

        widget.setObjectName("profilePage")
        widget.setStyleSheet("#profilePage { background: #cceeff; }")
        return widget

    def _update_profile_page(self):
        if self._use_api:
            if self._api_data is None:
                return
            self._profile_shop.setText(self._api_data.get("shop_name", ""))
            self._profile_eng.setText(self._username)
            self._profile_phone.setText(self._api_data.get("phone", ""))
            self._profile_date.setText(f"تاريخ التسجيل: {self._api_data.get('reg_date', '')}")
            if self._is_admin:
                self._dashboard_btn.show()
                self._banner_section.show()
                self._profile_sub.hide()
                for w in (self._profile_eng, self._profile_phone, self._profile_shop):
                    w.show()
            else:
                self._dashboard_btn.hide()
                self._banner_section.hide()
                self._profile_sub.show()
                remaining = self._compute_subscription_days()
                self._profile_sub.setText(f"الأيام المتبقية من الاشتراك: {remaining}")
                if remaining <= 0:
                    self._profile_sub.setStyleSheet("font-size: 14px; font-weight: bold; color: red; max-width: 300px; min-width: 250px;")
                elif remaining <= 7:
                    self._profile_sub.setStyleSheet("font-size: 14px; font-weight: bold; color: #e67e22; max-width: 300px; min-width: 250px;")
                else:
                    self._profile_sub.setStyleSheet("font-size: 14px; font-weight: bold; color: darkgreen; max-width: 300px; min-width: 250px;")
            pix_b64 = self._api_data.get("profile_pixmap")
            if pix_b64:
                import base64
                pix_data = base64.b64decode(pix_b64)
                pix = QPixmap()
                pix.loadFromData(pix_data)
                self._profile_pic_label.setPixmap(_make_circular_pixmap(pix, 100))
            else:
                letter = self._display_name[0] if self._display_name else "?"
                avatar = _make_avatar_pixmap(letter, size=100)
                self._profile_pic_label.setPixmap(avatar)
            return
        if self._username not in self._users:
            return
        user = self._users[self._username]
        self._profile_shop.setText(user.get("shop_name", ""))
        self._profile_eng.setText(self._username)
        self._profile_phone.setText(user.get("phone", ""))
        self._profile_date.setText(f"تاريخ التسجيل: {user.get('reg_date', '')}")

        if user.get("is_admin"):
            self._dashboard_btn.show()
            self._banner_section.show()
            self._profile_sub.hide()
            header = self.sender() if hasattr(self, 'sender') else None
            for w in (self._profile_eng, self._profile_phone, self._profile_shop):
                w.show()
        else:
            self._dashboard_btn.hide()
            self._banner_section.hide()
            self._profile_sub.show()
            remaining = self._compute_subscription_days(user)
            self._profile_sub.setText(f"الأيام المتبقية من الاشتراك: {remaining}")
            if remaining <= 0:
                self._profile_sub.setStyleSheet("font-size: 14px; font-weight: bold; color: red; max-width: 300px; min-width: 250px;")
            elif remaining <= 7:
                self._profile_sub.setStyleSheet("font-size: 14px; font-weight: bold; color: #e67e22; max-width: 300px; min-width: 250px;")
            else:
                self._profile_sub.setStyleSheet("font-size: 14px; font-weight: bold; color: darkgreen; max-width: 300px; min-width: 250px;")

        pix_data = user.get("profile_pixmap")
        if pix_data:
            pix = QPixmap()
            pix.loadFromData(pix_data)
            self._profile_pic_label.setPixmap(_make_circular_pixmap(pix, 100))
        else:
            letter = self._display_name[0] if self._display_name else "?"
            avatar = _make_avatar_pixmap(letter, size=100)
            self._profile_pic_label.setPixmap(avatar)

    def _pick_profile_picture(self):
        path, _ = QFileDialog.getOpenFileName(self, "اختر صورة الملف الشخصي", "",
            "Images (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        pix = QPixmap(path)
        if pix.isNull():
            return
        circular = _make_circular_pixmap(pix, 100)
        self._profile_pic_label.setPixmap(circular)
        from PySide6.QtCore import QBuffer
        buf = QBuffer()
        buf.open(QBuffer.WriteOnly)
        circular.save(buf, "PNG")
        buf.close()
        raw_bytes = bytes(buf.data())
        if self._use_api:
            from core.database import api_upload_pixmap
            api_upload_pixmap(raw_bytes)
        elif self._username in self._users:
            self._users[self._username]["profile_pixmap"] = raw_bytes
            self._save_user(self._username)
        small_circular = _make_circular_pixmap(pix, 48)
        self._profile_label.setPixmap(small_circular)
        logger.info("تم تغيير صورة الملف الشخصي لـ %s", self._username)

    def _save_profile(self):
        shop = self._profile_shop.text().strip()
        new_eng = self._profile_eng.text().strip()
        phone = self._profile_phone.text().strip()
        if not shop or not new_eng or not phone:
            QMessageBox.warning(self, "تنبيه", "يرجى ملء جميع الحقول")
            return
        import re
        if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]*$', new_eng):
            QMessageBox.warning(self, "خطأ", "اسم بالانكليزي يجب أن يبدأ بحرف ويحتوي على أحرف إنجليزية وأرقام فقط")
            return
        if self._use_api:
            if new_eng != self._username:
                QMessageBox.warning(self, "خطأ", "لا يمكن تغيير اسم المستخدم في وضع الخادم")
                return
            from core.database import api_update_profile
            rdata, err = api_update_profile(shop, phone)
            if err:
                QMessageBox.warning(self, "خطأ", err)
                return
            self._display_name = shop
            self._update_auth_ui()
            QMessageBox.information(self, "تم", "تم حفظ التغييرات")
            logger.info("تم حفظ الملف الشخصي لـ %s (API)", self._username)
            return
        if self._username not in self._users:
            return
        if new_eng != self._username:
            if new_eng == "ahmed" or new_eng in self._users:
                QMessageBox.warning(self, "خطأ", "اسم بالانكليزي موجود مسبقاً")
                return
            old_username = self._username
            self._users[new_eng] = self._users.pop(self._username)
            if self._is_admin:
                self._users[new_eng]["is_admin"] = True
            self._username = new_eng
            self._delete_user(old_username)
        user = self._users[self._username]
        user["shop_name"] = shop
        user["phone"] = phone
        if not user.get("is_admin"):
            self._display_name = shop
        self._save_user(self._username)
        self._update_auth_ui()
        QMessageBox.information(self, "تم", "تم حفظ التغييرات")
        logger.info("تم حفظ الملف الشخصي لـ %s", self._username)

    def _open_notifier_settings(self):
        from core.notifier import configure, _load_config
        cfg = _load_config()
        dialog = QDialog(self)
        dialog.setWindowTitle("إعدادات واتساب")
        dialog.setMinimumWidth(450)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            "إعدادات واتساب (مجاني 100% من ميتا)\n"
            "أول 1,000 رسالة/شهر مجاناً\n"
            "بعد الإعداد، الرسائل ترسل تلقائياً حتى لو جهازك طافي"
        ))
        layout.addWidget(QLabel("رقم الهاتف المرسل (مثال: 96478065402819):"))
        sender_inp = QLineEdit(cfg.get("sender_phone", "96478065402819"))
        sender_inp.setPlaceholderText("9647XXXXXXXX")
        layout.addWidget(sender_inp)
        layout.addWidget(QLabel("رقم معرف الهاتف (Phone Number ID من صفحة ميتا):"))
        pid_inp = QLineEdit(cfg.get("whatsapp_phone_id", ""))
        pid_inp.setPlaceholderText("مثال: 123456789012345")
        layout.addWidget(pid_inp)
        layout.addWidget(QLabel("الرمز المميز الدائم (Permanent Token من صفحة ميتا):"))
        tok_inp = QLineEdit(cfg.get("whatsapp_token", ""))
        tok_inp.setPlaceholderText("EAAT... يبدأ بـ EAAT")
        tok_inp.setEchoMode(QLineEdit.Password)
        layout.addWidget(tok_inp)
        btn_row = QHBoxLayout()
        btn_save = QPushButton("حفظ")
        btn_save.setStyleSheet("background:#27ae60;color:white;font-size:14px;padding:8px 20px;border-radius:6px;border:none;font-weight:bold;")
        btn_save.clicked.connect(lambda: (
            configure("whatsapp_cloud",
                      whatsapp_token=tok_inp.text().strip(),
                      whatsapp_phone_id=pid_inp.text().strip(),
                      sender_phone=sender_inp.text().strip()),
            QMessageBox.information(dialog, "تم", "تم حفظ الإعدادات"),
            dialog.accept()
        ))
        btn_cancel = QPushButton("إلغاء")
        btn_cancel.setStyleSheet("background:#ccc;color:#333;font-size:14px;padding:8px 20px;border-radius:6px;border:none;")
        btn_cancel.clicked.connect(dialog.reject)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)
        dialog.exec()

    def _change_password(self):
        if getattr(self, '_is_admin', False):
            pw, ok = QLineEdit.getText(self, "تغيير الرقم السري",
                "أدخل الرقم السري الجديد (8 أحرف وأرقام على الأقل):", QLineEdit.Password)
            if ok and pw.strip():
                import re
                if len(pw.strip()) < 8 or not re.search(r'[a-zA-Z]', pw) or not re.search(r'[0-9]', pw):
                    QMessageBox.warning(self, "خطأ", "الرقم السري يجب أن لا يقل عن 8 أحرف ويحتوي على حروف وأرقام")
                    return
                if self._use_api:
                    from core.database import api_change_password
                    rdata, err = api_change_password(pw.strip())
                    if err:
                        QMessageBox.warning(self, "خطأ", err)
                        return
                else:
                    self._users[self._username]["password"] = pw.strip()
                    self._save_user(self._username)
                QMessageBox.information(self, "تم", "تم تغيير الرقم السري")
                logger.info("تم تغيير الرقم السري لـ %s", self._username)
            return
        QMessageBox.information(self, "تغيير الرقم السري",
            "تغيير الرقم السري يتم عن طريق المالك فقط.\n"
            "يرجى التواصل مع المالك لتغيير الرقم السري.")

    def _open_profile(self):
        self._update_profile_page()
        self._stack.setCurrentWidget(self._profile_widget)
        self.setWindowTitle("ورشة طباعة - الملف الشخصي")
        logger.info("فتح الملف الشخصي لـ %s", self._username)

    def _build_subscription_page(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignCenter)

        back_btn = QPushButton("← رجوع")
        back_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #e67e22; font-size: 14px;
                border: none; font-weight: bold; padding: 5px;
            }
            QPushButton:hover { color: #d35400; }
        """)
        back_btn.clicked.connect(self._switch_to_main)
        top = QHBoxLayout()
        top.addWidget(back_btn)
        top.addStretch()
        layout.addLayout(top)

        msg = QLabel("الخدمة غير متوفرة حالياً\nفقط عن طريق المالك")
        msg.setAlignment(Qt.AlignCenter)
        msg.setStyleSheet("font-size: 20px; font-weight: bold; color: #e67e22; margin: 30px;")
        layout.addWidget(msg)

        contact = QLabel("تواصل مع المالك:\n📷  1wrsha\n📞  07865402819")
        contact.setAlignment(Qt.AlignCenter)
        contact.setStyleSheet("font-size: 16px; color: #555; margin: 20px;")
        layout.addWidget(contact)

        widget.setObjectName("subPage")
        widget.setStyleSheet("#subPage { background: #cceeff; }")
        return widget

    def _build_my_subscriptions_page(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        top = QHBoxLayout()
        back_btn = QPushButton("← رجوع")
        back_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #e67e22; font-size: 14px;
                border: none; font-weight: bold; padding: 5px;
            }
            QPushButton:hover { color: #d35400; }
        """)
        back_btn.clicked.connect(self._my_subs_back)
        top.addWidget(back_btn)
        top.addStretch()
        layout.addLayout(top)

        header = QLabel("اشتراكاتي")
        header.setAlignment(Qt.AlignCenter)
        header.setStyleSheet("font-size: 22px; font-weight: bold; color: #8e44ad; margin-bottom: 10px;")
        layout.addWidget(header)

        self._my_subs_table = QTableWidget()
        self._my_subs_table.setColumnCount(4)
        self._my_subs_table.setHorizontalHeaderLabels(["من تاريخ", "إلى تاريخ", "عدد الأيام", "الحالة"])
        self._my_subs_table.horizontalHeader().setStretchLastSection(True)
        self._my_subs_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._my_subs_table.setStyleSheet("""
            QTableWidget { font-size: 13px; border: 1px solid #ddd; border-radius: 8px; }
            QHeaderView::section { background: #8e44ad; color: white; font-weight: bold; padding: 6px; border: none; }
        """)
        layout.addWidget(self._my_subs_table)

        widget.setObjectName("mySubsPage")
        widget.setStyleSheet("#mySubsPage { background: #cceeff; }")
        return widget

    def _my_subs_back(self):
        self._switch_to_main()

    def _set_banner_image(self, side):
        path, _ = QFileDialog.getOpenFileName(self, f"اختر صورة للمستطيل {side}", "",
            "Images (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        pix = QPixmap(path)
        if pix.isNull():
            return
        from PySide6.QtCore import QBuffer
        buf = QBuffer()
        buf.open(QBuffer.WriteOnly)
        pix.scaled(400, 400, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(buf, "PNG")
        buf.close()
        raw_bytes = bytes(buf.data())
        link, ok = QInputDialog.getText(self, "رابط المستطيل",
            f"أدخل الرابط للمستطيل {side} (اختياري):")
        if ok:
            link_text = link.strip()
        else:
            link_text = ""
        if self._use_api:
            from core.database import api_set_banner
            api_set_banner(side, raw_bytes, link_text)
        else:
            admin = self._users["ahmed"]
            admin[f"banner_{side}_pixmap"] = raw_bytes
            admin[f"banner_{side}_link"] = link_text
            self._save_user("ahmed")
        self._update_banners()
        QMessageBox.information(self, "تم", f"تم تحديث صورة المستطيل {side}")
        logger.info("تم تحديث صورة المستطيل %s", side)

    def _delete_banner_image(self, side):
        reply = QMessageBox.question(self, "تأكيد الحذف",
            f"هل أنت متأكد من حذف صورة المستطيل {side}؟",
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        if self._use_api:
            from core.database import api_delete_banner
            api_delete_banner(side)
        else:
            admin = self._users.get("ahmed")
            if not admin:
                return
            admin[f"banner_{side}_pixmap"] = None
            admin[f"banner_{side}_link"] = ""
            self._save_user("ahmed")
        self._update_banners()
        side_name = "الأيسر" if side == "left" else "الأيمن"
        QMessageBox.information(self, "تم", f"تم حذف صورة المستطيل {side_name}")
        logger.info("تم حذف صورة المستطيل %s", side)

    def _open_my_subscriptions(self):
        self._update_my_subscriptions_page()
        self._stack.setCurrentWidget(self._my_subs_widget)
        self.setWindowTitle("ورشة طباعة - اشتراكاتي")
        logger.info("فتح صفحة اشتراكاتي لـ %s", self._username)

    def _update_my_subscriptions_page(self):
        self._my_subs_table.setRowCount(0)
        if self._use_api:
            from core.database import api_get_subscriptions
            data, err = api_get_subscriptions(self._username)
            if err:
                return
            subs = data.get("subscriptions", [])
        else:
            if self._username not in self._users:
                return
            user = self._users[self._username]
            subs = user.get("subscriptions", [])
        today = date.today()
        for i, sub in enumerate(subs):
            self._my_subs_table.insertRow(i)
            self._my_subs_table.setItem(i, 0, QTableWidgetItem(sub.get("start", "")))
            self._my_subs_table.setItem(i, 1, QTableWidgetItem(sub.get("end", "")))
            self._my_subs_table.setItem(i, 2, QTableWidgetItem(str(sub.get("days", 0))))
            try:
                end = date.fromisoformat(sub["end"])
                status = "ساري" if end > today else "منتهي"
            except Exception:
                status = "---"
            status_item = QTableWidgetItem(status)
            status_item.setForeground(QColor(Qt.darkGreen) if status == "ساري" else QColor(Qt.red))
            self._my_subs_table.setItem(i, 3, status_item)
        logger.info("تحديث صفحة اشتراكاتي لـ %s: %d اشتراك", self._username, len(subs))

    def _open_subscription_page(self):
        self._stack.setCurrentWidget(self._subscription_widget)
        self.setWindowTitle("ورشة طباعة - الاشتراك")
        logger.info("فتح صفحة الاشتراك لـ %s", self._username)

    def _logout(self):
        confirm = QMessageBox.question(self, "تسجيل خروج",
            "هل أنت متأكد من تسجيل الخروج؟",
            QMessageBox.Yes | QMessageBox.No)
        if confirm != QMessageBox.Yes:
            return
        total = len(self._users) - 1 if not self._use_api else 0
        logger.info("تسجيل خروج. عدد المستخدمين المسجلين: %d", total)
        if self._use_api:
            from core.database import api_logout
            api_logout()
        self._logged_in = False
        self._username = ""
        self._display_name = ""
        self._is_admin = False
        self._clear_session()
        from core import api_client
        api_client.set_token(None)
        self._switch_to_main()
        logger.info("تم تسجيل الخروج")

    def _open_login(self):
        self._stack.setCurrentWidget(self._login_widget)
        self.setWindowTitle("ورشة طباعة - تسجيل دخول")
        logger.info("فتح صفحة تسجيل الدخول")

    def _open_register(self):
        self._stack.setCurrentWidget(self._register_widget)
        self.setWindowTitle("ورشة طباعة - تسجيل جديد")
        logger.info("فتح صفحة التسجيل")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not self._logged_in:
            QMessageBox.information(self, "تنبيه", "يجب تسجيل الدخول أولاً")
            return
        paths = []
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            ext = os.path.splitext(path)[1].lower()
            if ext in IMAGE_EXTS:
                paths.append(path)
        if not paths:
            return
        logger.info("سحب %d صورة إلى الشاشة الرئيسية", len(paths))
        self._open_id_editor()
        for path in paths:
            self._editor.add_image(path)
