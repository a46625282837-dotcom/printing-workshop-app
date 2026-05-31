import logging
from PIL import Image

logger = logging.getLogger(__name__)

TARGET_DPI = 600


def photo_px_size(w_mm, h_mm, dpi=TARGET_DPI):
    return int(w_mm / 25.4 * dpi), int(h_mm / 25.4 * dpi)


def _remove_bg_ai(pil_image: Image.Image) -> Image.Image:
    from rembg import remove as _ai_remove
    result = _ai_remove(pil_image)
    logger.info("تمت إزالة الخلفية عبر AI (%s)", pil_image.size)
    return result


def _remove_bg_grabcut(pil_image: Image.Image) -> Image.Image:
    import cv2
    import numpy as np
    img = cv2.cvtColor(np.array(pil_image.convert("RGB")), cv2.COLOR_RGB2BGR)
    h, w = img.shape[:2]
    margin_x = max(1, int(w * 0.02))
    margin_y = max(1, int(h * 0.02))
    rect = (margin_x, margin_y, w - 2 * margin_x, h - 2 * margin_y)
    mask = np.zeros((h, w), np.uint8)
    bgd = np.zeros((1, 65), np.float64)
    fgd = np.zeros((1, 65), np.float64)
    cv2.grabCut(img, mask, rect, bgd, fgd, 2, cv2.GC_INIT_WITH_RECT)
    fg = (mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD)
    mask2 = fg.astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask2 = cv2.morphologyEx(mask2, cv2.MORPH_CLOSE, kernel)
    result_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    result_pil = Image.fromarray(result_rgb).convert("RGBA")
    result_pil.putalpha(Image.fromarray(mask2, mode="L"))
    logger.info("تمت إزالة الخلفية عبر grabCut (%s)", pil_image.size)
    return result_pil


def remove_background(pil_image: Image.Image) -> Image.Image:
    try:
        from rembg import remove as _ai_remove
        return _remove_bg_ai(pil_image)
    except Exception:
        try:
            return _remove_bg_grabcut(pil_image)
        except Exception as e:
            logger.error("فشل إزالة الخلفية", exc_info=True)
            return pil_image.convert("RGBA")


def auto_crop_subject(pil_image: Image.Image, margin_ratio=0.15) -> Image.Image:
    if pil_image.mode != "RGBA":
        return pil_image
    alpha = pil_image.getchannel("A")
    bbox = alpha.getbbox()
    if bbox is None:
        return pil_image
    left, upper, right, lower = bbox
    bw, bh = right - left, lower - upper
    margin_x = max(1, int(bw * margin_ratio))
    margin_y = max(1, int(bh * margin_ratio))
    new_left = max(0, left - margin_x)
    new_upper = max(0, upper - margin_y)
    new_right = min(pil_image.width, right + margin_x)
    new_lower = min(pil_image.height, lower + margin_y)
    logger.info("تم اقتصاص الصورة إلى (%d×%d) حول الموضوع", new_right - new_left, new_lower - new_upper)
    return pil_image.crop((new_left, new_upper, new_right, new_lower))


def resize_to_photo_size(pil_image: Image.Image, w_mm: float, h_mm: float, dpi=TARGET_DPI) -> Image.Image:
    target = photo_px_size(w_mm, h_mm, dpi)
    if pil_image.size == target:
        return pil_image
    if pil_image.width > target[0] or pil_image.height > target[1]:
        pil_image.thumbnail(target, Image.LANCZOS)
        logger.info("تم تصغير حجم الصورة إلى %s (%d×%d مم)", pil_image.size, w_mm, h_mm)
    else:
        logger.info("الصورة أصغر من الهدف (%s)، الإبقاء على الدقة الأصلية", pil_image.size)
    return pil_image