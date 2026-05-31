import logging

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    HAS_ARABIC = True
except ImportError:
    HAS_ARABIC = False

logger = logging.getLogger(__name__)

_DEFAULT_FONT = "C:/Windows/Fonts/arial.ttf"


class PdfTextReplacer:
    def __init__(self, pdf_path, font_path=None):
        import fitz
        self.pdf_path = pdf_path
        self.font_path = font_path or _DEFAULT_FONT
        self.doc = fitz.open(pdf_path)

    def process_arabic(self, text):
        if not text or not HAS_ARABIC:
            return text
        try:
            reshaped = arabic_reshaper.reshape(text)
            return get_display(reshaped)
        except Exception as e:
            logger.warning("Arabic processing failed for '%s': %s", text[:20], e)
            return text

    def mask_rects(self, page_index, rects, fill=None):
        page = self.doc[page_index]
        for r in rects:
            page.add_redact_annot(r, fill=fill)
        page.apply_redactions()
        logger.info("Masked %d rect(s) on page %d", len(rects), page_index + 1)

    def insert_text(self, page_index, point, text, font_size, color=(0, 0, 0)):
        page = self.doc[page_index]
        processed = self.process_arabic(text)
        try:
            page.insert_text(
                point, processed,
                fontfile=self.font_path,
                fontsize=max(font_size, 4),
                color=color,
            )
            logger.info("Inserted '%s' on page %d at (%.1f, %.1f)",
                         text[:30], page_index + 1, point.x, point.y)
        except Exception as e:
            logger.error("Failed to insert text on page %d: %s", page_index + 1, e)

    def replace(self, page_index, old_rects, new_text, position,
                font_size=None, bg_color=None):
        if font_size is None and old_rects:
            font_size = old_rects[0].y1 - old_rects[0].y0
        self.mask_rects(page_index, old_rects, fill=bg_color)
        import fitz
        self.insert_text(page_index, fitz.Point(*position), new_text,
                         font_size or 10)

    def save(self, output_path):
        self.doc.save(output_path, incremental=False, deflate=True)
        self.doc.close()
        logger.info("Saved modified PDF with rebuilt XREF: %s", output_path)
