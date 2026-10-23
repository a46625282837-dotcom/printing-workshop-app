import logging

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# مُكتشِف المستندات (Document Segmentation) للهويات فوق خلفيات معقّدة.
#
# ثلاث مراحل متتابعة:
#   1) نموذج ذكاء اصطناعي متخصص بالمستندات (YOLOv8-OBB / card_detector.onnx)
#      يرى الهوية كجسمٍ واحد "بطاقة" بغض النظر عن لون خلفيتها (سجّاد/تيرازو/
#      تداخل ألوان) ويرسم ربعاً (Quad) دقيقاً حولها.
#   2) Dilation (توسيع) القناع بمقدار 5-10 بكسل لضمان احتواء كامل حواف الهوية
#      خاصة عندما يتداخل لون حافة الهوية مع لون الخلفية (مثل image_3.png).
#   3) GrabCut من OpenCV كحماية عكسية: إذا فشل الكشف أو كان القناع صغيراً جداً
#      نُخبر GrabCut أن المنطقة المستطيلة المحيطة بالصورة كلها تقريباً هي
#      الهوية (عدا الأطراف البعيدة جداً)، فنحصل على قناع دقيق تحت غياب النموذج.
# ---------------------------------------------------------------------------

# عدد البكسلات لتوسيع القناع (Dilation) لضمّ كامل الحواف عند تداخل الألوان.
_DILATE_PX = 8

# نسبة الهامش الخارجي عند GrabCut: ما تبقى خارجها يُعدّ خلفية بعيدة.
_GRABCUT_MARGIN_RATIO = 0.05

# عتبة أمان: إن كانت مساحة القناع المكشوفة أقل من هذه النسبة من الصورة
# اعتبرنا الكشف فاشلاً (هوية بعيدة/فشل) ونلجأ للحماية العكسية.
_MIN_MASK_AREA_RATIO = 0.01


class DocumentResult:
    """نتيجة مرحلة كشف المستند.

    quad:  مصفوفة (4,2) float32 بإحداثيات الصورة (أو None إذا لم يتوفر ربع).
    mask:  قناع L-mode 0-255 بنفس مقاس الصورة (أو None).
    method: اسم الطريقة الناجحة للطباعة/التشخيص.
    """
    __slots__ = ("quad", "mask", "method")

    def __init__(self, quad=None, mask=None, method="none"):
        self.quad = quad
        self.mask = mask
        self.method = method


# ---------------------------------------------------------------------------
# أدوات هندسية (Dilation وبناء قناع من ربع)
# ---------------------------------------------------------------------------

def _expand_quad(quad, px):
    """يوسّع ربعاً رباعياً للخارج من مركزه بمقدار px بكسل (بلا قصّ).

    يحقّق المرحلة الهندسية "Dilation" على مستوى الزوايا: يُبعد كل رأس مسافة
    تقريبية px في اتجاه شعاعي بعيداً عن المركز، فيضمّ كامل حافة الهوية
    حتى لو تآكلت قليلاً عند التداخل اللوني.
    """
    quad = np.asarray(quad, dtype="float32").reshape(4, 2)
    center = quad.mean(axis=0)
    expanded = np.empty_like(quad)
    for i, p in enumerate(quad):
        v = p - center
        norm = np.linalg.norm(v)
        if norm < 1e-6:
            expanded[i] = p
            continue
        expanded[i] = p + (v / norm) * px
    return expanded


def _quad_to_mask(quad, size):
    """يحوّل ربعاً رباعياً إلى قناع ثنائي (0/255) L-mode بنفس مقاس الصورة."""
    import cv2
    mask = np.zeros((size[1], size[0]), dtype=np.uint8)
    pts = np.asarray(quad, dtype=np.int32).reshape(-1, 1, 2)
    cv2.fillPoly(mask, [pts], 255)
    return Image.fromarray(mask, mode="L")


def _dilate_mask(mask, px=_DILATE_PX):
    """توسيع قناع (Morphological Dilation) بمقدار px بكسل.

    يُطبَّق على القناع بعد الحصول عليه لاحتواء كامل حواف الهوية، خاصة
    عند تداخل لون الحافة مع الخلفية. يتعامل بأمان: لا يتجاوز حدود الصورة.
    """
    import cv2
    a = np.array(mask.convert("L"))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * px + 1, 2 * px + 1))
    a = cv2.dilate(a, kernel, iterations=1)
    return Image.fromarray(a, mode="L")


def _clip_quad_to_image(quad, size):
    """يقصّ الزوايا داخل حدود الصورة حتى لا تتجاوزها عند التوسيع."""
    quad = np.asarray(quad, dtype="float32").reshape(4, 2)
    w, h = size
    quad[:, 0] = np.clip(quad[:, 0], 0, w)
    quad[:, 1] = np.clip(quad[:, 1], 0, h)
    return quad


def _safe_mask_frac(mask):
    """نسبة مساحة القناع غير الصفري من كامل الصورة."""
    a = np.array(mask.convert("L"))
    frac = float((a > 0).sum()) / max(a.size, 1)
    return frac


# ---------------------------------------------------------------------------
# المرحلة 1: نموذج كشف المستندات (YOLOv8-OBB)
# ---------------------------------------------------------------------------

def _detect_with_model(pil_image: Image.Image):
    """يستخدم نموذج الكشف المتخصص (card_detector.onnx) إن توفر.

    يعود: DocumentResult بطريقة "model" أو "none".
    """
    try:
        from core.card_detector import detect_card_quad
    except Exception as e:  # pragma: no cover
        logger.warning("تعذّر استيراد card_detector: %s", e)
        return None

    try:
        quad = detect_card_quad(pil_image)
    except Exception as e:  # pragma: no cover
        logger.warning("فشل نموذج كشف البطاقة: %s", e)
        return None

    if quad is None:
        return None

    quad = _clip_quad_to_image(quad, pil_image.size)

    # توسيع (Dilation) الزوايا لضمّ كامل الحافة عند تداخل الألوان.
    exp = _expand_quad(quad, _DILATE_PX)
    exp = _clip_quad_to_image(exp, pil_image.size)
    mask = _dilate_mask(_quad_to_mask(exp, pil_image.size), _DILATE_PX)

    logger.info("كشف المستند عبر النموذج المتخصص: زوايا=%s", np.round(exp, 1).tolist())
    return DocumentResult(quad=exp, mask=mask, method="model")


# ---------------------------------------------------------------------------
# المرحلة 2: GrabCut كحماية عكسية
# ---------------------------------------------------------------------------

# الحجم الأقصى (أطول ضلع) الذي ننفّذ عليه GrabCut للتحكم بالوقت والذاكرة؛
# الصور الأكبر تُصغّر ثم يُعاد تكبير القناع لاحقاً.
_GRABCUT_MAX_SIDE = 640

# عتبة الانحراف المعياري للسطوع: إذا كانت الصورة شبه موحّدة (أقل من هذه
# الدرجة من التباين) نرفض تشغيل GrabCut لأنه:
#   1) لا توجد خلفية مميزة ليُزيلها أصلًا (كل الإطار هو الهوية).
#   2) GMM الخاصة بـ grabCut قد تتكرر بلا حدود على الأنسجة المسطحة → تعليق.
# فيلجأ المعالج لاحقاً للحل الآمن (اعتبار كامل الصورة هو الهوية).
_FLAT_STD_THRESHOLD = 12.0


def _is_flat_image(pil_image: Image.Image) -> bool:
    """هل الصورة شبه موحّدة (بدون تباين كافٍ) فلا يمكن فصل خلفية عنها؟"""
    import cv2
    img = np.array(pil_image.convert("RGB"))
    small = img
    longest = max(img.shape[0], img.shape[1])
    if longest > 320:
        r = 320.0 / longest
        small = cv2.resize(img, (max(1, int(img.shape[1] * r)),
                                 max(1, int(img.shape[0] * r))),
                           interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    std = float(gray.std())
    if std < _FLAT_STD_THRESHOLD:
        logger.info("صورة شبه موحّدة (std=%.1f) — تخطي GrabCut", std)
        return True
    return False


def _grabcut_document(pil_image: Image.Image):
    """GrabCut كحماية عكسية بخلفية كريمة.

    نُخبر GrabCut أن المستطيل المحيط بالصورة كلها تقريباً هو "الهوية" —
    عبر GC_INIT_WITH_RECT بشبه ملء الصورة (عدا الأطراف البعيدة جداً) —
    فيتعامل مع الخلفية المعقّدة/المتداخلة بشكلٍ فعّال.

    - نستخدم RECT عوض INIT_WITH_MASK لأن الإعلان عن كميات ضخمة من
      GC_PR_FGD يُبطئ GMM بشكل مرضي.
    - نرفض الصور شبه الموحّدة (نصرفها للحل الآمن) لأن GMM قد تعلّق بلا حدود
      على الأنسجة المسطحة ولا توجد خلفية تُزيلها أصلاً.
    """
    if _is_flat_image(pil_image):
        return None

    import cv2
    img = np.array(pil_image.convert("RGB"))
    bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    h, w = bgr.shape[:2]
    if w < 8 or h < 8:
        return None

    scale = 1.0
    longest = max(w, h)
    if longest > _GRABCUT_MAX_SIDE:
        scale = _GRABCUT_MAX_SIDE / float(longest)
        small_w, small_h = max(8, int(round(w * scale))), max(8, int(round(h * scale)))
        bgr_small = cv2.resize(bgr, (small_w, small_h), interpolation=cv2.INTER_AREA)
    else:
        small_w, small_h = w, h
        bgr_small = bgr

    m_x = max(2, int(small_w * _GRABCUT_MARGIN_RATIO))
    m_y = max(2, int(small_h * _GRABCUT_MARGIN_RATIO))
    rect = (m_x, m_y, small_w - m_x, small_h - m_y)

    mask = np.zeros((small_h, small_w), np.uint8)
    bgd = np.zeros((1, 65), np.float64)
    fgd = np.zeros((1, 65), np.float64)
    cv2.grabCut(bgr_small, mask, rect, bgd, fgd, 3, cv2.GC_INIT_WITH_RECT)
    fg = (mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD)
    fg_mask = fg.astype(np.uint8) * 255

    if (fg_mask > 0).sum() == 0:
        return None

    # إذا كنا صغّرنا، نعيد بناء القناع بالحجم الأصلي.
    if scale != 1.0:
        fg_mask = cv2.resize(fg_mask, (w, h), interpolation=cv2.INTER_CUBIC)

    # Dilation للتوسيع لضمّ كامل حواف الهوية عند التداخل اللوني.
    dilated = np.array(_dilate_mask(Image.fromarray(fg_mask, mode="L")), dtype=np.uint8)
    logger.info("تحصيل قناع الهوية عبر GrabCut (العائد %d بكسل)", int((dilated > 0).sum()))
    return DocumentResult(quad=None, mask=Image.fromarray(dilated, mode="L"),
                          method="grabcut")


# ---------------------------------------------------------------------------
# الواجهة العامة: رسم قناع الهوية من أيّ مصدر
# ---------------------------------------------------------------------------

def detect_document_mask(pil_image: Image.Image) -> Image.Image:
    """يرجع قناع الهوية (L-mode، 0-255) عبر النموذج ثم GrabCut إن لزم.

    - القناع مفهوم بمعنى "أي بكسل ينتمي للهوية".
    - يضمن (القاعدة الذهبية) ألا يُقصّ محتوى الهوية أبداً؛ أي فشل يعيد
      قناعاً كاملاً لا يُقصّ شيئاً.
    """
    res = detect_document(pil_image)
    if res.mask is not None:
        return res.mask
    # لا قناع إطلاقاً: نعيد قناعاً شفافاً/كامل الحالة بحسب ما يضمن السلامة —
    # عندها تتعامل extract_card مع الصورة كاملة دون قصّ.
    return Image.fromarray(np.zeros(pil_image.size[::-1], dtype=np.uint8), mode="L")


def detect_document(pil_image: Image.Image) -> DocumentResult:
    """يطبّق مراحل الكشف بالتتابع ويعيد أفضل نتيجة.

    التدفق:
      1) النموذج المتخصص (Document Detection Model) — الأفضل للخلفيات المعقدة.
      2) إن فشل/صغر القناع → GrabCut بقناع أمامي كريم (الحماية العكسية).
      3) إن فشل كلاهما → DocumentResult فارغ لا يقصّ شيئاً.
    """
    # المرحلة 1: النموذج المتخصص.
    res = _detect_with_model(pil_image)
    if res is not None:
        frac = _safe_mask_frac(res.mask) if res.mask is not None else 0.0
        if res.quad is not None and frac >= _MIN_MASK_AREA_RATIO:
            return res
        # القناع صغير جداً (فشل النموذج في العثور على الهوية بشكل كافٍ)
        # → نواصل للحماية العكسية.
        logger.info("قناع النموذج صغير جداً (%.3f) — اللجوء إلى GrabCut", frac)

    # المرحلة 2: GrabCut كحماية عكسية.
    res_gc = _grabcut_document(pil_image)
    if res_gc is not None:
        return res_gc

    # المرحلة 3: لا شيء — لا قصّ.
    logger.warning("فشل كشف المستند (نموذج + GrabCut) — لا قصّ آمن.")
    return DocumentResult(method="none")
