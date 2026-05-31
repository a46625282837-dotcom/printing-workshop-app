import logging
from PySide6.QtGui import QFont, QFontInfo
from PySide6.QtWidgets import QApplication

logger = logging.getLogger(__name__)


class WordFontSizeAdapter:
    """
    تحويل حجم الخط من Point (Word standard) إلى Pixels بناءً على DPI الشاشة.
    المعادلة: Pixels = Points * DPI / 72
    """

    WORD_SIZES = (8, 9, 10, 11, 12, 14, 16, 18, 20, 22, 24, 26, 28, 36, 48, 72)

    # Word line spacing كـ ProporationalHeight للنسبة: 120 = Single, 138 = 1.15
    SINGLE_SPACING = 120
    SPACING_1_15 = 138

    @staticmethod
    def detect_system_dpi():
        app = QApplication.instance()
        if app is None:
            return 96
        screen = app.primaryScreen()
        if screen is None:
            return 96
        dpi = screen.logicalDotsPerInch()
        logger.info("تم اكتشاف DPI النظام: %.1f", dpi)
        return dpi

    @staticmethod
    def points_to_pixels(points, dpi=None):
        if dpi is None:
            dpi = WordFontSizeAdapter.detect_system_dpi()
        return points * dpi / 72.0

    @staticmethod
    def pixels_to_points(pixels, dpi=None):
        if dpi is None:
            dpi = WordFontSizeAdapter.detect_system_dpi()
        if dpi <= 0:
            return pixels
        return pixels * 72.0 / dpi

    @staticmethod
    def line_height_percent(spacing):
        if spacing == "1.15":
            return WordFontSizeAdapter.SPACING_1_15
        return WordFontSizeAdapter.SINGLE_SPACING
