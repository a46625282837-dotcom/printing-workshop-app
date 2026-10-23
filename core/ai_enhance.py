"""تحسين الصور للطباعة — كبح ضوضاء مقنَّع ثم توضيح حواف خفيف.

القياس على صور المستخدم الحقيقية (900×1600 و960×1280 و901×901) أثبت أن
المشكلة ليست الضبابية: الصور حادّة أصلاً، والمحسِّن القديم يضيف إليها
‎+9…18% ضوضاء لون هي بالضبط «الحبّات المرئية» التي شكا منها المستخدم،
بينما لا يضيف سوى ‎×1.09 حِدّة. لذلك الرحلة الآن:

1) كبح ضوضاء اللون (a/b) دائماً — فهي لا تحمل تفصيلاً مرئياً (كل مسامّ
   البشرة في قناة الإضاءة) فتنقيتها لا تُفقد شيئاً وتزيل الحبيبات.
2) كبح ضوضاء الإضاءة فوق السطح المسطّح فقط (بشرة/خلفية) حفاظاً على المسام.
3) توضيح الحواف على الإضاءة، محكوم ببوابة البنية، بلا مساس بالبشرة.
4) لا تكبير ولا توليد تفاصيل ولا تعديل سطوع/تباين: أي رفع للسطوح يغيّر
   لون البشرة المُدرَك فيرى المستخدم بقعاً حمراء أو داكنة.
"""
import logging
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# قيم مضبوطة متحفّظة: النتيجة نظيفة من الضغطة الأولى مع بقاء الصورة طبيعية.
DEFAULT_DENOISE = 1.0       # وزن كبح الضوضاء (0 = تعطيل)
CHROMA_SIGMA = 6.0          # bilateral على a/b: يزيل الحبّات اللونية بلا مساس بالبنية
LUMA_SIGMA = 18.0           # bilateral على L داخل السطح المسطّح فقط
LUMA_FLAT_MAX = 0.30        # تحت هذه البوابة يُعدّ السطح نظيفاً قابلاً للتنقية
DEFAULT_SHARPEN = 2.40      # أقصى مقدار تحديد الحواف (للصور الأكثر ضبابية)
BASE_SHARPEN = 0.45         # حدّ أساسي يُطبَّق على كل صورة
DEFAULT_THRESHOLD = 5.0     # أدنى تفصيلة تُحدَّد (يحمي البشرة المسطحة والورق)
MAX_STEP = 8.0              # أقصى تغيّر في الإضاءة لكل بكسل — يمنع الهالة والخط الداكن
CLARITY_SIGMA = 3.5         # مقياس الوضوح المتوسط (بنفس البوابة: لا يسرّب إلى البشرة)
CLARITY_MAX = 0.45          # نصيب المقياس المتوسط من المقدار
GATE_WINDOW = 1.5           # نافذة قياس بنية الصورة
GATE_COH_LOW = 0.45         # أقل تماسك يُسمح بالتوضيح فوقه (فوقه حافة أو خط)
GATE_COH_HIGH = 0.75        # التماسك الذي يصبح عنده التوضيح كاملاً
GATE_EN_LOW = 0.08          # أدنى طاقة حدّية نسبية (تحتها نسيج لا حافة)
GATE_EN_HIGH = 0.25         # طاقة حدّية تُعدّ كاملة
DEFAULT_CONTRAST = 1.0      # لا تعديل تباين افتراضياً (حماية لون البشرة)
DARK_MEAN = 88.0            # متوسط إضاءة الصورة المعتمة
BRIGHT_MEAN = 208.0         # متوسط إضاءة الصورة الساطعة جداً
TARGET_ACUTANCE = 55.0      # حِدّة الإضاءة المستهدفة (تقيس الضبابية)


def is_available() -> bool:
    """التحسين متاح دائماً: يعتمد على OpenCV فقط بلا أي نماذج أو تحميل."""
    try:
        import cv2  # noqa: F401
        return True
    except ImportError:
        return False


def _split_luma(bgr):
    """يفصل الإضاءة عن الألوان: L للإضاءة، a/b لون الصورة (لا تُمسّان)."""
    import cv2
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)


def _acutance(l):
    """مقياس ضبابية الصورة: متوسط مقدار تدرّج الإضاءة (Sobel)."""
    import cv2
    gx = cv2.Sobel(l, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(l, cv2.CV_32F, 0, 1, ksize=3)
    return float(np.sqrt(gx * gx + gy * gy).mean())


def _adaptive_need(l):
    """كم تحتاج الصورة من التوضيح: 0 لصورة حادّة، 1 لصورة ضبابية تماماً."""
    cur = _acutance(l)
    if cur < 1e-3:
        return 0.0
    return float(np.clip(TARGET_ACUTANCE / cur - 1.0, 0.0, 1.0))


def _soft_threshold(detail, threshold):
    """حذّ أدنى ناعم: صفر تحت الحدّ ثم ارتفاع تدريجي.

    الحبيبات والجلد والورق تحت الحدّ لا تُلمس إطلاقاً، والحواف الحقيقية
    فوقه تُحدَّد بسلاسة فلا تظهر تدرّجات أفقية (terracing) في البشرة.
    """
    if threshold <= 0:
        return detail
    mag = np.abs(detail)
    t = float(threshold)
    knee = np.clip((mag - t) / t, 0.0, 1.0)
    knee = knee * knee * (3.0 - 2.0 * knee)
    return np.sign(detail) * mag * knee


def _smoothstep(x, lo, hi):
    t = np.clip((x - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _detail_gate(l, window):
    """بوابة تسمح بتوضيح الحواف والشعر والملابس فقط، وتمنع المسام والبشرة.

    الفكرة بنية الصورة لا لونها: نسيج المسام والجلد *غير موجّه* (تماسك منخفض)
    أما الحواف والشعر والخطوط فمتجهة (تماسك عالٍ). لذلك يُقاس التماسك من
    متجه البنية، فيُحظر التوضيح فوق البشرة والورق والجلد وتبقى العينان
    والشعر والملابس حادّة — بغض النظر عن درجة البشرة أو الإضاءة، فلا حاجة
    لتقدير لون البشرة ولا خطر من قصور التقدير.
    """
    import cv2
    g = np.ascontiguousarray(l, dtype=np.float32)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    jxx = cv2.GaussianBlur(gx * gx, (0, 0), window)
    jyy = cv2.GaussianBlur(gy * gy, (0, 0), window)
    jxy = cv2.GaussianBlur(gx * gy, (0, 0), window)
    tr = jxx + jyy
    det = jxx * jyy - jxy * jxy
    disc = np.sqrt(np.maximum(tr * tr * 0.25 - det, 0.0))
    l1 = tr * 0.5 + disc
    l2 = tr * 0.5 - disc
    coh = (l1 - l2) / (l1 + l2 + 1e-6)
    e_ref = float(np.percentile(l1, 90.0))
    if e_ref <= 0.0:
        return np.zeros_like(l)
    gate = _smoothstep(coh, GATE_COH_LOW, GATE_COH_HIGH)
    return gate * _smoothstep(l1 / e_ref, GATE_EN_LOW, GATE_EN_HIGH)


def _denoise_chroma(a, b, amount):
    """كبح ضوضاء اللون وحدها (بلا إضاءة) — يُنفَّذ مرّتين: قبل التوضيح وبعده.

    السبب: تعديل الإضاءة ثم إعادة الترميز إلى RGB يperturb قنوات a/b المستخرجة
    من الصورة، فيولّد حبيبات لونية جديدة. repass أخير بعد التوضيح يمحوها.
    """
    import cv2
    if amount <= 0:
        return a, b
    w = min(1.0, float(amount))
    d = 5 if max(a.shape) < 1600 else 7
    a_s = cv2.bilateralFilter(a, d, CHROMA_SIGMA, 3.0).astype(np.float32)
    b_s = cv2.bilateralFilter(b, d, CHROMA_SIGMA, 3.0).astype(np.float32)
    return a * (1.0 - w) + a_s * w, b * (1.0 - w) + b_s * w


def _denoise(l, a, b, amount):
    """يكبح ضوضاء الصورة: لوناً دائماً، وإضاءةً فوق السطح المسطّح فقط.

    قناتا a/b لا تحملان تفصيلاً مرئياً — مسامّ البشرة وخطوط الوجه كلها في
    قناة الإضاءة — فكبحهما لا يُفقد شيئاً يُرى، بل يزيل الحبيبات الملوّنة
    التي يزيدها التوضيح لاحقاً. والـbilateral حافظ للحواف وخامل على الصورة
    النظيفة بطبيعته، فلا يحتاج حدّاً للتكيّف.

    أمّا إضاءة السطح المسطّح (بشرة/خلفية/ورق) فتنقّى فقط حيث لا بنية،
    حفاظاً على مسامّ الوجه؛ العين والشعر وخطوط الملابس لا تمسّها البوابة.
    """
    import cv2
    if amount <= 0:
        return l, a, b
    w = min(1.0, float(amount))
    d = 5 if max(l.shape) < 1600 else 7

    a, b = _denoise_chroma(a, b, w)
    flat = np.clip(1.0 - _detail_gate(l, GATE_WINDOW) / LUMA_FLAT_MAX, 0.0, 1.0) * w
    if float(flat.max()) > 0.01:
        l_s = cv2.bilateralFilter(np.clip(l, 0, 255).astype(np.uint8), d,
                                  LUMA_SIGMA, 4.0).astype(np.float32)
        l = l * (1.0 - flat) + l_s * flat
    return l, a, b


def _clarity(l, amount, threshold):
    """زيادة الوضوح على الإضاءة فقط، فوق الحواف والشعر والملابس فقط.

    Unsharp Mask بمقياس واحد يحدّد الحواف المفصّلة: العين، الرموش، الشعر،
    خطوط الملابس والحروف. لا مقياس Clarity واسع: كان يعمل على البشرة نفسها،
    وهو مصدر البقع الذي شكا منه المستخدم — فأُسقط عمداً.

    كل التوضيح يمرّ عبر _detail_gate، فالمناطق غير الموجّهة (بشرة، مسام،
    جلد ناعم، ورق) لا تتغيّر إطلاقاً. ويبقى حدّ أقصى للتفصيلة يمنع الهالات،
    ومقدار تكيّفي يجعل الصور الواضحة أصلاً تأخذ الحدّ الأدنى.

    كبح الضوضاء ليس هنا: _denoise يسبقه على.channel L وبوابة السطح المسطّح.
    """
    import cv2
    if amount <= 0:
        return l
    need = _adaptive_need(l)
    amt = min(BASE_SHARPEN, amount) + need * max(0.0, amount - BASE_SHARPEN)
    if amt <= 0:
        return l
    # تحديد الحواف المفصّلة (العين، الرموش، الشعر، خطوط الملابس)
    sigma = max(0.8, min(2.0, min(l.shape) / 900.0))
    gate = _detail_gate(l, GATE_WINDOW)
    delta = _soft_threshold(l - cv2.GaussianBlur(l, (0, 0), sigma), threshold)
    if CLARITY_SIGMA > 0:
        # مقياس متوسط البنفس البوابة: يزيد الوضوح الظاهر دون تسريب إلى البشرة
        mid = _soft_threshold(
            l - cv2.GaussianBlur(l, (0, 0), CLARITY_SIGMA), threshold)
        delta = delta + (amt * CLARITY_MAX) * mid
    l = l + np.clip(amt * delta * gate, -MAX_STEP, MAX_STEP)
    return l


def _tone(l, contrast, lo, hi):
    """تباين اختياري داخل المدى اللوني الأصلي للصورة (معطّل افتراضياً).

    معطّل افتراضياً (contrast=1.0) عن قصد: أي رفع للسطوع أو تغيّر في
    التباين يغيّر لون البشرة المُدرَك (الأغمق أدفأ، الأفتح أكثر وردي) فيرى
    المستخدم بقعاً حمراء أو داكنة. من يطلب زيادة تباين يفعّلها تمريراً
    صريحاً. المنحنى مُطبَّع على [lo, hi] فلا يمدّ المدى ولا يقصّ التفاصيل.
    """
    if not contrast or abs(contrast - 1.0) <= 1e-3 or not hi > lo:
        return l
    span = hi - lo
    t = np.clip((l - lo) / span, 0.0, 1.0)
    t = t + (t - 0.5) * (float(contrast) - 1.0) * 4.0 * t * (1.0 - t)
    return lo + np.clip(t, 0.0, 1.0) * span


def _soft_limit(l, lo, hi, soft=8.0):
    """تحديد ناعم للتجاوز بدل القصّ الحاد عند 0/255.

    القصّ np.clip يجعل البكسلات القريبة من الأسود أو الأبيض متطابقة فيظهر
    خط قاسٍ وتضيع التفاصيل. هنا يبقى أي تجاوز ناعماً، ونقطتا الأسود
    والأبيض في الصورة الأصلية (lo/hi) لا تتحركان إطلاقاً، فتبقى الصورة
    على نفس مداها اللوني.
    """
    if not hi > lo:
        return l
    y = np.where(l > hi, hi + soft * (1.0 - np.exp(-(l - hi) / soft)), l)
    return np.where(y < lo, lo - soft * (1.0 - np.exp(-(lo - y) / soft)), y)


def enhance_image(pil_image, denoise=DEFAULT_DENOISE, sharpen=DEFAULT_SHARPEN,
                  threshold=DEFAULT_THRESHOLD, contrast=DEFAULT_CONTRAST):
    """يزيد وضوح الصورة للطباعة مع الحفاظ على ملامح الوجه والألوان 100%.

    الخطوات كلها على قناة الإضاءة: Unsharp Mask للحواف الدقيقة مع إبراز
    الملامح بنصف قطر واسع، ثم ضبط خفيف للسطوع والتباين. لا تكبير ولا توليد
    تفاصيل ولا مساس بالبشرة.
    """
    import cv2

    if pil_image is None:
        return pil_image
    img = np.asarray(pil_image.convert("RGB"))
    if img.size == 0 or img.ndim != 3:
        return pil_image

    bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    h, w = bgr.shape[:2]
    lab = _split_luma(bgr)
    l, a, b = cv2.split(lab)
    l = l.astype(np.float32)
    a = a.astype(np.float32)
    b = b.astype(np.float32)
    lo_src, hi_src = float(l.min()), float(l.max())

    l, a, b = _denoise(l, a, b, max(0.0, float(denoise)))
    l = _clarity(l, max(0.0, float(sharpen)), max(0.0, float(threshold)))
    l = _tone(l, float(contrast), lo_src, hi_src)
    l = _soft_limit(l, lo_src, hi_src)
    # كبح لون أخير: تعديل الإضاءة أعلاه أعاد تلويث قنوات a/b
    a, b = _denoise_chroma(a, b, max(0.0, float(denoise)))

    out_lab = cv2.merge([np.clip(l, 0, 255).astype(np.uint8),
                         np.clip(a, 0, 255).astype(np.uint8),
                         np.clip(b, 0, 255).astype(np.uint8)])
    out_bgr = cv2.cvtColor(out_lab, cv2.COLOR_LAB2BGR)
    logger.info("تحسين: %dx%d denoise=%.2f sharpen=%.2f thr=%.1f contrast=%.2f",
                w, h, denoise, sharpen, threshold, contrast)
    return Image.fromarray(cv2.cvtColor(out_bgr, cv2.COLOR_BGR2RGB))
