import logging
from PIL import Image

logger = logging.getLogger(__name__)

CARD_WIDTH_MM = 95
CARD_HEIGHT_MM = 55
TARGET_DPI = 600


def card_px_size(dpi: int = TARGET_DPI):
    w = int(CARD_WIDTH_MM / 25.4 * dpi)
    h = int(CARD_HEIGHT_MM / 25.4 * dpi)
    return w, h


def resize_to_card(image: Image.Image, dpi: int = TARGET_DPI) -> Image.Image:
    target = card_px_size(dpi)
    if image.size[0] <= target[0] and image.size[1] <= target[1]:
        return image
    image.thumbnail(target, Image.LANCZOS)
    logger.info("تم تغيير الحجم إلى %s", image.size)
    return image


