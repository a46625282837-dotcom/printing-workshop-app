"""قصّ الهوية باحترافية: كشف الأطراف + تصحيح المنظور + تجهيز للوحة A4.

الخطوات:
  1) كشف أطراف البطاقة بدقة شديدة (عدة استراتيجيات: Canny + contour).
  2) تحويل المنظور (Warp Perspective) لجعل البطاقة مسطحة بزوايا حادة.
  3) ضبط الأبعاد لتتناسب مع مستطيل ورقة A4 (95×55mm) مع ملء الفراغات.
"""
import logging

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

CARD_W_MM = 95
CARD_H_MM = 55
TARGET_DPI = 600


def _target_px(dpi=TARGET_DPI):
    w = int(CARD_W_MM / 25.4 * dpi)
    h = int(CARD_H_MM / 25.4 * dpi)
    return w, h


# --------------------------------------------------------------------------
# 1) كشف أطراف البطاقة
# --------------------------------------------------------------------------
def _find_card_contour(img_bgr):
    """يبحث عن أكبر مستطيل يشبه البطاقة في الصورة.

    يستخدم عدة مستويات من Canny مع Different threshold combinations
    لضمان العثور على الحواف بغض النظر عن الإضاءة أو لون الخلفية.
    """
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    H, W = gray.shape
    img_area = W * H

    # حاول عدة استراتيجيات كشف الحواف
    strategies = [
        # (blur, canny_lo, canny_hi, dilate_iter)
        (blur, 30, 100, 2),
        (blur, 50, 150, 2),
        (blur, 20, 80, 3),
        (cv2.medianBlur(gray, 5), 30, 100, 2),
        (cv2.bilateralFilter(gray, 11, 17, 17), 30, 100, 2),
    ]

    best_quad = None
    best_area = 0

    for src, lo, hi, dil_iter in strategies:
        edged = cv2.Canny(src, lo, hi)
        kernel = np.ones((3, 3), np.uint8)
        edged = cv2.dilate(edged, kernel, iterations=dil_iter)
        edged = cv2.morphologyEx(edged, cv2.MORPH_CLOSE, kernel, iterations=2)

        cnts, _ = cv2.findContours(edged, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        cnts = sorted(cnts, key=cv2.contourArea, reverse=True)[:10]

        for c in cnts:
            area = cv2.contourArea(c)
            if area < img_area * 0.05 or area > img_area * 0.98:
                continue
            peri = cv2.arcLength(c, True)
            for eps in (0.01, 0.015, 0.02, 0.025, 0.03, 0.04):
                approx = cv2.approxPolyDP(c, eps * peri, True)
                if len(approx) == 4:
                    quad = approx.reshape(4, 2).astype("float32")
                    xs, ys = quad[:, 0], quad[:, 1]
                    bw = float(xs.max() - xs.min())
                    bh = float(ys.max() - ys.min())
                    if bw < 30 or bh < 30:
                        continue
                    asp = bw / bh
                    if not ((1.1 <= asp <= 2.8) or (0.35 <= asp <= 0.9)):
                        continue
                    if area > best_area:
                        best_area = area
                        best_quad = quad
                    break

        if best_quad is not None and best_area > img_area * 0.1:
            break

    return best_quad


def _order_corners(pts):
    """ترتيب الزوايا: أعلى-يسار، أعلى-يمين، أسفل-يمين، أسفل-يسار."""
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


# --------------------------------------------------------------------------
# 2) تصحيح المنظور
# --------------------------------------------------------------------------
def _warp_perspective(img_bgr, quad):
    """تحويل المنظور: يحوّل الرباعي إلى مستطيل مسطح."""
    rect = _order_corners(quad)
    (tl, tr, br, bl) = rect

    w_top = np.linalg.norm(tr - tl)
    w_bot = np.linalg.norm(br - bl)
    max_w = int(max(w_top, w_bot))

    h_right = np.linalg.norm(tr - br)
    h_left = np.linalg.norm(tl - bl)
    max_h = int(max(h_right, h_left))

    if max_w < 30 or max_h < 30:
        return None

    dst = np.array([
        [0, 0],
        [max_w - 1, 0],
        [max_w - 1, max_h - 1],
        [0, max_h - 1],
    ], dtype="float32")

    M = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(img_bgr, M, (max_w, max_h),
                                 flags=cv2.INTER_CUBIC,
                                 borderMode=cv2.BORDER_REPLICATE)
    return warped


# --------------------------------------------------------------------------
# 3) ضبط الأبعاد للمستطيل في ورقة A4
# --------------------------------------------------------------------------
def _fit_to_card_slot(pil_image, dpi=TARGET_DPI):
    """تجهيز البطاقة لمستطيل A4 (95×55): دوران تلقائي + تعبئة كاملة بلا قصّ.

    القاعدة الدائمة: **لا قصّ لحواف البطاقة تحت أي ظرف** — تُملأ زوايا
    المستطيل بـ"تمديم متناسب طفيف" بدل قصّ الطرف الزائد.

    - إن كانت البطاقة عمودية ضمن مستطيل أفقي: تُدار 90° بملء أبيض.
    - ثم تُكبَّر بمقياس التعبئة (ارتفاع مثالي): أعلى/أسفل يتلامسان مع خط
      المستطيل تماماً كما السابق (خيار المستخدم "ممتاز").
    - ليمين/يسار: تمديم أفقي متناسب طفيف لملء العرض كاملاً (لا قصّ).
    - حارس: إن احتذت النسبة المطلوبة تشوّهاً كبيراً (أكثر من ~15%) نعود
      لـ Contain على خلفية بيضاء كي لا تتشوه ملامح الهوية.

    ملاحظة: تُطبَّق على جميع المسارات (محلي/حواف) — تعبئة كاملة.
    """
    target_w, target_h = _target_px(dpi)
    img = pil_image.convert("RGB")
    iw, ih = img.size

    # 1) الاتجاه الأفقي الصحيح: بطاقة عمودية في سلوت أفقي → أدرها 90° بملء أبيض
    if ih > iw and target_w > target_h:
        img = img.rotate(-90, expand=True, fillcolor=(255, 255, 255))
        iw, ih = img.size

    # 2) نسبة التمديد على كل محور لملء المستطيل بالكامل بلا قصّ
    sx = target_w / iw
    sy = target_h / ih
    ratio = max(sx, sy) / min(sx, sy) if min(sx, sy) > 0 else 1.0

    if 1.0 < ratio <= 1.15:
        # تمديد متناسب طفيف (يمين/يسار + أعلى/أسفل يلامسان خطوط المستطيل)
        img = img.resize((target_w, target_h), Image.LANCZOS)
        return img

    # 3) حارس التشوه الشديد: نلتزم بـ Contain على خلفية بيضاء (لا قصّ)
    scale = min(sx, sy)
    new_w = max(1, int(round(iw * scale)))
    new_h = max(1, int(round(ih * scale)))
    img = img.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new("RGB", (target_w, target_h), (255, 255, 255))
    left = (target_w - new_w) // 2
    top = (target_h - new_h) // 2
    canvas.paste(img, (left, top))
    return canvas


# --------------------------------------------------------------------------
# الدالة الرئيسية
# --------------------------------------------------------------------------
def cut_card(pil_image: Image.Image, dpi: int = TARGET_DPI) -> Image.Image:
    """قصّ الهوية باحترافية وتجهيزها لورقة A4.

    Pipeline كامل:
      1) كشف أطراف البطاقة (عدة استراتيجيات كشف حواف).
      2) تصحيح المنظور (Warp Perspective).
      3) ضبط الأبعاد للمستطيل (95×55mm) مع ملء الفراغات.

    Returns:
        PIL.Image بحجم (target_w × target_h) جاهز للإسقاط في ورقة A4.
    """
    try:
        img_bgr = cv2.cvtColor(np.array(pil_image.convert("RGB")), cv2.COLOR_RGB2BGR)

        # الخطوة 1: كشف الأطراف
        quad = _find_card_contour(img_bgr)
        if quad is None:
            logger.warning("لم يتم كشف أطراف البطاقة — محاولة rembg fallback")
            from core.id_extractor import extract_card
            extracted = extract_card(pil_image)
            return _fit_to_card_slot(extracted, dpi)

        # الخطوة 2: تصحيح المنظور
        warped = _warp_perspective(img_bgr, quad)
        if warped is None:
            logger.warning("تصحيح المنظور فشل — محاولة rembg fallback")
            from core.id_extractor import extract_card
            extracted = extract_card(pil_image)
            return _fit_to_card_slot(extracted, dpi)

        result = Image.fromarray(cv2.cvtColor(warped, cv2.COLOR_BGR2RGB))
        logger.info("تم قصّ الهوية — %dx%d", result.width, result.height)

        # الخطوة 3: ضبط الأبعاد
        return _fit_to_card_slot(result, dpi)

    except Exception as e:
        logger.error("فشل قصّ الهوية (%s) — rembg fallback", e)
        try:
            from core.id_extractor import extract_card
            extracted = extract_card(pil_image)
            return _fit_to_card_slot(extracted, dpi)
        except Exception:
            return pil_image.convert("RGB").resize(_target_px(dpi), Image.LANCZOS)
