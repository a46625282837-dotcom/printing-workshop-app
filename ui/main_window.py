import logging
import os
import sys
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout,
                               QPushButton, QLabel, QHBoxLayout,
                               QStackedWidget, QLineEdit, QMessageBox,
                               QTableWidget, QTableWidgetItem, QHeaderView,
                               QFileDialog, QInputDialog, QDateEdit, QFrame,
                               QDialog, QApplication)
from PySide6.QtGui import QAction
from PySide6.QtCore import Qt, QSize, Signal, QDate
from PySide6.QtGui import QAction, QIcon, QColor, QPixmap, QPainter, QBrush, QFont
from PySide6.QtWidgets import QGraphicsDropShadowEffect
from datetime import date, timedelta
from ui.a4_editor import A4Editor
from ui.photo_editor import PhotoEditor
from ui.pdf_editor import PdfEditor

def _img_path(rel):
    base = getattr(sys, '_MEIPASS', os.path.join(os.path.dirname(__file__), '..'))
    return os.path.join(base, 'img', rel)

IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff'}
_NO_SUB_MSG = "يجب أن تشترك قبل الاستخدام. تواصل مع المالك: واتساب 07865402819"

logger = logging.getLogger(__name__)

FUTURE_STYLE = """
    QPushButton { background: #f0f0f0; border-color: #ccc; color: #aaa; }
"""


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
            from core.database import init_db, load_users, save_user
            init_db()
            self._users = load_users()
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
        self._search_date_active = False
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

        if not use_api:
            self._update_banners()

        if use_api:
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
        header_label = QLabel("للتواصل معنا للاستفسار والاشتراك")
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
        from core import api_client
        token = api_client.get_token()
        if not token:
            return
        import json
        with open(self._session_path(), "w", encoding="utf-8") as f:
            json.dump({
                "token": token,
                "username": self._username,
                "display_name": self._display_name,
                "is_admin": self._is_admin,
            }, f)

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
        token = sess.get("token")
        username = sess.get("username")
        if not token or not username:
            self._clear_session()
            return False
        from core import api_client
        api_client.set_token(token)
        api_client.set_username(username)
        from core.database import api_check_auth
        qdata, qerr = api_check_auth()
        if qerr:
            self._clear_session()
            return False
        self._logged_in = True
        self._username = username
        self._display_name = sess.get("display_name", username)
        self._is_admin = sess.get("is_admin", False)
        self._api_data = qdata
        self._update_auth_ui()
        self._switch_to_main()
        if self._is_admin:
            self._update_banners()
        if not self._is_admin:
            pend = qdata.get("pending_messages", [])
            if pend:
                rem = qdata.get("remaining_days", 0)
                QMessageBox.information(self, "تم التجديد",
                    f"تم زيادة عدد أيام اشتراكك وأصبحت {rem} يوم")
                from core.database import api_clear_pending
                api_clear_pending()
        logger.info("استعادة جلسة سابقة: %s", username)
        return True

    def _on_session_expired(self):
        self._clear_session()
        self._logged_in = False
        self._username = ""
        self._is_admin = False
        self._update_auth_ui()
        self._switch_to_main()
        QMessageBox.warning(self, "تنبيه", "الحساب شغال في لابتوب آخر")

    def _login_submit(self, eng_user, password):
        username = eng_user.text().strip()
        pw = password.text().strip()
        password.clear()
        if not username or not pw:
            QMessageBox.warning(self, "تنبيه", "يرجى إدخال اسم المستخدم والرقم السري")
            return
        if self._use_api:
            from core.database import api_login, api_check_auth, api_clear_pending
            data, err = api_login(username, pw)
            if err:
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
            self._update_auth_ui()
            self._switch_to_main()
            if self._is_admin:
                self._update_banners()
            if not self._is_admin and qdata:
                pend = qdata.get("pending_messages", [])
                if pend:
                    rem = qdata.get("remaining_days", 0)
                    QMessageBox.information(self, "تم التجديد",
                        f"تم زيادة عدد أيام اشتراكك وأصبحت {rem} يوم")
                    api_clear_pending()
            self._save_session()
            logger.info("تسجيل دخول API: %s", username)
            return
        if username not in self._users:
            QMessageBox.warning(self, "خطأ", "اسم المستخدم غير موجود")
            return
        user = self._users[username]
        if user["password"] != pw:
            QMessageBox.warning(self, "خطأ", "الرقم السري غير صحيح")
            return
        self._logged_in = True
        self._username = username
        self._display_name = user["shop_name"]
        self._is_admin = False
        self._update_auth_ui()
        self._switch_to_main()
        self._show_subscription_warning()
        self._check_pending_notifications()
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
        if self._use_api:
            from core.database import api_register
            rdata, err = api_register(data["english_name"], data["password"],
                                       data["shop_name"], phone)
            if err:
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
            self._update_auth_ui()
            self._switch_to_main()
            self._save_session()
            logger.info("تم تسجيل مستخدم API: %s", rdata["username"])
            return
        if data["english_name"] in self._users:
            QMessageBox.warning(self, "خطأ", "اسم بالانكليزي موجود مسبقاً")
            return
        for u in self._users.values():
            if u.get("phone") == phone:
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
        for line in fields.values():
            line.clear()
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
        avatar = _make_avatar_pixmap(self._display_name[0], size=size)
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

        header = QLabel("لوحة تحكم المالك")
        header.setAlignment(Qt.AlignCenter)
        header.setStyleSheet("font-size: 24px; font-weight: bold; color: #e67e22; margin-bottom: 10px;")
        layout.addWidget(header)

        self._dashboard_table = QTableWidget()
        self._dashboard_table.setColumnCount(7)
        self._dashboard_table.setHorizontalHeaderLabels([
            "اسم المكتبة", "اسم بالانكليزي", "رقم الهاتف",
            "تاريخ التسجيل", "تاريخ الاشتراك", "أيام الاشتراك", "إجراءات"
        ])
        self._dashboard_table.horizontalHeader().setStretchLastSection(True)
        self._dashboard_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._dashboard_table.setAlternatingRowColors(True)
        self._dashboard_table.setStyleSheet("""
            QTableWidget {
                font-size: 13px; border: 1px solid #ddd; border-radius: 8px;
                alternate-background-color: #f9f9f9;
            }
            QHeaderView::section {
                background: #e67e22; color: white; font-weight: bold;
                padding: 6px; border: none;
            }
        """)
        self._dashboard_table.cellClicked.connect(self._on_dashboard_cell_clicked)
        layout.addWidget(self._dashboard_table)

        search_row = QHBoxLayout()
        search_row.setSpacing(10)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("🔍 بحث باسم بالانكليزي أو رقم الهاتف...")
        self._search_input.setStyleSheet("""
            QLineEdit {
                font-size: 13px; padding: 8px; border: 2px solid #ddd;
                border-radius: 6px; min-width: 250px;
            }
        """)
        self._search_input.textChanged.connect(self._apply_dashboard_filter)
        search_row.addWidget(self._search_input)

        self._date_filter = QDateEdit()
        self._date_filter.setCalendarPopup(True)
        self._date_filter.setDate(QDate.currentDate())
        self._date_filter.setStyleSheet("""
            QDateEdit {
                font-size: 13px; padding: 6px; border: 2px solid #ddd;
                border-radius: 6px;
            }
        """)
        self._date_filter.dateChanged.connect(self._on_date_filter_changed)
        search_row.addWidget(QLabel("📅 تاريخ:"))
        search_row.addWidget(self._date_filter)

        clear_date_btn = QPushButton("✕ إلغاء")
        clear_date_btn.setFixedHeight(30)
        clear_date_btn.setStyleSheet("""
            QPushButton {
                background: #ccc; color: #333; font-size: 12px;
                border-radius: 6px; border: none; padding: 4px 10px;
            }
            QPushButton:hover { background: #bbb; }
        """)
        clear_date_btn.clicked.connect(self._clear_date_filter)
        search_row.addWidget(clear_date_btn)

        layout.addLayout(search_row)

        btn_refresh = QPushButton("🔄 تحديث")
        btn_refresh.setStyleSheet("""
            QPushButton {
                background: #e67e22; color: white; font-size: 14px;
                padding: 8px 25px; border-radius: 6px; border: none;
                font-weight: bold;
            }
            QPushButton:hover { background: #d35400; }
        """)
        btn_refresh.clicked.connect(self._refresh_dashboard)
        layout.addWidget(btn_refresh, 0, Qt.AlignCenter)

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
        layout.addWidget(btn_settings, 0, Qt.AlignCenter)

        widget.setObjectName("dashboardPage")
        widget.setStyleSheet("#dashboardPage { background: #cceeff; }")
        return widget

    def _refresh_dashboard(self, search_text="", filter_date=None):
        self._dashboard_table.setRowCount(0)
        if self._use_api:
            from core.database import api_get_users
            users_data, err = api_get_users()
            if err:
                logger.error("فشل تحميل لوحة التحكم: %s", err)
                return
            users_list = [(u["username"], u) for u in users_data]
        else:
            users_list = [(eng_name, user) for eng_name, user in self._users.items() if not user.get("is_admin")]
        if search_text:
            st = search_text.lower()
            users_list = [(e, u) for e, u in users_list
                          if st in e.lower() or st in u.get("phone", "").lower()]
        if filter_date is not None:
            fd = filter_date.strftime("%Y-%m-%d")
            users_list = [(e, u) for e, u in users_list if u.get("reg_date", "") == fd]
        logger.info("تحديث لوحة التحكم: %d مستخدم من %d إجمالي", len(users_list), len(users_list))
        for i, (eng_name, user) in enumerate(sorted(users_list, key=lambda x: x[1].get("reg_date", ""))):
            self._dashboard_table.insertRow(i)
            self._dashboard_table.setItem(i, 0, QTableWidgetItem(user.get("shop_name", "")))
            self._dashboard_table.setItem(i, 1, QTableWidgetItem(eng_name))
            self._dashboard_table.setItem(i, 2, QTableWidgetItem(user.get("phone", "")))
            self._dashboard_table.setItem(i, 3, QTableWidgetItem(user.get("reg_date", "")))
            for col in range(4):
                self._dashboard_table.item(i, col).setForeground(QColor("#333"))
            if self._use_api:
                remaining = user.get("remaining_days", 0)
                sub_date = user.get("latest_sub_start", "بدون اشتراك")
            else:
                remaining = self._compute_subscription_days(username=eng_name)
                subs = user.get("subscriptions", [])
                latest_sub = subs[-1] if subs else None
                sub_date = latest_sub["start"] if latest_sub else "بدون اشتراك"
            self._dashboard_table.setItem(i, 4, QTableWidgetItem(sub_date))
            self._dashboard_table.item(i, 4).setForeground(QColor("#333"))
            self._dashboard_table.setItem(i, 5, QTableWidgetItem(str(remaining)))
            self._dashboard_table.item(i, 5).setForeground(QColor("#333"))

            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(2, 2, 2, 2)
            actions_layout.setSpacing(4)

            btn_reset = QPushButton("🔑")
            btn_reset.setToolTip("استعادة الرقم السري")
            btn_reset.setFixedSize(28, 28)
            btn_reset.setStyleSheet("QPushButton { background: #3498db; color: white; border-radius: 14px; font-size: 12px; } QPushButton:hover { background: #2980b9; }")
            btn_reset.clicked.connect(lambda checked, e=eng_name: self._dashboard_reset_password(e))
            actions_layout.addWidget(btn_reset)

            btn_add_days = QPushButton("➕")
            btn_add_days.setToolTip("زيادة أيام الاشتراك")
            btn_add_days.setFixedSize(28, 28)
            btn_add_days.setStyleSheet("QPushButton { background: #27ae60; color: white; border-radius: 14px; font-size: 12px; } QPushButton:hover { background: #219a52; }")
            btn_add_days.clicked.connect(lambda checked, e=eng_name: self._dashboard_add_days(e))
            actions_layout.addWidget(btn_add_days)

            btn_delete = QPushButton("✕")
            btn_delete.setToolTip("حذف الحساب")
            btn_delete.setFixedSize(28, 28)
            btn_delete.setStyleSheet("QPushButton { background: #e74c3c; color: white; border-radius: 14px; font-size: 12px; } QPushButton:hover { background: #c0392b; }")
            btn_delete.clicked.connect(lambda checked, e=eng_name: self._dashboard_delete_user(e))
            actions_layout.addWidget(btn_delete)

            self._dashboard_table.setCellWidget(i, 6, actions_widget)
        logger.info("تحديث لوحة التحكم: %d مستخدم", self._dashboard_table.rowCount())

    def _apply_dashboard_filter(self):
        search = self._search_input.text().strip()
        if self._search_date_active:
            qd = self._date_filter.date()
            fd = date(qd.year(), qd.month(), qd.day())
        else:
            fd = None
        self._refresh_dashboard(search_text=search, filter_date=fd)

    def _clear_date_filter(self):
        self._search_date_active = False
        self._apply_dashboard_filter()

    def _on_date_filter_changed(self):
        self._search_date_active = True
        self._apply_dashboard_filter()

    def _open_dashboard(self):
        self._prev_page_index = self._stack.currentIndex()
        self._search_date_active = False
        self._search_input.clear()
        self._date_filter.setDate(QDate.currentDate())
        self._refresh_dashboard()
        self._stack.setCurrentWidget(self._dashboard_widget)
        self.setWindowTitle("ورشة طباعة - لوحة تحكم المالك")
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

    def _on_dashboard_cell_clicked(self, row, col):
        if col == 1:
            item = self._dashboard_table.item(row, col)
            if item:
                eng_name = item.text()
                self._show_subscription_history_dialog(eng_name)

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
                avatar = _make_avatar_pixmap(self._display_name[0], size=100)
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
            avatar = _make_avatar_pixmap(self._display_name[0], size=100)
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
