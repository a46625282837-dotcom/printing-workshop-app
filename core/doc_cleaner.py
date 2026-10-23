"""إزالة خلفية وثائق A4 بطريقة GrabCut (كشف شكل الورقة).

النظام: OpenCV GrabCut يكتشف منطقة الورقة بدقة ثم يحذف المحيط مع الحفاظ
التام على ألوان الوثيقة وجميع محتوياتها (نص، صور، توقيعات).

لا rembg، لا نماذج أشخاص — فقط كشف هندسي للورقة من خلال:
1) تحديد منطقة الورقة التقريبية في وسط الصورة
2) GrabCut يُحسّن حدود الورقة بدقة
3) القناع الناتج يُستخدم لإزالة ما حول الورقة فقط
4) حماية: إن فشل كل شيء تُعاد الصورة كما هي بدون أي تغيير.
"""
import logging
import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

_BG_ACCENTUATE = 1.01

_MAX_GRABCUT_PX = 700  # حد أقصى لأطول ضلع أثناء GrabCut لسرعة عالية


def _isolate_paper_grabcut(img_rgb: np.ndarray, margin_ratio: float = 0.03):
    """كشف ورقة الوثيقة عبر GrabCut.

    يُرجع قناع (0/255) لمنطقة الورقة، أو None إذا فشل.
    يُنفَّذ GrabCut على نسخة مصغّرة (max 700px) ثم يُرفع القناع للحجم الأصلي
    ليكون سريعاً حتى مع صور الماسح الضوئي عالية الدقة.
    """
    orig_h, orig_w = img_rgb.shape[:2]

    # ── 0) تصغير العمل للحصول على سرعة عالية ──
    scale = 1.0
    work = img_rgb
    if max(orig_h, orig_w) > _MAX_GRABCUT_PX:
        scale = _MAX_GRABCUT_PX / float(max(orig_h, orig_w))
        tw = max(10, int(orig_w * scale))
        th = max(10, int(orig_h * scale))
        work = cv2.resize(img_rgb, (tw, th), interpolation=cv2.INTER_AREA)

    h, w = work.shape[:2]
    gray = cv2.cvtColor(work, cv2.COLOR_RGB2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)

    # ── 1) حدود تقريبية للورقة من العتبة الأوتسي ──
    _, otsu = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kern = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    otsu = cv2.morphologyEx(otsu, cv2.MORPH_CLOSE, kern)
    otsu = cv2.morphologyEx(otsu, cv2.MORPH_OPEN, kern)

    # أكبر مكوّن متصل فاتح = الورقة التقريبية
    num, labels, stats, _ = cv2.connectedComponentsWithStats(otsu, 8)
    best = None
    for i in range(1, num):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < h * w * 0.05:
            continue
        if best is None or area > int(stats[best, cv2.CC_STAT_AREA]):
            best = i
    if best is None:
        return None

    paper_mask = (labels == best).astype(np.uint8)

    # ── 2) محيط تقريبي للورقة ──
    contours, _ = cv2.findContours(paper_mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    paper_cnt = max(contours, key=cv2.contourArea)
    if cv2.contourArea(paper_cnt) < h * w * 0.05:
        return None

    # ε‑approximation إلى رباعي
    peri = cv2.arcLength(paper_cnt, True)
    approx = cv2.approxPolyDP(paper_cnt, 0.02 * peri, True)
    if len(approx) != 4:
        approx = paper_cnt  # fallback: الكنتور الأصلي

    # حساب المربع التغطية
    rx, ry, rw, rh = cv2.boundingRect(approx)
    mx = int(rw * margin_ratio)
    my = int(rh * margin_ratio)
    rx0 = max(0, rx - mx)
    ry0 = max(0, ry - my)
    rx1 = min(w, rx + rw + mx)
    ry1 = min(h, ry + rh + my)

    # ── 3) GrabCut على المنطقة المحددة ──
    gc_mask = np.zeros((h, w), np.uint8)
    gc_mask[:] = cv2.GC_BGD
    gc_mask[ry0:ry1, rx0:rx1] = cv2.GC_PR_FGD

    # الكنتور الرباعي → مقدمة مؤكدة
    cv2.fillConvexPoly(gc_mask, approx.reshape(-1, 2), cv2.GC_FGD)

    bgd_model = np.zeros((1, 65), np.float64)
    fgd_model = np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(work, gc_mask, None, bgd_model, fgd_model, 5,
                     cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        logger.warning("grabcut فشل — محاولة بدون mask مبدئي")
        return None

    fg_mask = ((gc_mask == cv2.GC_FGD) | (gc_mask == cv2.GC_PR_FGD)).astype(
        np.uint8) * 255

    # ── 4) تنظيف القناع ──
    # ملء الثقوب الصغيرة داخل الورقة
    kern_close = cv2.getStructuringElement(cv2.MORPH_RECT, (11, 11))
    fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kern_close)
    fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kern_close)

    # إزالة الرقائق الصغيرة خارج الورقة
    num2, lab2, st2, _ = cv2.connectedComponentsWithStats(fg_mask, 8)
    total = h * w
    cleaned = np.zeros_like(fg_mask)
    for j in range(1, num2):
        if st2[j, cv2.CC_STAT_AREA] >= total * 0.02:
            cleaned[lab2 == j] = 255
    fg_mask = cleaned

    # التحقق: هل القناع يغطي جزءاً كبيراً من الصورة؟
    cover = float(fg_mask.sum()) / (255.0 * total)
    if cover < 0.03 or cover > 0.98:
        logger.info("grabcut: تغطية %.1f%% — مرفوضة", cover * 100)
        return None

    # feather على الحدود
    alpha = cv2.GaussianBlur(fg_mask.astype(np.float32), (0, 0), 1.5)
    alpha = np.clip(alpha, 0, 255)

    # ── 5) رفع القناع إلى حجم الصورة الأصلي ──
    if scale != 1.0:
        alpha = cv2.resize(alpha, (orig_w, orig_h), interpolation=cv2.INTER_LANCZOS4)
    return alpha / 255.0


def remove_document_background(pil_image: Image.Image) -> Image.Image:
    """إزالة خلفية وثيقة A4 مع الحفاظ على ألوان محتواها.

    - خلفية الصورة تُحوَّل لأبيض بالكامل.
    - محتوى الوثيقة يبقى بألوانه الأصلية.
    - عند الفشل: تُعاد الصورة كما هي (لا تغيير مطلقاً).
    """
    img_pil = pil_image.convert("RGB")
    arr = np.array(img_pil)
    h, w = arr.shape[:2]

    alpha = _isolate_paper_grabcut(arr)
    if alpha is None:
        logger.info("doc_cleaner: فشل كشف الورقة — إرجاع الصورة كما هي")
        return img_pil

    # تركيب على أبيض
    white = np.full_like(arr, 255, dtype=np.uint8)
    alpha_3 = alpha[..., None]
    out = (arr.astype(np.float32) * alpha_3 +
           white.astype(np.float32) * (1.0 - alpha_3))
    out = np.clip(out, 0, 255).astype(np.uint8)

    cover = float(alpha.sum()) / (255.0 * h * w)
    logger.info("doc_cleaner: تم حذف الخلفية (تغطية الورقة %.0f%%) → %s",
                cover * 100, out.shape)
    return Image.fromarray(out, "RGB")


# ============================================================
# دالة مؤقتة: قصّ وفرد وثائق A4 تلقائياً عبر OpenCV (بدون ذكاء اصطناعي)
# ============================================================

_A4_W_MM = 210.0
_A4_H_MM = 297.0
_MAX_EDGE_PX = 1600  # أطول ضلع أثناء كشف الحواف (سرعة) ثم تُرفع الأركان للأصل


def order_corners_tl_tr_br_bl(pts):
    """ترتيب الأركان الأربعة بدقة: أعلى يسار، أعلى يمين، أسفل يمين، أسفل يسار."""
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    sums = pts.sum(axis=1)                # x + y
    diffs = np.diff(pts, axis=1).ravel()  # y - x
    tl = pts[int(np.argmin(sums))]
    br = pts[int(np.argmax(sums))]
    tr = pts[int(np.argmin(diffs))]
    bl = pts[int(np.argmax(diffs))]
    return np.array([tl, tr, br, bl], dtype=np.float32)


def _best_quad_from_contours(work_img):
    """أكبر رباعي مغلق يمثّل ورقة A4 عبر الحواف (Canny). إحداثيات صورة العمل."""
    h, w = work_img.shape[:2]
    gray = cv2.cvtColor(work_img, cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 50, 150)
    kern = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    edges = cv2.dilate(edges, kern)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    img_area = float(h * w)
    candidates = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < img_area * 0.10:
            continue
        peri = cv2.arcLength(cnt, True)
        if peri <= 0:
            continue
        approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
        if len(approx) != 4:
            continue
        if not cv2.isContourConvex(approx):
            continue
        candidates.append((area, approx.reshape(4, 2).astype(np.float32)))
    if not candidates:
        return None
    return max(candidates, key=lambda t: t[0])[1]


def _best_quad_from_mask(mask):
    """رباعي أكبر كتلة للورقة من قناع GrabCut (approx أو minAreaRect)."""
    binary = ((np.asarray(mask) > 0.5).astype(np.uint8)) * 255
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE,
                              cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7)))
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    cnt = max(contours, key=cv2.contourArea)
    peri = cv2.arcLength(cnt, True)
    if peri <= 0:
        return None
    approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
    if len(approx) == 4:
        return approx.reshape(4, 2).astype(np.float32)
    rect = cv2.minAreaRect(cnt)
    return cv2.boxPoints(rect).astype(np.float32)


def _best_quad_from_otsu(work_img):
    """أكبر كتلة محتوى (غير بيضاء) تُمثّل حدود الورقة على خلفية بيضاء.

    يُدمج نص/رسومات الوثيقة في كتلة واحدة كبيرة عبر إغلاق مورفولوجي، ثم
    رباعيها بـ minAreaRect — منقذ للحالات التي تختفي فيها حواف الورقة.
    """
    gray = cv2.cvtColor(work_img, cv2.COLOR_RGB2GRAY)
    _, binv = cv2.threshold(gray, 0, 255,
                            cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if float(binv.sum()) < 0.5:
        return None
    big = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25))
    merged = cv2.morphologyEx(binv, cv2.MORPH_CLOSE, big)
    contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    cnt = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(cnt)
    h, w = work_img.shape[:2]
    if area < (h * w) * 0.08:
        return None
    rect = cv2.minAreaRect(cnt)
    return cv2.boxPoints(rect).astype(np.float32)


def _warp_ordered_to_a4(img_rgb: np.ndarray, ordered, dpi: int) -> Image.Image:
    """يفرد رباعياً (بإحداثيات الصورة الأصلية) إلى مقاس A4 القياسي الكامل.

    إحداثيات الوجهة (dst) تغطّي حدود صفحة A4 القصوى دائماً: عمودية
    2480×3508 أو عرضية 3508×2480 (عند 300dpi) — فتملأ الورقة بلا فراغات.
    """
    tl, tr, br, bl = ordered
    quad_w = max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl))
    quad_h = max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr))
    if quad_w < 10 or quad_h < 10:
        return None
    px_w = int(round(_A4_W_MM * dpi / 25.4))
    px_h = int(round(_A4_H_MM * dpi / 25.4))
    portrait = quad_h >= quad_w
    dst_w, dst_h = (px_w, px_h) if portrait else (px_h, px_w)
    dst = np.array([[0, 0], [dst_w - 1, 0],
                    [dst_w - 1, dst_h - 1], [0, dst_h - 1]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(ordered, dst)
    warped = cv2.warpPerspective(img_rgb, m, (dst_w, dst_h),
                                 flags=cv2.INTER_CUBIC)
    logger.info("warp_a4: فُرد إلى مقاس A4 الكامل (%s) → %s",
                "عمودي" if portrait else "عرضي", warped.shape)
    return Image.fromarray(warped, "RGB")


def extract_document_to_a4(pil_image: Image.Image, dpi: int = 300) -> Image.Image:
    """إزالة الخلفية بالكامل ثم فرد الوثيقة لتملأ ورقة A4 بالكامل.

    1) GrabCut يستخرج قناع الورقة + حدودها (رباعيها) من الصورة الخام.
    2) يُبنى المحتوى النظيف: ما حول الوثيقة يتحول لأبيض ناصع (لا خلفية قديمة).
    3) الرباعي يُفرد إلى مقاس A4 القياسي الكامل (dst = حدود الصفحة 2480×3508)
       من اللوحة النظيفة — فلا تظهر الخلفية المحيطة إطلاقاً ولا تبقى فراغات.

    عند تعذّر كشف ورقة تُعاد الصورة كما هي (بلا تغيير).
    """
    img_rgb = np.array(pil_image.convert("RGB"))
    h, w = img_rgb.shape[:2]
    if h < 100 or w < 100:
        return pil_image.convert("RGB")

    # 1) قناع الورقة من الصورة الخام (حيث تباين الورقة/الخلفية واضح).
    alpha = _isolate_paper_grabcut(img_rgb)
    quad = _best_quad_from_mask(alpha) if alpha is not None else None
    if quad is None:
        # سلم كشف احتياطي: حواف Canny ثم كتلة المحتوى Otsu ثم إطار قريب من A4.
        work = cv2.resize(img_rgb, (max(640, min(1600, w // 2)),
                                    max(640, min(1600, h // 2))),
                          interpolation=cv2.INTER_AREA)
        quad = _best_quad_from_contours(work)
        if quad is not None:
            s = float(img_rgb.shape[1]) / work.shape[1]
            quad = np.asarray(quad, np.float32) * s
        else:
            quad = _best_quad_from_otsu(work)
            if quad is not None:
                s = float(img_rgb.shape[1]) / work.shape[1]
                quad = np.asarray(quad, np.float32) * s
            else:
                a4r = _A4_W_MM / _A4_H_MM
                ratio = w / float(h)
                if (abs(ratio - a4r) / a4r < 0.12 or
                        abs(ratio - 1.0 / a4r) / (1.0 / a4r) < 0.12):
                    quad = np.array([[0, 0], [w - 1, 0],
                                     [w - 1, h - 1], [0, h - 1]],
                                    dtype=np.float32)

    if quad is not None:
        # 2) لوحة نظيفة: ما حول الورقة أبيض ناصع (لا خلفية قديمة) إن وُجد قناع.
        if alpha is not None:
            white = np.full_like(img_rgb, 255, dtype=np.uint8)
            a3 = np.asarray(alpha, dtype=np.float32)[..., None]
            clean = (img_rgb.astype(np.float32) * a3 +
                     white.astype(np.float32) * (1.0 - a3))
            clean = np.clip(clean, 0, 255).astype(np.uint8)
        else:
            clean = img_rgb
        # 3) فرد الرباعي إلى مقاس A4 القياسي الكامل (dst = حدود الصفحة).
        ordered = order_corners_tl_tr_br_bl(np.asarray(quad, np.float32))
        out = _warp_ordered_to_a4(clean, ordered, dpi)
        if out is not None:
            return out

    logger.info("extract_a4: تعذّر كشف ورقة — إرجاع الصورة كما هي")
    return pil_image.convert("RGB")


def straighten_document_to_a4(pil_image: Image.Image, dpi: int = 300) -> Image.Image:
    """قصّ وثيقة A4 تلقائياً وفردها مستقيمة تماماً بنسبة A4 (بدون ذكاء اصطناعي).

    خطوات المعالجة:
    1) تحويل الصورة إلى تدرجات الرمادي (cvtColor) وتنعيمها (GaussianBlur).
    2) استخراج الحواف بكاشف Canny.
    3) البحث عن الحدود الخارجية (findContours).
    4) اختيار أكبر شكل هندسي مغلق ذي 4 أركان يعبّر عن ورقة A4 (approxPolyDP).
    5) ترتيب الأركان الأربعة بدقة (أعلى يسار، أعلى يمين، أسفل يمين، أسفل يسار).
    6) تحويل المنظور (getPerspectiveTransform + warpPerspective) لقصّ الوثيقة
       وفردها بنسبة A4 (210:297 مم) حسب اتجاه الورقة (عمودي أو عرضي).

    عند فشل كشف ورقة A4 تُعاد الصورة الأصلية كما هي بدون أي تغيير.
    """
    img_rgb = np.array(pil_image.convert("RGB"))
    orig_h, orig_w = img_rgb.shape[:2]
    if orig_h < 100 or orig_w < 100:
        return pil_image.convert("RGB")

    # ── تصغير العمل للسرعة ثم رفع الأركان للحجم الأصلي ──
    scale = 1.0
    work = img_rgb
    if max(orig_h, orig_w) > _MAX_EDGE_PX:
        scale = _MAX_EDGE_PX / float(max(orig_h, orig_w))
        work = cv2.resize(img_rgb, (max(10, int(orig_w * scale)),
                                    max(10, int(orig_h * scale))),
                          interpolation=cv2.INTER_AREA)

    h, w = work.shape[:2]

    # 1-4) أكبر رباعي للورقة: حواف Canny ← ثم GrabCut ← ثم Otsu/المحتوى
    quad = _best_quad_from_contours(work)
    if quad is None:
        logger.info("straighten_a4: لم يُكشف رباعي بالحواف — محاولة GrabCut")
        mask = _isolate_paper_grabcut(work)
        quad = _best_quad_from_mask(mask) if mask is not None else None
    if quad is None:
        logger.info("straighten_a4: GrabCut فشل — محاولة عبر كتلة المحتوى Otsu")
        quad = _best_quad_from_otsu(work)
    if quad is None:
        # ضمانة أخيرة: إذا كانت الصورة نفسها بنسبة A4 تقريباً (مسح ضوئي
        # ملء الصفحة) نستخدم كامل الإطار كحدود الورقة — فتتمدد إحداثيات
        # الوجهة (dst) لتغطي A4 كاملة بلا فراغات.
        h, w = work.shape[:2]
        ratio = w / float(h)
        a4r = _A4_W_MM / _A4_H_MM
        if abs(ratio - a4r) / a4r < 0.12 or abs(ratio - 1.0 / a4r) / (1.0 / a4r) < 0.12:
            quad = np.array([[0, 0], [w - 1, 0],
                             [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
            logger.info("straighten_a4: إطار قريب من A4 — استخدام كامل الصورة")
    if quad is None:
        logger.info("straighten_a4: تعذّر كشف الورقة نهائياً — إرجاع الصورة كما هي")
        return pil_image.convert("RGB")

    # رفع الأركان إلى مقياس الصورة الأصلي
    if scale != 1.0:
        quad = quad / scale

    # 5) ترتيب الأركان TL / TR / BR / BL
    ordered = order_corners_tl_tr_br_bl(quad)
    tl, tr, br, bl = ordered

    quad_w = max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl))
    quad_h = max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr))
    if quad_w < 10 or quad_h < 10:
        return pil_image.convert("RGB")

    # 6) تحويل المنظور إلى بنسبة A4 تماماً (اتجاه حسب شكل الورقة)
    px_w = int(round(_A4_W_MM * dpi / 25.4))
    px_h = int(round(_A4_H_MM * dpi / 25.4))
    portrait = quad_h >= quad_w
    dst_w, dst_h = (px_w, px_h) if portrait else (px_h, px_w)

    dst = np.array([[0, 0], [dst_w - 1, 0],
                    [dst_w - 1, dst_h - 1], [0, dst_h - 1]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(ordered, dst)
    warped = cv2.warpPerspective(img_rgb, m, (dst_w, dst_h),
                                 flags=cv2.INTER_CUBIC)

    logger.info("straighten_a4: قُصّت الورقة وفُردت (%s) → %s",
                "عمودي" if portrait else "عرضي", warped.shape)
    return Image.fromarray(warped, "RGB")