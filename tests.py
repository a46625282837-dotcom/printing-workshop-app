import unittest
import sys
import logging
import inspect
import os
import tempfile
import cv2
import numpy as np
from PIL import Image, ImageDraw

logging.disable(logging.CRITICAL)

from core.image_utils import card_px_size, resize_to_card, CARD_WIDTH_MM, CARD_HEIGHT_MM, TARGET_DPI
from core.id_extractor import extract_card, four_point_transform, order_points, CARD_W, CARD_H
from core.printer import print_scene, set_printer_name, get_selected_printer_name
from core.photo_processor import remove_background, resize_to_photo_size, photo_px_size, auto_crop_subject, enhance_auto_remini, restore_interior_alpha



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

    @staticmethod
    def _angled_card_scene():
        """يُبني مشهداً لبطاقة منظورية (بزاوية) فوق خلفية متدرّجة ويُعيد الصورة."""
        card = Image.new("RGB", (856, 540), (240, 242, 245))
        dc = ImageDraw.Draw(card)
        dc.rectangle([30, 30, 826, 510], outline=(0, 0, 0), width=4)
        dc.rectangle([60, 60, 300, 400], fill=(150, 150, 150))
        card_bgr = np.array(card)[:, :, ::-1].copy()
        src = np.float32([[0, 0], [856, 0], [856, 540], [0, 540]])
        dst = np.float32([[150, 120], [750, 60], [820, 620], [180, 700]])
        M = cv2.getPerspectiveTransform(src, dst)
        scene = np.full((900, 1000, 3), (105, 125, 150), np.uint8)
        rng = np.random.default_rng(0)
        scene = (scene.astype("float32") * 0.5 +
                 rng.integers(0, 60, size=(900, 1000, 3)).astype("float32")).astype("uint8")
        warped = cv2.warpPerspective(card_bgr, M, (1000, 900))
        mask = cv2.warpPerspective(np.full((540, 856), 255, np.uint8), M, (1000, 900))
        ys, xs = np.where(mask > 0)
        scene[ys, xs] = warped[ys, xs]
        return Image.fromarray(scene[:, :, ::-1])

    def test_extract_card_removes_background(self):
        """استخراج البطاقة عبر rembg يزيل الخلفية ويرجع RGBA."""
        rng = np.random.default_rng(0)
        scene = rng.integers(80, 160, size=(900, 1200, 3)).astype("uint8")
        pil = Image.fromarray(scene)
        d = ImageDraw.Draw(pil)
        d.rectangle([200, 150, 800, 650], fill=(240, 242, 245))
        d.rectangle([220, 170, 780, 630], fill=(180, 200, 220))
        result = extract_card(pil)
        self.assertIsNotNone(result)
        self.assertEqual(result.mode, "RGBA")

    def test_extract_card_returns_rgba(self):
        """extract_card يرجع دائماً RGBA بغض النظر عن نوع الإدخال."""
        img = Image.new("RGB", (400, 300), color=(100, 150, 200))
        result = extract_card(img)
        self.assertEqual(result.mode, "RGBA")

    def test_extract_card_full_frame_returns_original(self):
        """صورة مملأة بالكامل تُرجع كـ RGBA."""
        img = Image.new("RGB", (500, 400), color=(128, 128, 128))
        result = extract_card(img)
        self.assertEqual(result.mode, "RGBA")


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

    def test_print_scene_accepts_back_offsets(self):
        sig = inspect.signature(print_scene)
        for name in ("back_offset_dx_mm", "back_offset_dy_mm"):
            self.assertIn(name, sig.parameters)
            self.assertEqual(sig.parameters[name].default, 0.0)

    def test_print_scene_back_offset_applies_to_back_pages_only(self):
        import inspect as _inspect
        src = _inspect.getsource(print_scene)
        self.assertIn("page_idx % 2 == 0", src,
                      "back-page correction must be applied only to even (back) pages")
        self.assertIn("off_dx = -back_offset_dx_mm if back_shift else 0.0", src,
                      "back-page shift must be applied via the source rect in scene mm")

    def test_print_setup_dialog_has_back_offset_controls(self):
        from ui.a4_editor import PrintSetupDialog
        import inspect as _inspect
        init = _inspect.getsource(PrintSetupDialog.__init__)
        self.assertIn("back_dx_spin", init)
        self.assertIn("back_dy_spin", init)
        self.assertIn("self.duplex_cb.toggled.connect(self._on_duplex_toggled)", init)
        self.assertTrue(callable(getattr(PrintSetupDialog, "back_offset_dx", None)))
        self.assertTrue(callable(getattr(PrintSetupDialog, "back_offset_dy", None)))

    def test_back_offset_defaults_by_printer_brand(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from ui.a4_editor import PrintSetupDialog
        d = PrintSetupDialog(page_count=2)
        d.printer_combo.setCurrentText("HP_FAKE_BRAND_777")
        d._load_back_offsets()
        self.assertEqual(d.back_offset_dx(), 9.0, "HP printers must default dx=9.0")
        self.assertEqual(d.back_offset_dy(), -1.0, "HP printers must default dy=-1.0")
        d.printer_combo.setCurrentText("Brother_FAKE_BRAND_777")
        d._load_back_offsets()
        self.assertEqual(d.back_offset_dx(), 0.0, "non-HP printers must default dx=0.0")
        self.assertEqual(d.back_offset_dy(), 0.0, "non-HP printers must default dy=0.0")


class TestBackOffsetRender(unittest.TestCase):
    def test_back_page_shifts_but_front_page_stays(self):
        from PySide6.QtWidgets import QApplication, QGraphicsScene
        from PySide6.QtGui import QImage, QPainter, QPen, QBrush, QColor
        from PySide6.QtCore import QRectF
        from ui.a4_editor import A4_W, A4_H
        app = QApplication.instance() or QApplication([])

        def bbox_of_dark(img):
            minx = miny = 10 ** 9
            maxx = maxy = -1
            for y in range(img.height()):
                for x in range(img.width()):
                    px = img.pixel(x, y)
                    r = (px >> 16) & 0xFF
                    g = (px >> 8) & 0xFF
                    b = px & 0xFF
                    if r < 250 and g < 250 and b < 250:
                        if x < minx:
                            minx = x
                        if x > maxx:
                            maxx = x
                        if y < miny:
                            miny = y
                        if y > maxy:
                            maxy = y
            return (minx, miny, maxx, maxy)

        def render_page(page_idx, dx_mm, dy_mm, duplex=True):
            scene = QGraphicsScene(0.0, 0.0, A4_W, A4_H * 2)
            pen = QPen(QColor(0, 0, 0))
            pen.setWidthF(0.5)
            scene.addRect(100.0, (page_idx - 1) * A4_H + 143.5, 10.0, 10.0, pen, QBrush(QColor(0, 0, 0)))
            img = QImage(2480, 3507, QImage.Format.Format_RGB32)
            img.fill(QColor(255, 255, 255))
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.scale(200.0 / A4_W, 200.0 / A4_W)
            back_shift = duplex and page_idx % 2 == 0 and (dx_mm or dy_mm)
            off_dx = -dx_mm if back_shift else 0.0
            off_dy = -dy_mm if back_shift else 0.0
            source = QRectF(off_dx, (page_idx - 1) * A4_H + off_dy, A4_W, A4_H)
            scene.render(p, QRectF(), source)
            p.end()
            return bbox_of_dark(img)

        front_plain = render_page(1, 0, 0)
        front_with = render_page(1, 5, 0)
        back_plain = render_page(2, 0, 0)
        back_with = render_page(2, 5, 0)
        self.assertEqual(front_plain, front_with,
                         "front page must be identical regardless of back offset")
        shift_x = back_with[0] - back_plain[0]
        self.assertGreaterEqual(shift_x, 40,
                                f"back page must shift right by ~5mm (got {shift_x}px)")
        self.assertEqual(back_plain[1], back_with[1],
                         "back page must not shift vertically when only dx offset is used")


class TestPhotoEnhance(unittest.TestCase):
    def test_enhance_button_wired_ready(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        import inspect as _ins
        from ui.photo_editor import PhotoCropDialog
        src = _ins.getsource(PhotoCropDialog._setup_ui)
        # الزر معطّل مؤقتاً بطلب المستخدم: يعرض رسالة صيانة ولا ينفّذ معالجة
        self.assertIn("_enhance_maintenance", src,
                      "enhance button must show the maintenance notice")
        self.assertNotIn("_btn_enhance.clicked.connect(self._run_enhance)", src,
                         "enhance button must not run the pipeline while disabled")
        self.assertIn("تحسين وتوضيح الصورة", src,
                      "button label must read تحسين وتوضيح الصورة")
        for m in ("_run_enhance", "_on_enhance_ready", "_on_enhance_failed",
                  "_enhance_maintenance"):
            self.assertTrue(callable(getattr(PhotoCropDialog, m, None)), m)

    def test_enhance_thread_defined_in_editor(self):
        from ui.photo_editor import PhotoEnhanceThread
        from PySide6.QtCore import Signal
        self.assertTrue(hasattr(PhotoEnhanceThread, "result_ready"),
                        "photo enhance thread must expose result_ready")
        self.assertTrue(hasattr(PhotoEnhanceThread, "failed"))

    def test_ai_enhance_module_exposes_enhance_image(self):
        from core.ai_enhance import enhance_image, is_available
        self.assertTrue(callable(enhance_image))
        self.assertTrue(callable(is_available))


class TestDocumentPageFit(unittest.TestCase):
    def _make_pixmap(self, w, h):
        from PySide6.QtGui import QColor, QImage, QPixmap
        img = QImage(w, h, QImage.Format.Format_RGB32)
        img.fill(QColor(255, 255, 255))
        return QPixmap.fromImage(img)

    def _item(self, w, h, pw=210.0, ph=297.0):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from ui.documents_editor import DocumentItem
        return DocumentItem(self._make_pixmap(w, h), pw, ph)

    def test_added_document_defaults_to_page_fit(self):
        item = self._item(2480, 3508)
        self.assertIsNone(item._nat_w_mm, "added image must fit the A4 page, not natural size")
        self.assertIsNone(item._nat_h_mm)
        self.assertAlmostEqual(item._base_scale(), min(210.0 / 2480, 297.0 / 3508), 8,
                               msg="base scale must be the page-fit scale")

    def test_small_image_gets_enlarged_to_fill_page(self):
        item = self._item(300, 424)
        w, h = item._content_dims()
        self.assertAlmostEqual(w, 210.0, delta=1.0,
                               msg="small image must be enlarged to fill the A4 page width")
        self.assertAlmostEqual(h, 297.0, delta=1.0,
                               msg="small image must be enlarged to fill the A4 page height")

    def test_photo_gets_enlarged_to_fill_page(self):
        item = self._item(1080, 1440)
        w, h = item._content_dims()
        self.assertAlmostEqual(w, 210.0, delta=1.0,
                               msg="4:3 image must be enlarged to fill the A4 page width")
        self.assertAlmostEqual(h, 210.0 * 1440 / 1080, delta=1.0,
                               msg="4:3 image scaled proportionally, never distorted")
        self.assertLessEqual(h, 297.0, msg="4:3 image must stay inside the A4 page height")

    def test_big_image_does_not_overshoot_page(self):
        item = self._item(3508, 5000)
        w, h = item._content_dims()
        self.assertLessEqual(w, 211.0, msg="image must never exceed the A4 page width")
        self.assertLessEqual(h, 298.0, msg="image must never exceed the A4 page height")

    def _pack_editor(self, documents):
        from types import SimpleNamespace
        return SimpleNamespace(
            _num_pages=1, documents=documents,
            _slot_w=lambda: 210.0, _slot_h=lambda: 297.0,
            _page_stride=lambda: 297.0 + 5.0)

    def test_first_full_document_places_on_first_page(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from ui.documents_editor import DocumentsEditor
        item = self._item(2480, 3508)
        ed = self._pack_editor([])
        page, off = DocumentsEditor._pack_slot(ed, item)
        self.assertEqual(page, 0, "first full-page document must land on page 1 (index 0)")
        self.assertEqual(off, QPointF(0.0, 0.0))

    def test_second_full_document_goes_to_new_page(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from ui.documents_editor import DocumentsEditor
        first = self._item(2480, 3508)
        first.setPos(0, 0)
        ed = self._pack_editor([first])
        item = self._item(2480, 3508)
        page, _ = DocumentsEditor._pack_slot(ed, item)
        self.assertEqual(page, 1, "second full-page document must go to a new page")

    def _editor_stub(self, num_pages, docs):
        from types import SimpleNamespace
        from PySide6.QtWidgets import QApplication, QGraphicsScene
        app = QApplication.instance() or QApplication([])
        return SimpleNamespace(
            _num_pages=num_pages, documents=docs,
            _page_stride=lambda: 297.0 + 5.0, _page_w=lambda: 210.0,
            scene=QGraphicsScene(), _current_page=2,
            _update_page_lbl=lambda: None)

    def _prune(self, ed):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from ui.documents_editor import DocumentsEditor
        DocumentsEditor._prune_empty_trailing_pages(ed)
        return ed._num_pages

    def test_moving_last_doc_out_prunes_empty_page(self):
        first = self._item(2480, 3508)
        first.setPos(0, 0)
        n = self._prune(self._editor_stub(2, [first]))
        self.assertEqual(n, 1, "empty trailing page must be removed after document left it")

    def test_page_with_document_is_kept(self):
        first = self._item(2480, 3508)
        first.setPos(0, 302)
        n = self._prune(self._editor_stub(2, [first]))
        self.assertEqual(n, 2, "page containing a document must never be pruned")

    def test_only_trailing_empty_pages_are_pruned(self):
        doc = self._item(2480, 3508)
        doc.setPos(0, 302)
        n = self._prune(self._editor_stub(3, [doc]))
        self.assertEqual(n, 2, "single trailing empty page removed, middle page kept")


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

    def test_swap_uses_pre_drag_origin(self):
        """When a card is dragged onto another, the dropped card's ORIGINAL
        position is lost once it sits over the target (mouseReleaseEvent fires
        on_dropped after the item has already moved). _swap_cards must therefore
        use the captured _drag_origin (set on mousePress) to move the target card
        to where the moved card came from — otherwise no visible swap happens."""
        from ui.a4_editor import A4Editor
        from ui.id_card_item import IDCardItem
        import inspect

        swap = inspect.getsource(A4Editor._swap_cards)
        self.assertIn("_drag_origin", swap,
                      "_swap_cards must use a._drag_origin (pre-drag position)")
        self.assertNotIn("pos_a = a.pos()", swap,
                         "pos_a must not be a.pos() — that is the already-dropped position")

        init = inspect.getsource(IDCardItem.__init__)
        self.assertIn("self._drag_origin = None", init,
                      "IDCardItem must initialize _drag_origin")

        press = inspect.getsource(IDCardItem.mousePressEvent)
        self.assertIn("self._drag_origin = self.pos()", press,
                      "mousePressEvent must capture the original position")

    def test_swap_keeps_index_in_sync(self):
        """After _swap_cards, each card's .index must equal its position in
        self.cards. When cards are swapped, the UI captures card_item.index and
        passes it to _replace_card for the double-click crop flow; a stale index
        makes the crop overwrite the WRONG card (the double-clicked card keeps its
        old image and the crop appears on the neighbour card)."""
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QPixmap
        app = QApplication.instance() or QApplication([])
        from ui.a4_editor import A4Editor

        ed = A4Editor()
        pm = QPixmap(120, 80)
        pm.fill()
        ed._place_card(pm)
        ed._place_card(pm)
        ed._place_card(pm)

        a, b = ed.cards[0], ed.cards[2]
        a._drag_origin = a.pos()
        ed._swap_cards(a, b)
        b._drag_origin = b.pos()
        ed._swap_cards(b, ed.cards[1])

        for item in ed.cards:
            self.assertEqual(item.index, ed.cards.index(item),
                             f"card index {item.index} != list position "
                             f"{ed.cards.index(item)} after swap")
            self.assertLess(item.index, len(ed.cards))


class TestSessionPersistence(unittest.TestCase):
    """Session must persist across app restarts until the user logs out manually.
    The startup restore must NOT destroy the saved session when the server's auth
    check returns an error (e.g. session_expired), otherwise the user is forced to
    log in again on every launch."""

    def test_check_auth_supports_suppress_expired(self):
        """check_auth must accept suppress_expired and forward it as
        _fire_session_expired so a startup check cannot wipe the saved session."""
        from core import api_client
        import unittest.mock as mock
        with mock.patch.object(api_client, "_request", return_value=("data", None)) as m:
            api_client.check_auth()
            self.assertIs(m.call_args.kwargs.get("_fire_session_expired"), True,
                          "default session check should still fire the expired callback")
        with mock.patch.object(api_client, "_request", return_value=("data", None)) as m:
            api_client.check_auth(suppress_expired=True)
            self.assertIs(m.call_args.kwargs.get("_fire_session_expired"), False,
                          "restore-time check must NOT fire the expired callback")

    def test_api_check_auth_passes_suppress(self):
        """database.api_check_auth must forward suppress_expired to api_client."""
        from core import database
        import unittest.mock as mock
        with mock.patch("core.api_client.check_auth", return_value=("data", None)) as m:
            database.api_check_auth()
            self.assertEqual(m.call_args[1].get("suppress_expired"), False)
        with mock.patch("core.api_client.check_auth", return_value=("data", None)) as m:
            database.api_check_auth(suppress_expired=True)
            self.assertEqual(m.call_args[1].get("suppress_expired"), True)

    def test_restore_uses_suppress_expired(self):
        """Session restore must call api_check_auth with suppress_expired=True."""
        from ui.main_window import MainWindow
        import inspect
        src = inspect.getsource(MainWindow._verify_session_worker)
        self.assertIn("api_check_auth(suppress_expired=True)", src,
                      "restore must suppress the expired callback to keep the session")

    def test_save_session_creates_missing_data_dir(self):
        """_save_session must create DATA_DIR if it does not exist, otherwise the
        session write silently fails in the frozen EXE (API mode) where init_db is
        never called and the data folder is absent -> user must re-login every launch."""
        import tempfile, os, json, unittest.mock as mock
        from core import database
        from ui.main_window import MainWindow
        tmp = tempfile.mkdtemp()
        try:
            target = os.path.join(tmp, "data_does_not_exist")
            self.assertFalse(os.path.exists(target))
            fake = mock.Mock()
            fake._username = "u"
            fake._display_name = "d"
            fake._is_admin = False
            fake._subscription_required = False
            fake._use_api = True
            fake._session_path.side_effect = lambda: os.path.join(target, "session.json")
            with mock.patch("core.api_client.get_token", return_value="realtoken123"), \
                 mock.patch("core.api_client.get_token_id", return_value="tid"), \
                 mock.patch.object(database, "DATA_DIR", target):
                MainWindow._save_session(fake)
            self.assertTrue(os.path.isdir(target), "DATA_DIR must be created before saving")
            self.assertTrue(os.path.isfile(os.path.join(target, "session.json")))
            with open(os.path.join(target, "session.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["token"], "realtoken123")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestSubscriptionGate(unittest.TestCase):
    """While the post-login verification (subscription state) is pending, no
    section can be wrongly blocked by the subscription dialog."""

    def _fake(self):
        import unittest.mock as mock
        fake = mock.Mock()
        fake._is_admin = False
        fake._sub_pending = True
        fake._subscription_required = True
        fake._use_api = True
        fake._username = "u"
        fake._compute_subscription_days = mock.Mock(return_value=0)
        return fake

    def test_section_access_allowed_while_sub_pending(self):
        import unittest.mock as mock
        from ui.main_window import MainWindow
        fake = self._fake()
        self.assertTrue(MainWindow._check_section_access(fake))
        with mock.patch("ui.main_window.QMessageBox") as box:
            fake._sub_pending = False
            self.assertFalse(MainWindow._check_section_access(fake))
            box.warning.assert_called()

    def test_print_require_allowed_while_sub_pending(self):
        import unittest.mock as mock
        from ui.main_window import MainWindow
        fake = self._fake()
        self.assertTrue(MainWindow._require_subscription(fake))
        with mock.patch("ui.main_window.QMessageBox") as box:
            fake._sub_pending = False
            self.assertFalse(MainWindow._require_subscription(fake))
            box.warning.assert_called()

    def test_warning_skipped_while_sub_pending(self):
        from ui.main_window import MainWindow
        import unittest.mock as mock
        fake = self._fake()
        with mock.patch("ui.main_window.QMessageBox") as box:
            MainWindow._show_subscription_warning(fake)
            box.information.assert_not_called()


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

    def test_restore_interior_alpha_fills_enclosed_hole(self):
        img = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
        pix = img.load()
        for y in range(40, 160):
            for x in range(40, 160):
                if 90 <= x <= 110 and 90 <= y <= 110:
                    pix[x, y] = (0, 0, 0, 0)  # interior hole (white shirt)
                else:
                    pix[x, y] = (255, 255, 255, 255)
        restored = restore_interior_alpha(img)
        a = np.array(restored.getchannel("A"))
        self.assertEqual(a[100, 100], 255)  # interior hole filled
        self.assertEqual(a[10, 10], 0)      # external background stays transparent

    def test_restore_interior_alpha_no_alpha_returns_same(self):
        img = Image.new("RGB", (100, 100), (255, 0, 0))
        restored = restore_interior_alpha(img)
        self.assertEqual(restored.mode, "RGB")

    def test_restore_interior_alpha_background_touching_border_stays(self):
        img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        pix = img.load()
        for y in range(30, 70):
            for x in range(30, 70):
                pix[x, y] = (255, 255, 255, 255)
        restored = restore_interior_alpha(img)
        a = np.array(restored.getchannel("A"))
        self.assertEqual(a[5, 5], 0)        # border background not filled
        self.assertEqual(a[50, 50], 255)    # subject kept

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

    def test_enhance_auto_remini_rgb_same_size(self):
        img = Image.new("RGB", (200, 300), color=(120, 130, 140))
        result = enhance_auto_remini(img)
        self.assertEqual(result.size, (200, 300))
        self.assertEqual(result.mode, "RGB")

    def test_enhance_auto_remini_rgba_alpha_preserved(self):
        img = Image.new("RGBA", (200, 300), (120, 130, 140, 200))
        result = enhance_auto_remini(img)
        self.assertEqual(result.mode, "RGBA")
        self.assertEqual(result.size, (200, 300))
        np.testing.assert_array_equal(
            np.array(result.getchannel("A")),
            np.full((300, 200), 200, dtype=np.uint8))

    def test_enhance_auto_remini_black_does_not_crash(self):
        img = Image.new("RGB", (100, 100), color=(0, 0, 0))
        result = enhance_auto_remini(img)
        self.assertEqual(result.size, (100, 100))

    def test_enhance_auto_remini_white_does_not_crash(self):
        img = Image.new("RGB", (100, 100), color=(255, 255, 255))
        result = enhance_auto_remini(img)
        self.assertEqual(result.size, (100, 100))

    def test_enhance_auto_remini_small_image_does_not_crash(self):
        img = Image.new("RGB", (10, 10), color=(100, 120, 140))
        result = enhance_auto_remini(img)
        self.assertEqual(result.size, (10, 10))

    def test_enhance_auto_remini_actually_modifies_pixels(self):
        img = Image.new("RGB", (100, 100), color=(80, 90, 100))
        result = enhance_auto_remini(img)
        self.assertFalse(np.array_equal(np.array(img), np.array(result)))


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


class TestMaxDevicesDefault(unittest.TestCase):
    def test_get_max_devices_returns_1_when_none(self):
        from backend.database import get_max_devices
        import unittest.mock as mock
        with mock.patch("backend.database._conn") as mock_conn:
            mock_cur = mock.MagicMock()
            mock_conn.return_value.cursor.return_value = mock_cur
            mock_cur.fetchone.return_value = (None,)
            result = get_max_devices("nonexistent")
            self.assertEqual(result, 1)

    def test_get_max_devices_returns_value_when_set(self):
        from backend.database import get_max_devices
        import unittest.mock as mock
        with mock.patch("backend.database._conn") as mock_conn:
            mock_cur = mock.MagicMock()
            mock_conn.return_value.cursor.return_value = mock_cur
            mock_cur.fetchone.return_value = (2,)
            result = get_max_devices("testuser")
            self.assertEqual(result, 2)


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


class TestServerImport(unittest.TestCase):
    """Server-side import should not fail (no orphan imports)."""

    def test_backend_app_imports_cleanly(self):
        """Importing backend.app must not raise ImportError."""
        try:
            from backend.app import app
            self.assertIsNotNone(app)
        except ImportError as e:
            self.fail(f"backend.app raised ImportError: {e}")


class TestAppVersion(unittest.TestCase):
    """Update-check feature: server endpoint, client function, UI method."""

    def test_server_endpoint_returns_version_and_url(self):
        """GET /api/app/version returns version and download_url."""
        from backend.app import app as flask_app
        with flask_app.test_client() as client:
            resp = client.get("/api/app/version")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertIn("version", data)
            self.assertIn("download_url", data)
            self.assertIsInstance(data["version"], str)
            self.assertIsInstance(data["download_url"], str)



class TestPhotoItem(unittest.TestCase):
    """PhotoItem double-click should open PhotoCropDialog."""

    def test_photo_item_has_mouse_double_click(self):
        """PhotoItem must have mouseDoubleClickEvent to handle double-click directly
        (because PhotoItem.mousePressEvent calls super() which starts a move state
        machine, preventing the view's mouseDoubleClickEvent from being called)."""
        from ui.photo_editor import PhotoItem, PhotoGraphicsView
        self.assertTrue(hasattr(PhotoItem, "mouseDoubleClickEvent"),
                        "PhotoItem needs its own mouseDoubleClickEvent")
        self.assertTrue(callable(PhotoItem.mouseDoubleClickEvent))

    def test_photo_graphics_view_double_click_forwards_to_super(self):
        """PhotoGraphicsView.mouseDoubleClickEvent must NOT contain dialog logic
        (only calls super) to avoid double-opening when PhotoItem handles it."""
        from ui.photo_editor import PhotoGraphicsView
        import inspect
        src = inspect.getsource(PhotoGraphicsView.mouseDoubleClickEvent)
        self.assertNotIn("PhotoCropDialog", src,
                         "PhotoGraphicsView must not open dialog — PhotoItem handles it")


class TestPhotoPrintClean(unittest.TestCase):
    """Printing/saving must render ONLY photos on the paper — never guide boxes."""

    def test_print_paths_hide_all_non_photo_items(self):
        import inspect
        from ui.photo_editor import PhotoEditor
        for name in ("_print_page", "_save_pdf"):
            src = inspect.getsource(getattr(PhotoEditor, name))
            self.assertIn("not isinstance(it, PhotoItem)", src,
                          f"{name} must hide every non-photo item before printing")


class TestScannerQuality(unittest.TestCase):
    """Scanner section must render/print/save at 300 DPI so imported images
    keep full detail — not the old low-res 72 DPI (595x842) scene."""

    def test_scene_is_high_resolution(self):
        from ui.scanner_page import A4_W, A4_H
        self.assertGreaterEqual(A4_W, 2400, "scene width must be ~300dpi A4")
        self.assertGreaterEqual(A4_H, 3400, "scene height must be ~300dpi A4")
        # 300 dpi A4 = 2480 x 3508
        self.assertAlmostEqual(A4_W / 210.0, 300 / 25.4, delta=2)
        self.assertAlmostEqual(A4_H / 297.0, 300 / 25.4, delta=2)

    def test_save_pdf_uses_high_resolution(self):
        import inspect
        from ui.scanner_page import ScannerPage
        src = inspect.getsource(ScannerPage._save_pdf)
        self.assertIn("QPrinter.HighResolution", src,
                      "_save_pdf must print at HighResolution for sharp PDFs")


class TestSliderGroup(unittest.TestCase):
    """Sliders in the photo editor panel should be vertical for visibility."""

    def test_slider_orientation_is_vertical(self):
        """_SliderGroup.__init__ must use Qt.Vertical for its slider."""
        from ui.photo_editor import _SliderGroup
        import inspect
        src = inspect.getsource(_SliderGroup.__init__)
        self.assertIn("Qt.Vertical", src,
                      "Slider must be vertical — Qt.Vertical not found in __init__")


class TestAutoCardStraighten(unittest.TestCase):
    """القاعدة الثابتة للتلقائي: تُعدَّل الميلان تلقائياً من زوايا البطاقة
    الأربع الحقيقية (مثل النقر اليدوي لكن تلقائياً) ثم تُلصق بزوايا مستطيل A4
    — لا قصّ مباشر على صورة مائلة أبداً، حتى بلا مضلع YOLO (قناع)."""

    @staticmethod
    def _tilted_card(tilt_deg=20, card_w=856, card_h=540,
                     canvas=(1100, 750), cx=560, cy=380, seed=0):
        rng = np.random.default_rng(seed)
        base = rng.integers(30, 90, (card_h, card_w, 3), dtype=np.uint8)
        base[20:-20, 20:-20] = rng.integers(160, 235, (card_h - 40, card_w - 40, 3), dtype=np.uint8)
        cv2.rectangle(base, (10, 10), (card_w - 11, card_h - 11), (20, 20, 20), 4)
        a = np.radians(tilt_deg); ca, sa = np.cos(a), np.sin(a)
        src = np.array([[0, 0], [card_w - 1, 0], [card_w - 1, card_h - 1], [0, card_h - 1]], np.float32)
        dst = np.array([(cx + (x - card_w / 2) * ca - (y - card_h / 2) * sa,
                         cy + (x - card_w / 2) * sa + (y - card_h / 2) * ca)
                        for x, y in src], np.float32)
        M = cv2.getPerspectiveTransform(src, dst)
        canvas_arr = np.full((canvas[1], canvas[0], 3), 245, np.uint8)
        warped = cv2.warpPerspective(base, M, (canvas[0], canvas[1]))
        ys, xs = np.nonzero(warped.sum(axis=2) > 100)
        canvas_arr[ys, xs] = warped[ys, xs]
        return Image.fromarray(canvas_arr)

    def test_no_mask_content_path_straightens(self):
        """صورة مائلة بلا قناع YOLO → تُفرد تلقائياً (أو تُرجع None صريحاً،
        لا صورة مائلة/مقلوبة أبداً). الإخراج عندما ينجح يُلصق بمستطيل A4."""
        from core.local_yolo import cut_card_local
        for tilt in (0, 15, 30, 45):
            out = cut_card_local(self._tilted_card(tilt), dpi=300)
            if out is None:
                continue  # لا كشف — يُعاملها الواجهة عبر مسارات أخرى بأمان
            w, h = out.size
            self.assertEqual((w, h), (1122, 649),
                             f"tilt={tilt} not glued to A4 slot (95x55 @300dpi)")

    def test_corner_ordering_is_landscape_stable(self):
        """ترتيب الزوايا الأربع يبقى عَرْضياً سليماً بأي ميلان (لا انقلاب)."""
        from core.local_yolo import order_card_corners

        def rot_corners(cw, ch, deg, cx, cy):
            a = np.radians(deg); ca, sa = np.cos(a), np.sin(a)
            return [(cx + (x - cw / 2) * ca - (y - ch / 2) * sa,
                     cy + (x - cw / 2) * sa + (y - ch / 2) * ca)
                    for x, y in ((0, 0), (cw, 0), (cw, ch), (0, ch))]

        for tilt in (0, 15, 30, 45, 60, 75, 88):
            pts = rot_corners(856, 540, tilt, 400, 300)
            tl, tr, br, bl = order_card_corners(pts)
            wtop = np.hypot(tr[0] - tl[0], tr[1] - tl[1])
            hside = np.hypot(br[0] - tr[0], br[1] - tr[1])
            self.assertGreater(wtop, hside,
                               f"tilt={tilt}: top edge must be the long (landscape) side")
            # الاتجاهية غير معكوسة: det > 0 (CCW)
            d = np.cross(tr - tl, br - tl)
            self.assertGreater(d, 0, f"tilt={tilt}: mirrored order")


if __name__ == "__main__":
    unittest.main(verbosity=2)