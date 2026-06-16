import unittest
import sys
import logging
import inspect
import os
import tempfile
import cv2
import numpy as np
from PIL import Image

logging.disable(logging.CRITICAL)

from core.image_utils import card_px_size, resize_to_card, CARD_WIDTH_MM, CARD_HEIGHT_MM, TARGET_DPI
from core.id_extractor import extract_card, four_point_transform, order_points
from core.printer import print_scene, set_printer_name, get_selected_printer_name
from core.photo_processor import remove_background, resize_to_photo_size, photo_px_size, auto_crop_subject



class TestImageUtils(unittest.TestCase):
    def test_card_px_size_default(self):
        w, h = card_px_size()
        expected_w = int(CARD_WIDTH_MM / 25.4 * TARGET_DPI)
        expected_h = int(CARD_HEIGHT_MM / 25.4 * TARGET_DPI)
        self.assertEqual((w, h), (expected_w, expected_h))

    def test_card_px_size_custom_dpi(self):
        w, h = card_px_size(dpi=150)
        expected_w = int(CARD_WIDTH_MM / 25.4 * 150)
        expected_h = int(CARD_HEIGHT_MM / 25.4 * 150)
        self.assertEqual((w, h), (expected_w, expected_h))

    def test_resize_to_card_keeps_small(self):
        img = Image.new("RGB", (800, 600), color="red")
        resized = resize_to_card(img)
        target = card_px_size()
        self.assertLessEqual(resized.width, target[0])
        self.assertLessEqual(resized.height, target[1])
        self.assertEqual(resized.mode, "RGB")

    def test_resize_to_card_downscales_large(self):
        img = Image.new("RGB", (2000, 1500), color="red")
        resized = resize_to_card(img)
        target = card_px_size()
        self.assertLessEqual(resized.width, target[0])
        self.assertLessEqual(resized.height, target[1])

    def test_resize_to_card_already_correct(self):
        target = card_px_size()
        img = Image.new("RGB", target, color="blue")
        resized = resize_to_card(img)
        self.assertEqual(resized.size, target)

    def test_resize_landscape(self):
        img = Image.new("RGB", (1200, 400), color="green")
        resized = resize_to_card(img)
        target = card_px_size()
        self.assertLessEqual(resized.width, target[0])
        self.assertLessEqual(resized.height, target[1])


class TestIDExtractor(unittest.TestCase):
    def test_extract_card_no_contour(self):
        img = Image.new("RGB", (200, 300), color=(128, 128, 128))
        result = extract_card(img)
        self.assertIsInstance(result, Image.Image)

    def test_extract_card_with_rectangle(self):
        img = Image.new("RGB", (400, 500), color=(200, 200, 200))
        arr = np.array(img)
        x1, y1, x2, y2 = 50, 50, 350, 450
        arr[y1:y2, x1:x2] = (255, 255, 255)
        arr[y1+1:y2-1, x1+1:x2-1] = (100, 150, 200)
        pil = Image.fromarray(arr)
        result = extract_card(pil)
        self.assertIsInstance(result, Image.Image)

    def test_four_point_transform(self):
        img = np.zeros((200, 200, 3), dtype=np.uint8)
        pts = np.array([[10, 10], [190, 10], [190, 190], [10, 190]], dtype="float32")
        warped = four_point_transform(img, pts)
        self.assertEqual(warped.shape[:2], (180, 180))

    def test_order_points(self):
        pts = np.array([[50, 200], [10, 10], [200, 50], [190, 190]], dtype="float32")
        ordered = order_points(pts)
        self.assertTrue(np.array_equal(ordered[0], [10, 10]))
        self.assertTrue(np.array_equal(ordered[2], [200, 200]) or
                        np.array_equal(ordered[2], [190, 190]))

    def test_extract_all_white_image(self):
        img = Image.new("RGB", (300, 400), color="white")
        result = extract_card(img)
        self.assertIsInstance(result, Image.Image)

    def test_extract_all_black_image(self):
        img = Image.new("RGB", (300, 400), color="black")
        result = extract_card(img)
        self.assertIsInstance(result, Image.Image)

    def test_extract_card_grabcut_fallback(self):
        arr = np.full((200, 300, 3), 30, dtype=np.uint8)
        cy, cx = 100, 150
        for y in range(200):
            for x in range(300):
                d = ((y - cy)/70)**2 + ((x - cx)/100)**2
                if d < 1:
                    arr[y, x] = [180, 200, 220]
                elif d < 1.3:
                    t = (d - 1) / 0.3
                    arr[y, x] = ((1-t) * np.array([180, 200, 220]) + t * np.array([30, 30, 30])).astype(np.uint8)
        pil = Image.fromarray(arr)
        result = extract_card(pil)
        self.assertIsInstance(result, Image.Image)


class TestEdgeCases(unittest.TestCase):
    def test_transparent_image(self):
        img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        rgb = img.convert("RGB")
        result = extract_card(rgb)
        self.assertIsInstance(result, Image.Image)

    def test_tiny_image(self):
        img = Image.new("RGB", (1, 1), color="white")
        result = extract_card(img)
        resized = resize_to_card(result)
        self.assertEqual(resized.size, (1, 1))

    def test_large_image(self):
        img = Image.new("RGB", (5000, 5000), color="blue")
        result = extract_card(img)
        resized = resize_to_card(result)
        self.assertLessEqual(resized.width, card_px_size()[0])
        self.assertLessEqual(resized.height, card_px_size()[1])

    def test_extract_card_various_colors(self):
        for color in [
            (200, 80, 80), (80, 200, 80), (80, 80, 200), (200, 200, 80)
        ]:
            img = Image.new("RGB", (500, 400), color=(40, 60, 100))
            arr = np.array(img)
            arr[80:420, 50:350] = color
            result = extract_card(Image.fromarray(arr))
            self.assertIsInstance(result, Image.Image)
            self.assertGreater(result.width, 0)
            self.assertGreater(result.height, 0)

    def test_extract_card_colorful_gradient_background(self):
        """Card on a multicolored gradient bg - tests the color clustering fallback."""
        arr = np.zeros((500, 600, 3), dtype=np.uint8)
        h, w = arr.shape[:2]
        for y in range(h):
            for x in range(w):
                arr[y, x] = [int(128 + 64 * np.sin(x / 30) + 64 * np.cos(y / 20)),
                             int(128 + 64 * np.cos(x / 25 + y / 35)),
                             int(128 + 64 * np.sin((x + y) / 40))]
        x1, y1, x2, y2 = 100, 80, 500, 420
        cv2.rectangle(arr, (x1, y1), (x2, y2), (200, 180, 220), -1)
        pil = Image.fromarray(arr)
        result = extract_card(pil)
        self.assertIsInstance(result, Image.Image)
        self.assertGreater(result.width, 0)
        self.assertGreater(result.height, 0)

    def test_extract_card_low_contrast(self):
        """Card with subtle contrast against background."""
        arr = np.full((400, 500, 3), [100, 110, 120], dtype=np.uint8)
        x1, y1, x2, y2 = 80, 60, 420, 340
        cv2.rectangle(arr, (x1, y1), (x2, y2), (115, 125, 135), -1)
        pil = Image.fromarray(arr)
        result = extract_card(pil)
        self.assertIsInstance(result, Image.Image)

    def test_extract_card_noisy_background(self):
        """Card on highly textured/noisy background."""
        np.random.seed(42)
        arr = np.random.randint(0, 256, (400, 500, 3), dtype=np.uint8)
        x1, y1, x2, y2 = 60, 50, 440, 350
        cv2.rectangle(arr, (x1, y1), (x2, y2), (180, 200, 220), -1)
        pil = Image.fromarray(arr)
        result = extract_card(pil)
        self.assertIsInstance(result, Image.Image)
        self.assertGreater(result.width, 0)
        self.assertGreater(result.height, 0)

    def test_extract_card_perspective(self):
        """Card with perspective distortion."""
        arr = np.full((400, 500, 3), [50, 60, 70], dtype=np.uint8)
        src_pts = np.array([[80, 120], [420, 60], [440, 340], [60, 320]], dtype="float32")
        dst_pts = np.array([[0, 0], [350, 0], [350, 280], [0, 280]], dtype="float32")
        M = cv2.getPerspectiveTransform(src_pts, dst_pts)
        card = np.full((300, 400, 3), [200, 210, 230], dtype=np.uint8)
        card[30:270, 20:380] = [180, 190, 220]
        warped = cv2.warpPerspective(card, M, (500, 400))
        arr_mask = (warped.sum(axis=2) > 0).astype(np.uint8)
        result_img = np.where(arr_mask[:, :, None], warped, arr)
        pil = Image.fromarray(result_img.astype(np.uint8))
        result = extract_card(pil)
        self.assertIsInstance(result, Image.Image)
        self.assertGreater(result.width, 0)
        self.assertGreater(result.height, 0)


class TestPrinter(unittest.TestCase):
    def test_print_scene_accepts_copies(self):
        sig = inspect.signature(print_scene)
        self.assertIn('copies', sig.parameters)
        self.assertEqual(sig.parameters['copies'].default, 1)

    def test_print_scene_accepts_duplex(self):
        sig = inspect.signature(print_scene)
        self.assertIn('duplex', sig.parameters)
        self.assertEqual(sig.parameters['duplex'].default, False)

    def test_set_printer_name(self):
        old = get_selected_printer_name()
        set_printer_name("Test Printer")
        self.assertEqual(get_selected_printer_name(), "Test Printer")
        set_printer_name(old)


class TestCardSwap(unittest.TestCase):
    def test_grid_pos_calculation(self):
        MARGIN = 10
        CARD_W = 90
        CARD_H = 55
        CARD_GAP = 10
        A4_H = 297
        CARDS_PER_ROW = 2
        MAX_CARDS = 8

        def grid_pos(idx):
            page_idx = idx // MAX_CARDS
            local_idx = idx % MAX_CARDS
            y_offset = page_idx * A4_H
            col = local_idx % CARDS_PER_ROW
            row = local_idx // CARDS_PER_ROW
            return (MARGIN + col * (CARD_W + CARD_GAP),
                    y_offset + MARGIN + row * (CARD_H + CARD_GAP))

        self.assertEqual(grid_pos(0), (10, 10))
        self.assertEqual(grid_pos(1), (110, 10))
        self.assertEqual(grid_pos(2), (10, 75))
        self.assertEqual(grid_pos(3), (110, 75))
        self.assertEqual(grid_pos(7), (110, 205))
        self.assertEqual(grid_pos(8), (10, 307))
        self.assertEqual(grid_pos(9), (110, 307))
        self.assertEqual(grid_pos(15), (110, 502))


class TestPhotoProcessor(unittest.TestCase):
    def test_photo_px_size_35x45(self):
        w, h = photo_px_size(35, 45, 300)
        self.assertAlmostEqual(w / h, 35 / 45, delta=0.01)

    def test_photo_px_size_30x40(self):
        w, h = photo_px_size(30, 40, 300)
        self.assertAlmostEqual(w / h, 30 / 40, delta=0.01)

    def test_remove_background_rgba(self):
        img = Image.new("RGB", (200, 300), color=(100, 150, 200))
        result = remove_background(img)
        self.assertEqual(result.mode, "RGBA")

    def test_resize_to_photo_size_small(self):
        img = Image.new("RGBA", (100, 80), color=(255, 0, 0))
        resized = resize_to_photo_size(img, 35, 45, 300)
        self.assertLessEqual(resized.width, photo_px_size(35, 45, 300)[0])
        self.assertLessEqual(resized.height, photo_px_size(35, 45, 300)[1])

    def test_resize_to_photo_size_large(self):
        img = Image.new("RGBA", (2000, 1500), color=(0, 255, 0))
        resized = resize_to_photo_size(img, 50, 45, 300)
        self.assertLessEqual(resized.width, photo_px_size(50, 45, 300)[0])
        self.assertLessEqual(resized.height, photo_px_size(50, 45, 300)[1])
        self.assertAlmostEqual(resized.width / resized.height, 2000 / 1500, delta=0.01)

    def test_auto_crop_subject_crops_transparent_margin(self):
        img = Image.new("RGBA", (200, 300), (0, 0, 0, 0))
        img.putpixel((50, 60), (255, 0, 0, 255))
        img.putpixel((150, 240), (0, 255, 0, 255))
        cropped = auto_crop_subject(img)
        self.assertLess(cropped.width, 200)
        self.assertLess(cropped.height, 300)
        self.assertGreater(cropped.width, 50)
        self.assertGreater(cropped.height, 100)

    def test_auto_crop_subject_no_alpha(self):
        img = Image.new("RGB", (100, 100), (255, 0, 0))
        cropped = auto_crop_subject(img)
        self.assertEqual(cropped.size, (100, 100))

    def test_auto_crop_subject_fully_transparent(self):
        img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        cropped = auto_crop_subject(img)
        self.assertEqual(cropped.size, (100, 100))

    def test_photo_grid_pos(self):
        MARGIN = 10
        GAP = 5
        A4_H = 297
        pw, ph = 35, 45
        cols = max(1, (210 - 2 * MARGIN + GAP) // (pw + GAP))

        def grid_pos(idx, per_page):
            page_idx = idx // per_page
            local_idx = idx % per_page
            y_offset = page_idx * A4_H
            col = local_idx % cols
            row = local_idx // cols
            return (MARGIN + col * (pw + GAP),
                    y_offset + MARGIN + row * (ph + GAP))

        per_page = cols * max(1, (297 - 2 * MARGIN + GAP) // (ph + GAP))
        self.assertEqual(grid_pos(0, per_page), (10, 10))


class TestPdfEditor(unittest.TestCase):
    def test_imports(self):
        from ui.pdf_editor import PdfEditor
        self.assertIsNotNone(PdfEditor)

    def test_has_color_picker(self):
        from ui.pdf_editor import PdfEditor
        self.assertTrue(hasattr(PdfEditor, '_pick_color'),
                        "PdfEditor should have _pick_color method")
        self.assertTrue(hasattr(PdfEditor, '_sync_color_button'),
                        "PdfEditor should have _sync_color_button method")

    def test_has_save_methods(self):
        from ui.pdf_editor import PdfEditor
        self.assertTrue(hasattr(PdfEditor, '_save_as_pdf'),
                        "PdfEditor should have _save_as_pdf method")
        self.assertTrue(hasattr(PdfEditor, '_save_as_docx'),
                        "PdfEditor should have _save_as_docx method")

    def test_toggle_numbers_logic(self):
        arabic = '٠١٢٣٤٥٦٧٨٩'
        western = '0123456789'
        self.assertEqual(len(arabic), 10)
        self.assertEqual(len(western), 10)
        tbl_aw = str.maketrans(western, arabic)
        tbl_wa = str.maketrans(arabic, western)
        self.assertEqual("Hello 123".translate(tbl_aw), "Hello ١٢٣")
        self.assertEqual("Hello ١٢٣".translate(tbl_wa), "Hello 123")
        self.assertEqual("Hello".translate(tbl_aw), "Hello")
        self.assertEqual("Hello".translate(tbl_wa), "Hello")


class TestPdfTextEngine(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.src_pdf = os.path.join(self.tmpdir, "src.pdf")
        self.out_pdf = os.path.join(self.tmpdir, "out.pdf")
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text(fitz.Point(72, 100), "Hello World", fontsize=20)
        doc.save(self.src_pdf)
        doc.close()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_replace_text(self):
        from core.pdf_text_engine import PdfTextReplacer
        import fitz
        replacer = PdfTextReplacer(self.src_pdf)
        rect = fitz.Rect(60, 85, 260, 120)
        replacer.replace(0, [rect], "Replaced", (72, 100), font_size=20)
        replacer.save(self.out_pdf)
        self.assertTrue(os.path.exists(self.out_pdf))
        check = fitz.open(self.out_pdf)
        text = check[0].get_text()
        check.close()
        self.assertIn("Replaced", text)

    def test_process_arabic_returns_same_for_latin(self):
        from core.pdf_text_engine import PdfTextReplacer
        replacer = PdfTextReplacer(self.src_pdf)
        result = replacer.process_arabic("Hello")
        self.assertEqual(result, "Hello")


from core.font_utils import WordFontSizeAdapter


class TestWordFontSizeAdapter(unittest.TestCase):
    def test_points_to_pixels_known_dpi(self):
        px = WordFontSizeAdapter.points_to_pixels(12, dpi=96)
        self.assertAlmostEqual(px, 16.0)
        px = WordFontSizeAdapter.points_to_pixels(72, dpi=96)
        self.assertAlmostEqual(px, 96.0)

    def test_pixels_to_points_known_dpi(self):
        pt = WordFontSizeAdapter.pixels_to_points(16, dpi=96)
        self.assertAlmostEqual(pt, 12.0)
        pt = WordFontSizeAdapter.pixels_to_points(96, dpi=96)
        self.assertAlmostEqual(pt, 72.0)

    def test_roundtrip(self):
        for pt in (8, 10, 12, 14, 16, 18, 20, 24, 36, 48, 72):
            px = WordFontSizeAdapter.points_to_pixels(pt, dpi=96)
            back = WordFontSizeAdapter.pixels_to_points(px, dpi=96)
            self.assertAlmostEqual(back, pt)

    def test_line_height_percent(self):
        self.assertEqual(WordFontSizeAdapter.line_height_percent("single"), 120)
        self.assertEqual(WordFontSizeAdapter.line_height_percent("1.15"), 138)

    def test_word_sizes_contains_standard(self):
        for sz in (8, 9, 10, 11, 12, 14, 16, 18, 20, 22, 24, 26, 28, 36, 48, 72):
            self.assertIn(sz, WordFontSizeAdapter.WORD_SIZES)


class TestRefreshButton(unittest.TestCase):
    def test_refresh_user_data_method_exists(self):
        from ui.main_window import MainWindow
        self.assertTrue(hasattr(MainWindow, '_refresh_user_data'))
        self.assertTrue(callable(MainWindow._refresh_user_data))


class TestLoginSpinner(unittest.TestCase):
    def test_login_submit_shows_spinner(self):
        import inspect
        from ui.main_window import MainWindow
        src = inspect.getsource(MainWindow._login_submit)
        self.assertIn('self._login_spinner.show()', src)
        self.assertIn('self._login_spinner.hide()', src)

    def test_register_submit_shows_spinner(self):
        import inspect
        from ui.main_window import MainWindow
        src = inspect.getsource(MainWindow._register_submit)
        self.assertIn('self._register_spinner.show()', src)
        self.assertIn('self._register_spinner.hide()', src)


class TestSessionControl(unittest.TestCase):
    def test_api_client_has_logout(self):
        from core.api_client import logout
        self.assertTrue(callable(logout))

    def test_api_client_has_get_user_sessions(self):
        from core.api_client import get_user_sessions
        self.assertTrue(callable(get_user_sessions))

    def test_api_client_has_set_max_devices(self):
        from core.api_client import set_max_devices
        self.assertTrue(callable(set_max_devices))

    def test_dashboard_set_max_devices_method_exists(self):
        from ui.main_window import MainWindow
        self.assertTrue(hasattr(MainWindow, '_dashboard_set_max_devices'))
        self.assertTrue(callable(MainWindow._dashboard_set_max_devices))


if __name__ == "__main__":
    unittest.main(verbosity=2)
