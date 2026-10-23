"""تحسين الصور بنموذج Real-ESRGAN محلياً (تنفيذ ncnn-Vulkan) عند ضغط زر التحسين.

لماذا ncnn-Vulkan بدل حزمة بايثون: ملف تنفيذي واحد (6 ميغا) يعمل على معالج
الرسوميات عبر Vulkan، بلا torch ولا CUDA ولا مكتبات بايثون، فيبقى خفيفاً
ويعمل داخل التطبيق دون تنزيلات ضخمة. النموذج `realesrgan-x4plus` الرسمي من
Real-ESRGAN بحجم 32 ميغا، ومجلده `models/realesrgan`.

لماذا تُحفظ دقة ×4: قياس فعلي على بورتريه مُباعة أظهر أن مكسب النموذج
(×2.6 إلى ×3.9 في استرجاع التفصيل) يضيع تماماً عند التصغير من ×4 إلى ×1،
لأن تفصيله المسترجع غير متّسق الطور فيُلغي متوسطُ التصغير. فالناتج يُحفظ
بدقة ×4 ( capped عند OUT_MAX_SIDE لحماية الذاكرة )، وهي الدقة التي يطبع
عندها التطبيق.

مبدأ «خفيف ومتوازن»: لا تُنقل صورة النموذج كما هي، بل يُنقل **التفصيل عالي
التردد الذي أضافه** فقط (فرق طبقة التفاصيل بين ناتجه وتكبير المصدر اللanczos)،
وممزوجاً بنسبة `weight` خفيفة. وبذلك:
  * قناةا a/b تُؤخذ من المصدر مكبَّراً ⇒ صفر انحراف لوني، ولا منحنى سطوع.
  * بوابة `_detail_gate` (مقياسها مضروبة في ×4) تقصر النقل على الحواف
    الموجّهة — العين، الرموش، الشعر، خطوط الملابس — فلا تضخّم النموذج مسام
    البشرة ولا يهلوس فوقها؛ فبقيت البشرة من المصدر تماماً.
  * كل تغيّر محدود بسقف `MAX_STEP` فلا هالات ولا خطوط داكنة.

إن غاب النموذج أو فشل تنفيذه (لا GPU، لا Vulkan، خطأ) تُرجى None فيستدعي
المعالج الكلاسيكي في `ai_enhance` بدل أن يفشل زر التحسين.
"""
import logging
import os
import shutil
import subprocess
import tempfile

import numpy as np
from PIL import Image

from . import ai_enhance
from .runtime import resource_path

logger = logging.getLogger(__name__)

EXE_NAME = "realesrgan-ncnn-vulkan.exe"
MODEL_NAME = "realesrgan-x4plus"          # بلا لاحقة، كما يتوقعه ncnn
MODEL_SCALE = 4                            # معامل تكبير النموذج
WEIGHT = 0.85                              # «الوزن الخفيف» من 0 إلى 1
DETAIL_SIGMA = 1.2                         # نصف قطر فصل التفصيل عند الدقة الأصلية
SOFT_LAP_MAX = 4.0                         # تحت هذا الحد تكون الصورة طرية
MAX_SIDE_OUT = 6000                        # سقف الضلع الطويل للناتج (حماية الذاكرة)
TIMEOUT_S = 240                            # مهلة التنفيذ بالثواني
TILE = 0                                   # 0 = تلقائي حسب ذاكرة الجهاز

# معطّل افتراضياً: القياس على صور المستخدم الحقيقية (900×1600، 960×1280،
# 901×901) أعطى حِدّة وجه ×0.92 أي أن النموذج يجعل الصور الحادّة أنعم،
# مقابل 52 ثانية لكل صورة. النموذج للاستعادة لا للتحسين، فلم يبقَ له دور
# هنا. الكبح المقطون في core.ai_enhance هو الحل المقيس.
USE_MODEL = False

_MODEL_DIR = resource_path(os.path.join("models", "realesrgan"))


def model_dir() -> str:
    return os.environ.get("REALESRGAN_DIR") or _MODEL_DIR


def is_available() -> bool:
    """هل النموذج موجود على القرص؟ لا يشغّل شيئاً ولا يحتاج معالج رسوميات."""
    d = model_dir()
    return (os.path.isfile(os.path.join(d, EXE_NAME))
            and os.path.isfile(os.path.join(d, MODEL_NAME + ".param"))
            and os.path.isfile(os.path.join(d, MODEL_NAME + ".bin")))


def _run_exe(in_path, out_path):
    d = model_dir()
    cmd = [os.path.join(d, EXE_NAME),
           "-i", in_path, "-o", out_path,
           "-n", MODEL_NAME,
           "-s", str(MODEL_SCALE),
           "-m", d,
           "-t", str(TILE),
           "-f", "png"]
    try:
        proc = subprocess.run(cmd, cwd=d, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        logger.warning("Real-ESRGAN: انتهت المهلة بعد %s ثانية", TIMEOUT_S)
        return False
    except OSError as exc:
        logger.warning("Real-ESRGAN: تعذر تشغيل النموذج: %s", exc)
        return False
    if proc.returncode != 0 or not os.path.isfile(out_path):
        tail = (proc.stdout or b"")[-400:].decode("utf-8", "ignore")
        logger.warning("Real-ESRGAN: فشل التنفيذ (rc=%s): %s", proc.returncode, tail)
        return False
    return True


def _lab_float(rgb):
    """يُرجع (L, a, b) كـ float32 من صورة RGB uint8."""
    import cv2
    lab = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2BGR)
    lab = cv2.cvtColor(lab, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    return (np.ascontiguousarray(l, np.float32),
            np.ascontiguousarray(a, np.float32),
            np.ascontiguousarray(b, np.float32))


def softness(pil_image):
    """قدّر طراوة الصورة: متوسط |Laplacian| على نصف الدقة (0 = مسطّحة تماماً).

    القياس: صورة مُباعة σ≥0.8 تعطي 1.0–4.0، بينما الوثائق الحادة 6–28.
    """
    import cv2
    try:
        l = _lab_float(np.asarray(pil_image.convert("RGB")))[0]
        half = cv2.resize(l, (max(8, l.shape[1] // 2), max(8, l.shape[0] // 2)),
                          interpolation=cv2.INTER_AREA)
        return float(np.abs(cv2.Laplacian(half, cv2.CV_32F)).mean())
    except Exception:
        return 999.0


def should_use_model(pil_image):
    """هل يستحق تشغيل النموذج هذه الصورة؟

    النموذج استعادةُ صور متضررة لا مُحسِّنُ حِدّة: على صورة مُباعة يسترجع
    ×2.6–×3.9 من التفصيل، وعلى صورة حادة أصلاً يضيف ~5٪ فقط ويُرخّي العين.
    لذا لا يُشغَّل إلا حيث يُثبت قياسه أنه يفيد.
    """
    if not USE_MODEL or not is_available() or pil_image is None:
        return False
    s = softness(pil_image)
    logger.info("Real-ESRGAN: طراوة الصورة %.2f (الحد %.1f) => %s",
                s, SOFT_LAP_MAX, "تشغيل النموذج" if s < SOFT_LAP_MAX else "المعالج الكلاسيكي")
    return s < SOFT_LAP_MAX

def enhance_image(pil_image, weight=WEIGHT):
    """يعيد الصورة محسّنة بدقة ×MODEL_SCALE، أو None إذا تعذر تشغيل النموذج.

    لا يرمي استثناءً أبداً: الفشل يعني None فيستدعيه المعالج الكلاسيكي.
    """
    if pil_image is None or not is_available():
        return None
    import cv2
    try:
        src = pil_image.convert("RGB")
        w0, h0 = src.size
        if min(w0, h0) < 32:
            return None

        # المُدخل يُصغَّر إن كانNing كبيراً، حتى لا يتجاوز الناتج السقف
        work = src
        target_in = MAX_SIDE_OUT / float(MODEL_SCALE)
        if max(w0, h0) > target_in:
            s = target_in / float(max(w0, h0))
            work = src.resize((max(1, int(w0 * s)), max(1, int(h0 * s))), Image.LANCZOS)

        tmp = tempfile.mkdtemp(prefix="realesr_")
        try:
            in_path, out_path = os.path.join(tmp, "in.png"), os.path.join(tmp, "out.png")
            work.save(in_path)
            if not _run_exe(in_path, out_path):
                return None
            with Image.open(out_path) as up:
                up = up.convert("RGB")
                expect = (work.size[0] * MODEL_SCALE, work.size[1] * MODEL_SCALE)
                if up.size != expect:
                    logger.warning("Real-ESRGAN: حجم غير متوقع %s بدل %s", (up.size, expect))
                    return None
                up_big = np.asarray(up)

            # التكبير اللطيف للمصدر: منه تُؤخذ قناتا اللون، وهو أساس المقارنة
            ref = np.asarray(work.resize((work.size[0] * MODEL_SCALE,
                                          work.size[1] * MODEL_SCALE), Image.LANCZOS))
            l_ai, _, _ = _lab_float(up_big)
            l_ref, a_ch, b_ch = _lab_float(ref)

            s = DETAIL_SIGMA * MODEL_SCALE
            import cv2 as _cv2
            added = ((l_ai - _cv2.GaussianBlur(l_ai, (0, 0), s))
                     - (l_ref - _cv2.GaussianBlur(l_ref, (0, 0), s)))
            # البوابة تُقاس على الدقة الأصلية — حيث ضُبطت وضُبطت معايرتها —
            # ثم تُكبَّر، لأن بنية الصورة المُكبَّرة إحصاءاتها مختلفة.
            l_src, _, _ = _lab_float(np.asarray(work))
            g_small = ai_enhance._detail_gate(l_src, ai_enhance.GATE_WINDOW)
            gate = np.asarray(
                Image.fromarray((g_small * 255.0).astype(np.uint8)).resize(
                    (work.size[0] * MODEL_SCALE, work.size[1] * MODEL_SCALE),
                    Image.BILINEAR), dtype=np.float32) / 255.0
            out_l = l_ref + np.clip(float(np.clip(weight, 0.0, 1.0)) * added * gate,
                                    -ai_enhance.MAX_STEP, ai_enhance.MAX_STEP)

            merged = _cv2.merge([np.clip(out_l, 0, 255).astype(np.uint8),
                                 a_ch.astype(np.uint8), b_ch.astype(np.uint8)])
            out_rgb = _cv2.cvtColor(_cv2.cvtColor(merged, _cv2.COLOR_LAB2BGR),
                                    _cv2.COLOR_BGR2RGB)
            out = Image.fromarray(out_rgb)
            out.info["realesrgan_weight"] = float(weight)
            out.info["realesrgan_source_size"] = (w0, h0)
            return out
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    except Exception as exc:                        # أي خطأ = لا تحسين، لا انهيار
        logger.warning("Real-ESRGAN: استثناء أثناء التحسين: %s", exc)
        return None


def actual_scale(pil_image):
    """معامل التكبير الفعلي المتوقع للناتج (قد يقل عن ×4 للصور الكبيرة)."""
    if pil_image is None:
        return 1.0
    w, h = pil_image.size
    return min(MODEL_SCALE, MAX_SIDE_OUT / float(max(w, h)))
