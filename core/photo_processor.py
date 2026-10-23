from __future__ import annotations

import logging, os, shutil, threading
from PIL import Image

from core.runtime import resource_path

logger = logging.getLogger(__name__)

TARGET_DPI = 600
_MODEL_FILENAME = "u2netp.onnx"
_HOME_DIR = os.path.expanduser("~")
_MODEL_TARGET_DIR = os.path.join(_HOME_DIR, ".u2net")
_MODEL_TARGET_PATH = os.path.join(_MODEL_TARGET_DIR, _MODEL_FILENAME)

# النموذج الأساسي لإزالة خلفية صور الأشخاص (الأدق)
_PREFERRED_REMBG = "u2net"  # نحاول أولاً النموذج المحلي الأفضل
_PRIMARY_MODEL = "isnet-general-use"  # إن توفر محلياً
_FALLBACK_MODEL = "u2net"  # متوفر محلياً، أدق من u2netp
# خيار (ج): كل النماذج المتوفرة محلياً للبطاقات — تُجرَّب بالترتيب
_REMBG_MODEL_NAMES = ("isnet-general-use", "u2net")
_rembg_session = None
_ort_session = None
_rembg_lock = threading.Lock()


def photo_px_size(w_mm, h_mm, dpi=TARGET_DPI):
    return int(w_mm / 25.4 * dpi), int(h_mm / 25.4 * dpi)


def _get_bundled_model_dir():
    """مجلد النماذج المضمّنة في الحزمة (أو مجلد models في شيفرة المصدر)."""
    return resource_path('models')


def _get_bundled_rembg_paths():
    """كل نماذج rembg المضمّنة في الحزمة (isnet ثم u2net) — للنسخ إلى ~/.u2net."""
    base = _get_bundled_model_dir()
    found = []
    for name in (_PRIMARY_MODEL, _FALLBACK_MODEL):
        p = os.path.join(base, f"{name}.onnx")
        if os.path.exists(p) and os.path.getsize(p) > 1000:
            found.append((name, p))
    return found


def _ensure_rembg_model_files():
    """نسخ نماذج rembg المضمّنة (isnet/u2net) إلى ~/.u2net إن لم توجد.

    يعمل بدون إنترنت — نسخ محلي فقط. يضمن تطابق نتيجة إزالة الخلفية
    (isnet عبر rembg) على أي جهاز مثل جهاز المطوّر تماماً.
    """
    os.makedirs(_MODEL_TARGET_DIR, exist_ok=True)
    for name, src in _get_bundled_rembg_paths():
        dst = os.path.join(_MODEL_TARGET_DIR, f"{name}.onnx")
        if not (os.path.exists(dst) and os.path.getsize(dst) > 1000):
            try:
                shutil.copy2(src, dst)
                logger.info("تم نسخ نموذج rembg %s من الحزمة إلى %s", name, _MODEL_TARGET_DIR)
            except Exception as e:
                logger.warning("تعذّر نسخ نموذج rembg %s: %s", name, e)
    _ensure_model_file()


def _get_bundled_model_path():
    """النموذج المضمّن المستخدم: u2netp فقط (كما كان قبل تجربة isnet)."""
    path = resource_path(f'models/{_MODEL_FILENAME}')
    if os.path.exists(path):
        return path
    return None


def _ensure_model_file():
    """نسخ النموذج المحلي المضمَّن (u2netp) إلى مجلد المستخدم إن لم يوجد.

    يعمل بدون إنترنت — لا تحميل أبداً.
    """
    if os.path.exists(_MODEL_TARGET_PATH):
        return
    bundled = _get_bundled_model_path()
    if bundled:
        os.makedirs(_MODEL_TARGET_DIR, exist_ok=True)
        shutil.copy2(bundled, _MODEL_TARGET_PATH)
        logger.info("تم نسخ النموذج %s من الحزمة إلى %s", _MODEL_FILENAME, _MODEL_TARGET_DIR)
    else:
        logger.info("النموذج غير موجود في الحزمة — سيتم استخدام الطرق المتاحة فقط")


def _has_local_rembg_model(name):
    """هل النموذج موجود محلياً في ~/.u2net (بدون إنترنت)؟"""
    path = os.path.join(_MODEL_TARGET_DIR, f"{name}.onnx")
    return os.path.exists(path) and os.path.getsize(path) > 1000


def ensure_rembg_ready() -> bool:
    global _rembg_session
    try:
        os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
        _ensure_rembg_model_files()
        # 1) نموذج محلي جاهز إن وُجد (الأدق): isnet ثم u2net
        for name in (_PRIMARY_MODEL, _FALLBACK_MODEL):
            if _has_local_rembg_model(name):
                from rembg import new_session
                _rembg_session = new_session(name)
                logger.info("نموذج rembg (%s) محلي جاهز", name)
                return True
        # 2) انتهى — النموذج المضمّن u2netp يعمل عبر onnxruntime مباشرة
        logger.info("لا يوجد نموذج rembg محلي — نستخدم onnxruntime مباشرة بالنموذج المضمّن")
        return False
    except Exception as e:
        logger.warning("rembg غير متاح (%s) — نستخدم onnxruntime مباشرة", e)
        return False


def _build_rembg_session():
    """بناء جلسة rembg من النماذج المحلية فقط (isnet ثم u2net) بدون تحميل من الإنترنت."""
    from rembg import new_session
    session = None
    for name in (_PRIMARY_MODEL, _FALLBACK_MODEL):
        if _has_local_rembg_model(name):
            try:
                session = new_session(name)
                break
            except Exception:
                session = None
    return session


def warmup_rembg():
    """تدفئة جلسة rembg عند الإقلاع من خيط خلفي لتسريع أول قصّ صورة شخصية.

    الصورة الأولى بعد فتح التطبيق كانت تدفع ثمن تحميل النموذج الضخم
    (isnet 170MB على المعالج) فتبدو متأخرة جداً. هذه الدالة تبني الجلسة
    مبكراً بنفس المنطق والأولوية تماماً، فلا يتغير أي مخرج.
    """
    try:
        global _rembg_session
        os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
        _ensure_rembg_model_files()
        with _rembg_lock:
            if _rembg_session is None:
                _rembg_session = _build_rembg_session()
                if _rembg_session is not None:
                    logger.info("تمت تدفئة نموذج rembg في الخلفية")
    except Exception as e:
        logger.warning("فشلت تدفئة نموذج rembg (%s)", e)


def _remove_bg_ai(pil_image: Image.Image) -> Image.Image:
    global _rembg_session
    os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
    _ensure_rembg_model_files()
    try:
        from rembg import remove as _ai_remove, new_session
    except Exception as e:
        # rembg معطوب/غير متاح (مثل تعارض numpy/numba): لا نسقط صامتاً —
        # نستخدم onnxruntime المباشر بالنموذج المضمّن بدلاً من عدم القص إطلاقاً.
        logger.warning("rembg غير متاح (%s) — السقوط على onnxruntime المباشر", e)
        return _remove_bg_direct(pil_image)
    with _rembg_lock:
        if _rembg_session is None:
            _rembg_session = _build_rembg_session()
        if _rembg_session is None:
            raise RuntimeError("لا يوجد نموذج rembg محلي جاهز")
        result = _ai_remove(pil_image, session=_rembg_session)
    model = _FALLBACK_MODEL
    logger.info("تمت إزالة الخلفية عبر AI (%s, %s)", model, pil_image.size)
    return result


def _remove_bg_direct(pil_image: Image.Image) -> Image.Image:
    global _ort_session
    import numpy as np
    _ensure_model_file()
    os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
    import onnxruntime
    model_path = _get_bundled_model_path()
    if model_path is None:
        model_path = _MODEL_TARGET_PATH
    if _ort_session is None or os.path.dirname(_ort_session._model_path) != model_path:
        _ort_session = onnxruntime.InferenceSession(
            model_path,
            providers=['CPUExecutionProvider'],
        )
        _ort_session._model_path = model_path
    orig_size = pil_image.size
    img_resized = pil_image.convert("RGB").resize((320, 320), Image.LANCZOS)
    im_ary = np.array(img_resized, dtype=np.float32)
    im_ary = im_ary / max(np.max(im_ary), 1e-6)
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    im_ary = (im_ary - mean) / std
    im_ary = im_ary.transpose((2, 0, 1))
    im_ary = np.expand_dims(im_ary, 0).astype(np.float32)
    ort_outs = _ort_session.run(None, {_ort_session.get_inputs()[0].name: im_ary})
    pred = ort_outs[0][:, 0, :, :]
    ma, mi = np.max(pred), np.min(pred)
    pred = (pred - mi) / (ma - mi + 1e-8)
    pred = np.squeeze(pred)
    mask = Image.fromarray((pred * 255).astype("uint8"), mode="L")
    mask = mask.resize(orig_size, Image.LANCZOS)
    result = pil_image.convert("RGBA")
    result.putalpha(mask)
    logger.info("تمت إزالة الخلفية عبر onnxruntime مباشرة (u2netp, %s)", pil_image.size)
    return result


def _fill_holes_cv2(binary: np.ndarray) -> np.ndarray:
    """تعبئة الثقوب الداخلية (بديل binary_fill_holes دون scipy).

    أي بكسل خلفي (0) غير متصل بحدود الصورة هو «ثقب داخلي» يُملأ معتماً.
    يعمل بدون scipy — مهم للنسخة المجمّعة حيث scipy غير مضمّنة.
    """
    import cv2
    import numpy as np
    if binary.ndim != 2:
        return binary
    h, w = binary.shape
    marker = np.zeros((h, w), np.uint8)
    mask = np.zeros((h + 2, w + 2), np.uint8)
    # تعلّم البكسلات الخلفية المتصلة بالحدود (خلفية حقيقية).
    for y in range(h):
        if binary[y, 0] == 0:
            cv2.floodFill(marker, mask, (0, y), 128)
        if binary[y, w - 1] == 0:
            cv2.floodFill(marker, mask, (w - 1, y), 128)
    for x in range(w):
        if binary[0, x] == 0:
            cv2.floodFill(marker, mask, (x, 0), 128)
        if binary[h - 1, x] == 0:
            cv2.floodFill(marker, mask, (x, h - 1), 128)
    # أي بكسل خلفي لم يصل إليه الفيضان = ثقب داخلي ← يمتلئ معتماً.
    out = binary.copy()
    out[(binary == 0) & (marker != 128)] = 255
    return out


def _decontaminate_edges(pil_image: Image.Image,
                         edge_alpha_max: float = 0.95,
                         min_alpha: float = 0.06,
                         fringe_open_kernel: int = 3,
                         bleed_alpha_min: float = 0.45,
                         bleed_alpha_max: float = 0.9,
                         bleed_dist: float = 35.0,
                         tight_threshold: float = 0.5,
                         tight_cut2: float = 0.8) -> Image.Image:
    """إزالة هالة لون الخلفية المتبقية على أطراف الشخص (decontamination).

    المشكلة: بكسلات الحواف شبه الشفافة (0 < alpha < 1) تحتفظ بمزيج من لون
    الخلفية المعني (alpha compositing) — أسد الشعيرات الرقيقة بلون الخلفية
    على محيط الجلد/الملابس، فتظهر «قصاً بسيطاً متبقياً» عند التقريب.

    العلاج:
      1) حسب لون الخلفية من بكسلات شفافة تماماً عند إطار الصورة (بدون إنترنت).
      2) للبكسلات شبه الشفافة: استرجاع لون الموضوع النقي عبر فكّ معادلة
         التركيب: color = (observed - bg*(1-alpha)) / alpha.
      3) البكسلات الباهتة جداً (alpha <= min_alpha) تُصفَّر تماماً.
      4) قمع الشراشيب الرقيقة المنفصلة (MORPH_OPEN) حتى لا تظهر أثارة خلفية
         ناتئة خارج خط الجسم، ثم تنعيم حدود القناع للحفاظ على حواف ناعمة.
      5) إزالة «شريحة الخلفية اللونية المتسربة»: البكسلات شبه الشفافة ذات
         alpha متوسطة (bleed_alpha_min..bleed_alpha_max) وقريبة اللون من
         الخلفية (المسافة < bleed_dist) تُحذف كلياً — تلتقط حافة سميكة
         من u2netp تحتفظ بلون الخلفية وتظهر عند التقريب كقصّ متبقٍّ.
    يعمل بـ numpy/cv2/PIL فقط — متوافق مع النسخة المجمّعة (لا scipy).
    """
    if pil_image.mode != "RGBA":
        return pil_image
    import numpy as np
    import cv2
    alpha = pil_image.getchannel("A")
    rgb = pil_image.convert("RGB")
    a = np.asarray(alpha, dtype=np.float32) / 255.0
    im = np.asarray(rgb, dtype=np.float32)
    h, w = a.shape

    # (1) لون الخلفية: متوسط بكسلات الشفافية الكاملة عند الحدود الخارجية
    m = 2
    border = np.zeros((h, w), dtype=bool)
    border[:m, :] = True
    border[-m:, :] = True
    border[:, :m] = True
    border[:, -m:] = True
    bg_mask = border & (a <= min_alpha)
    if bg_mask.sum() < 16:
        bg_mask = a <= min_alpha
    if bg_mask.sum() < 16:
        logger.warning("decontamination: لا بكسلات خلفية واضحة — تخطي")
        return pil_image
    bg = im[bg_mask].mean(axis=0)

    # (2) فكّ تركيب الحواف شبه الشفافة (محمي من القسمة على صفر)
    sel = (a > min_alpha) & (a < edge_alpha_max)
    out = im.copy()
    if sel.sum() > 0:
        bg_f = bg.reshape(1, 1, 3)
        a_safe = np.maximum(a[sel][:, None], min_alpha * 0.5)
        clean = (im[sel] - bg_f * (1.0 - a[sel][:, None])) / a_safe
        out[sel] = clean
    out = np.clip(out, 0, 255).astype(np.uint8)

    # (3) تصفير البكسلات الباهتة جداً (خلفية متبقية)
    a_out = a.copy()
    a_out[a <= min_alpha] = 0.0

    # (4) قمع الشراشيب الرقيقة خارج جسم الشخص + إعادة تنعيم الحواف
    core = (a_out >= (min_alpha + 0.02)).astype(np.uint8) * 255
    k = max(2, fringe_open_kernel)
    opk = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1))
    cleaned = cv2.morphologyEx(core, cv2.MORPH_OPEN, opk)
    # تنعيم حدود القناع النظيف (feather لطيف — لا يزيل الكثافة الداخلية)
    sigma = max(1.0, min(h, w) * 0.0012)
    smooth = cv2.GaussianBlur(cleaned.astype(np.float32), (0, 0), sigmaX=sigma)
    a_final = np.maximum(a_out, smooth / 255.0).clip(0.0, 1.0)
    a_final[cleaned == 0] = 0.0
    # لا نسمح للقناع المُنعَّم أن يخلق شراشيب جديدة خارج الجسد
    a_final[a <= min_alpha] = 0.0

    # (5) إزالة «شريحة الخلفية اللونية المتسربة»: بكسلات شبه شفافة ذات
    #     alpha متوسطة وقريبة اللون من الخلفية تُحذف كلياً (قصّ أدق).
    #     يُستخدم a_final (بعد feather) وليس alpha الأصلي: البكسلات المعتمة
    #     بالكامل (a==1) داخل الجسم لا تُمسّ حتى لو لونها قريب من الخلفية.
    bg_dist = np.linalg.norm(
        out.astype(np.float32) - bg.reshape(1, 1, 3), axis=2)
    bleed = ((a_final > bleed_alpha_min) & (a_final < bleed_alpha_max)
             & (bg_dist < bleed_dist))
    if bleed.any():
        a_final[bleed] = 0.0
        logger.info("decontamination: حذف %d بكسل من شريحة الخلفية المتسربة",
                    int(bleed.sum()))

    # (6) تحويل alpha إلى شبه-ثنائي صارم: كل ما فوق العتبة → معتم، تحتها →
#     شفاف، ثم feather رفيع جداً على الخط القاطعي فقط. يُبنى من alpha
#     الأصلي قبل التلوين المسبق (a ليس a_final) حتى لا تتسع الحدود بسبب
#     التدرجات السابقة، ويُقصّ ذيل feather الباهت (يظهر كخلفية عند التقريب).
    core_hard = (a >= tight_threshold).astype(np.uint8) * 255
    core_hard = cv2.morphologyEx(core_hard, cv2.MORPH_OPEN, opk)
    sharp = cv2.GaussianBlur(core_hard.astype(np.float32), (0, 0),
                             sigmaX=max(0.7, min(h, w) * 0.0006))
    a_final = sharp / 255.0
    a_final[core_hard == 0] = 0.0
    a_final[sharp / 255.0 < tight_cut2] = 0.0

    result = Image.fromarray(out, mode="RGB").convert("RGBA")
    result.putalpha(Image.fromarray((a_final * 255).astype(np.uint8), mode="L"))
    logger.info("decontamination: تنظيف %d بكسل — قصّ حاد (thr=%.2f)",
                int(sel.sum()), tight_threshold)
    return result


def _remove_bg_grabcut(pil_image: Image.Image) -> Image.Image:
    import cv2
    import numpy as np
    img = cv2.cvtColor(np.array(pil_image.convert("RGB")), cv2.COLOR_RGB2BGR)
    h, w = img.shape[:2]
    margin_x = max(1, int(w * 0.02))
    margin_y = max(1, int(h * 0.02))
    mask = np.zeros((h, w), np.uint8)
    mask[margin_y:h-margin_y, margin_x:w-margin_x] = cv2.GC_PR_FGD
    mask[:1, :] = cv2.GC_BGD
    mask[h-1:, :] = cv2.GC_BGD
    mask[:, :1] = cv2.GC_BGD
    mask[:, w-1:] = cv2.GC_BGD
    bgd = np.zeros((1, 65), np.float64)
    fgd = np.zeros((1, 65), np.float64)
    cv2.grabCut(img, mask, None, bgd, fgd, 4, cv2.GC_INIT_WITH_MASK)
    fg = (mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD)
    mask2 = fg.astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask2 = cv2.morphologyEx(mask2, cv2.MORPH_CLOSE, kernel)
    result_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    result_pil = Image.fromarray(result_rgb).convert("RGBA")
    result_pil.putalpha(Image.fromarray(mask2, mode="L"))
    logger.info("تمت إزالة الخلفية عبر grabCut (%s)", pil_image.size)
    return result_pil


def remove_background_multi(pil_image: Image.Image):
    """خيار (ج): تجربة نماذج rembg متعددة محلياً واختيار أفضل مسار للبطاقة.

    نحاول النماذج المتاحة (isnet-general-use ثم u2net ثم u2netp عبر onnxruntime)
    ونقيّم جودة قناع كل نتيجة كيف يلتقط «بطاقة كاملة» بهوامش خلفية معقولة،
    لأن هويات السطوع القوي قد يقتطعها النموذج الواحد إلى أجزاء.
    تعيد (best_image, used_model, score) — جميعها بدون إنترنت.
    """
    os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
    _ensure_rembg_model_files()
    results = []
    # جودة مقيَّدة: نكتفي بنتيجة ممتازة ولا نشغّل بقية النماذج إلا عند الحاجة.
    GOOD_ENOUGH = 0.72
    try:
        from rembg import new_session, remove as _ai_remove
        for name in _REMBG_MODEL_NAMES:
            if not _has_local_rembg_model(name):
                continue
            try:
                with _rembg_lock:
                    result = _ai_remove(pil_image, session=new_session(name))
                if result.mode == "RGBA":
                    result = restore_interior_alpha(result)
                score = _card_cut_score(result)
                logger.info("rembg %s: جودة القص %.3f", name, score)
                results.append((score, name, result))
                if score >= GOOD_ENOUGH:
                    break
            except Exception as e:
                logger.warning("rembg %s فشل (%s)", name, e)
    except Exception as e:
        logger.warning("rembg متعدد النماذج غير متاح (%s)", e)
    try:
        with _rembg_lock:
            direct = _remove_bg_direct(pil_image)
        score = _card_cut_score(direct)
        logger.info("u2netp علىnx مباشر: جودة القص %.3f", score)
        results.append((score, "u2netp", direct))
    except Exception as e:
        logger.warning("u2netp مباشر فشل (%s)", e)
    if not results:
        logger.warning("كل النماذج فشلت — إرجاع الصورة كما هي")
        return pil_image.convert("RGBA"), "none", 0.0
    results.sort(key=lambda r: r[0], reverse=True)
    best_score, best_name, best_img = results[0]
    logger.info("rembg متعدد النماذج: أفضل نموذج %s بجودة %.3f (من %d نتائج)",
                best_name, best_score, len(results))
    return best_img, best_name, best_score


def _card_cut_score(rgba_image: Image.Image) -> float:
    """قيّم جودة قص البطاقة: شفافية تعزل بطاقة كاملة بهوامش خلفية معقولة.

    مقياس مبني على alpha:
      - البطاقة ليست كامل الصورة (فشل صامت) ولا لا شيء (فشل كامل).
      - أكبر مكوّن معزول قريب من نسبة أبعاد البطاقة (~0.7) ويحتل حجماً معقولاً.
      - حوافه متصلة (لا تقطيع لأجزاء).
    العائد 0..1، الأعلى أفضل.
    """
    import numpy as np
    import cv2
    if rgba_image.mode != "RGBA":
        return 0.0
    alpha = np.array(rgba_image.getchannel("A"), dtype=np.uint8)
    h, w = alpha.shape
    area = h * w
    opaque = int(np.sum(alpha > 20))
    frac = opaque / area if area else 0
    if frac < 0.02 or frac > 0.99:
        return 0.0
    _, binary = cv2.threshold(alpha, 20, 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return 0.0
    largest = max(contours, key=cv2.contourArea)
    x, y, bw, bh = cv2.boundingRect(largest)
    cont_area = cv2.contourArea(largest)
    if bw <= 0 or bh <= 0:
        return 0.0
    aspect = bw / bh if bw >= bh else bh / bw  # 1 للبطاقة الأفقية
    bbox_frac = (bw * bh) / area
    solidity = cont_area / max(1, bw * bh)
    cut_measure = max(0.0, 1.0 - (asp_ratio_dist(aspect) * 2.0))
    coverage = min(1.0, bbox_frac / 0.6)
    return max(0.0, min(1.0, solidity * coverage * cut_measure))


def asp_ratio_dist(aspect: float) -> float:
    """مدى بُعد النسبة عن نسبة البطاقة المعروفة (~1.5863 لعرض:ارتفاع برتوكول).

    نسمح للبطاقات الأفقية والعمودية — نقيس البعد فقط من النطاق المقبول.
    """
    if 1.2 <= aspect <= 2.4:
        return 0.0
    if aspect < 1.2:
        return 1.2 - aspect
    return aspect - 2.4


def refine_subject_alpha(pil_image: Image.Image,
                         grow_ratio: float = 0.0038,
                         feather_ratio: float = 0.005,
                         recover_level: int = 55) -> Image.Image:
    """حماية أجزاء الشخص من الاقتطاع بعد إزالة الخلفية.

    المشكلة: النماذج المحلية (خصوصاً u2netp) تُصغّر دقة القناع فتُفقد أطراف
    الشعر والكتفين، وقد تعطي أجزاءً من الشخص شفافية منخفضة فتُحذف مع الخلفية.

    العلاج (آمن — يضيف للقناع فقط ولا يحذف منه):
      1) سدّ الشقوق الدقيقة في القناع (MORPH_CLOSE) ل Prevent
         تجزّع أجزاء الشخص إلى منفصلة عن الجسم.
      2) تعبئة الفجوات الداخلية (binary_fill_holes).
      3) استعادة البكسلات المنخفضة الشفافية التي قد تكون جزءاً من الشخص.
      4) توسيع القناع للخارج (dilate) لحماية الأطراف.
      5) تنعيم الحواف (feather).
    """
    if pil_image.mode != "RGBA":
        return pil_image
    import cv2
    import numpy as np
    alpha = pil_image.getchannel("A")
    a = np.array(alpha, dtype=np.uint8)
    h, w = a.shape
    if h == 0 or w == 0:
        return pil_image

    # (1) سدّ الشقوق: 결합 أجزاء الشخص المتقاربة قبل أي تعبئة
    k = max(2, int(round(min(h, w) * 0.012)))
    close_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1))
    bin_mask = (a >= 30).astype(np.uint8) * 255
    closed = cv2.morphologyEx(bin_mask, cv2.MORPH_CLOSE, close_k)

    # (2) تعبئة الفجوات الداخلية — أي بقعة شفافة محاطة بالقناع تُملأ
    # (بديل cv2 — يعمل في النسخة المجمّعة حيث scipy غير مضمّنة)
    try:
        from scipy.ndimage import binary_fill_holes
        filled = binary_fill_holes(closed > 127).astype(np.uint8) * 255
    except ImportError:
        filled = _fill_holes_cv2((closed > 127).astype(np.uint8))

    # (3) استعادة البكسلات الباهتة: رفع قيم alpha المنخفضة المتصلة بالشخص
    af = filled.astype(np.float32)
    boost = np.clip((af.astype(np.float32) - 0) / max(1, recover_level), 0.0, 1.0)
    recovered = np.maximum(a.astype(np.float32), boost * 255.0).clip(0, 255)

    # (4) توسيع القناع للخارج (حماية حواف الشعر والكتف)
    grow = max(3, min(12, int(round(min(h, w) * grow_ratio))))
    gk = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1, 2 * grow + 1))
    core = (recovered >= 128).astype(np.uint8) * 255
    grown = cv2.dilate(core, gk)

    # نأخذ الأعلى بين القناع المستعاد والموسّع
    combined = np.maximum(recovered, grown.astype(np.float32)).clip(0, 255)

    # (5) تنعيم الحواف دون إنقاص كثافة الداخل
    sigma = max(0.8, min(h, w) * feather_ratio)
    feathered = cv2.GaussianBlur(combined, (0, 0), sigmaX=sigma)
    final = np.maximum(combined, feathered).clip(0, 255).astype(np.uint8)

    result = pil_image.copy()
    result.putalpha(Image.fromarray(final, mode="L"))
    logger.info("حماية الشخص: سدّ %dpx، توسيع %dpx، استعادة عتبة %d، تنعيم %.1f",
                k, grow, recover_level, sigma)
    return result


def remove_background(pil_image: Image.Image) -> Image.Image:
    """إزالة خلفية الصورة بقسم الصور الشخصية — rembg فقط.

    يُستخدم النموذج المحلي الأفضل المتوفر في ~/.u2net (isnet ثم u2net)
    عبر rembg، وتُرجع نتيجته كما هي دون أي مرحلة معالجة لاحقة
    ودون السقوط على u2netp المباشر. إن لم يوجد نموذج rembg محلي
    تُرجع الصورة كما هي (لا نقصّ بجودة سيئة ولا نتلف التفاصيل).
    """
    _ensure_rembg_model_files()
    result = _remove_bg_ai(pil_image)
    if result.mode == "RGBA":
        alpha = result.getchannel("A")
        ext = alpha.getextrema()
        if ext[0] == 255 and ext[1] == 255:
            logger.warning("إزالة الخلفية عبر AI أنتجت alpha كلها 255 (فشل صامت) — إرجاع الصورة كما هي")
            return pil_image.convert("RGBA")
    return result


def composite_white_bg(pil_image: Image.Image) -> Image.Image:
    if pil_image.mode == "RGBA":
        bg = Image.new("RGB", pil_image.size, (255, 255, 255))
        bg.paste(pil_image, mask=pil_image.split()[3])
        logger.info("تم تركيب الصورة على خلفية بيضاء (%s)", pil_image.size)
        return bg
    if pil_image.mode != "RGB":
        return pil_image.convert("RGB")
    return pil_image


def restore_interior_alpha(pil_image: Image.Image, min_hole_ratio=0.003) -> Image.Image:
    """Restore (fill) transparent holes that are fully surrounded by opaque subject.

    isnet/u2net sometimes carve out clothing that visually blends with the background
    (white shirts, multi-color/camouflage garments) producing "holes" inside the body.
    Any transparent region NOT connected to the image border is a hole inside the person
    and is re-filled as opaque -- while genuine background (connected to the border)
    stays removed.
    """
    if pil_image.mode != "RGBA":
        return pil_image
    import cv2
    import numpy as np
    alpha = pil_image.getchannel("A")
    a = np.array(alpha, dtype=np.uint8)
    h, w = a.shape
    opaque = (a >= 128).astype(np.uint8)  # 1 = opaque subject

    # binary_fill_holes fills any transparent region that does NOT touch the border
    # (i.e. enclosed by opaque subject) -> restores white/multi-color clothing.
    # بديل cv2 — يعمل في النسخة المجمّعة حيث scipy غير مضمّنة.
    try:
        from scipy.ndimage import binary_fill_holes
        filled = binary_fill_holes(opaque).astype(np.uint8)
    except ImportError:
        filled = _fill_holes_cv2((opaque > 0).astype(np.uint8) * 255) // 255
    holes = (filled - opaque).clip(0)

    # Remove tiny speckle holes below threshold (noise), keep real interior holes.
    npx = h * w
    holes8 = (holes * 255).astype(np.uint8)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(holes8, connectivity=8)
    keep = np.zeros_like(holes8)
    for i in range(1, num):
        if stats[i, cv2.CC_STAT_AREA] >= max(1, int(npx * min_hole_ratio)):
            keep[labels == i] = 255
    if keep.sum() > 0:
        new_a = a.copy()
        new_a[keep > 0] = 255
        result = pil_image.copy()
        result.putalpha(Image.fromarray(new_a, mode="L"))
        logger.info("استعادة %d بكسل من ثقوب داخلية بالملابس", int(keep.sum() / 255))
        return result
    return pil_image


def auto_crop_subject(pil_image: Image.Image, margin_ratio=0.15) -> Image.Image:
    if pil_image.mode != "RGBA":
        return pil_image
    alpha = pil_image.getchannel("A")
    bbox = alpha.getbbox()
    if bbox is None:
        return pil_image
    left, upper, right, lower = bbox
    bw, bh = right - left, lower - upper
    total_area = pil_image.width * pil_image.height
    crop_area = bw * bh
    if crop_area >= total_area * 0.98:
        logger.warning("إزالة الخلفية فشلت (bbox يغطي %d%% من الصورة)، تخطي القص", crop_area * 100 // total_area)
        return pil_image
    actual_margin = margin_ratio
    if crop_area < total_area * 0.3:
        logger.info("المساحة المقطوعة صغيرة جداً (%d%%)، تكبير الهامش", crop_area * 100 // total_area)
        actual_margin = max(margin_ratio, 0.5)
    elif crop_area < total_area * 0.5:
        logger.info("مساحة القص متوسط (%d%%)، زيادة الهامش", crop_area * 100 // total_area)
        actual_margin = max(margin_ratio, 0.3)
    margin_x = max(1, int(bw * actual_margin))
    margin_y = max(1, int(bh * actual_margin))
    new_left = max(0, left - margin_x)
    new_upper = max(0, upper - margin_y)
    new_right = min(pil_image.width, right + margin_x)
    new_lower = min(pil_image.height, lower + margin_y)
    logger.info("تم اقتصاص الصورة إلى (%d×%d) حول الموضوع (هامش %.0f%%)", new_right - new_left, new_lower - new_upper, actual_margin * 100)
    return pil_image.crop((new_left, new_upper, new_right, new_lower))


def _face_features(bgr_img):
    """Detect face and return dict of feature masks, or None if no face found."""
    import cv2
    import numpy as np
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    gray = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2GRAY)
    faces = cascade.detectMultiScale(gray, 1.1, 5, minSize=(100, 100))
    if len(faces) == 0:
        return None
    x, y, w, h = max(faces, key=lambda r: r[2] * r[3])
    hi, wi = bgr_img.shape[:2]

    def _ell(mask, cx, cy, rx, ry):
        cv2.ellipse(mask, (cx, cy), (rx, ry), 0, 0, 360, 255, -1)

    # Face-only skin mask (used for skin_nf, brightness extended below)
    skin_face = np.zeros((hi, wi), dtype=np.uint8)
    _ell(skin_face, x + w // 2, y + int(h * 0.42), w // 2, int(h * 0.48))
    left_eye = np.zeros((hi, wi), dtype=np.uint8)
    _ell(left_eye, x + int(w * 0.28), y + int(h * 0.37), int(w * 0.09), int(h * 0.06))
    right_eye = np.zeros((hi, wi), dtype=np.uint8)
    _ell(right_eye, x + int(w * 0.72), y + int(h * 0.37), int(w * 0.09), int(h * 0.06))
    left_eb = np.zeros((hi, wi), dtype=np.uint8)
    _ell(left_eb, x + int(w * 0.28), y + int(h * 0.22), int(w * 0.09), int(h * 0.035))
    right_eb = np.zeros((hi, wi), dtype=np.uint8)
    _ell(right_eb, x + int(w * 0.72), y + int(h * 0.22), int(w * 0.09), int(h * 0.035))
    lips = np.zeros((hi, wi), dtype=np.uint8)
    _ell(lips, x + w // 2, y + int(h * 0.68), int(w * 0.11), int(h * 0.04))
    # Skin (face + neck) for brightness
    skin = skin_face.copy()
    cv2.rectangle(skin, (x + w // 2 - int(w * 0.35), y + int(h * 0.60)),
                  (x + w // 2 + int(w * 0.35), y + int(h * 1.20)), 255, -1)
    # skin_nf = face skin with features removed, for smooth/blemish
    skin_nf = skin_face.copy()
    for feat in (left_eye, right_eye, left_eb, right_eb, lips):
        skin_nf[feat > 0] = 0
    # Color-based skin mask for brightness — YCrCb chrominance distance (ignores luminance)
    ref_px = bgr_img[skin_nf > 0]
    if len(ref_px) > 0:
        img_ycrcb = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2YCrCb).astype(np.float32)
        ref_ycrcb = cv2.cvtColor(
            ref_px.reshape(-1, 1, 3).astype(np.uint8),
            cv2.COLOR_BGR2YCrCb
        ).reshape(-1, 3)
        avg_cr = np.mean(ref_ycrcb[:, 1])
        avg_cb = np.mean(ref_ycrcb[:, 2])
        diff = np.sqrt((img_ycrcb[:,:,1] - avg_cr)**2 + (img_ycrcb[:,:,2] - avg_cb)**2)
        std_crcb = np.mean([np.std(ref_ycrcb[:, 1]), np.std(ref_ycrcb[:, 2])])
        thresh = max(8, min(30, std_crcb * 2.5))
        skin_color = (diff < thresh).astype(np.uint8) * 255
        skin_color = skin_color & skin
        skin_color = cv2.morphologyEx(skin_color, cv2.MORPH_CLOSE, np.ones((5,5), np.uint8))
        skin_color = cv2.morphologyEx(skin_color, cv2.MORPH_OPEN, np.ones((3,3), np.uint8))
        logger.info("قناع لون البشرة YCrCb (%d عينة, عتبة CrCb=%.1f)", len(ref_px), thresh)
    else:
        skin_color = skin
    return dict(face_rect=(x, y, w, h), skin_color=skin_color, skin_nf=skin_nf)


def enhance_portrait_advanced(pil_image: Image.Image, settings: dict) -> Image.Image:
    """Face-aware portrait enhancement.
    settings keys (all 0-100): skin_smooth, blemish, brightness.
    Falls back to global processing if no face is detected.
    """
    if all(v == 0 for v in settings.values()):
        return pil_image
    alpha = None
    if pil_image.mode == "RGBA":
        alpha = pil_image.getchannel("A")
        pil_image = pil_image.convert("RGB")
    import cv2
    import numpy as np
    img = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
    feats = _face_features(img)
    result = img.copy().astype(np.float32)

    if feats is None:
        logger.info("لم يتم كشف وجه، تطبيق معالجة عامة")
        ss = settings.get('skin_smooth', 0)
        if ss > 0:
            t = ss / 100.0
            d = max(3, int(5 + t * 10))
            sc = max(10, int(10 + t * 140))
            blur = cv2.bilateralFilter(img, d, sc, sc)
            result = (result * (1 - t * 0.5) + blur.astype(np.float32) * (t * 0.5))
        bl = settings.get('blemish', 0)
        if bl > 0:
            t = bl / 100.0
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
            lm = cv2.GaussianBlur(gray, (15, 15), 0)
            lsq = cv2.GaussianBlur(gray ** 2, (15, 15), 0)
            std = np.sqrt(np.maximum(0, lsq - lm ** 2))
            th = max(10, int(30 - t * 20))
            m = (std > th).astype(np.uint8) * 255
            m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
            m = cv2.dilate(m, np.ones((3, 3), np.uint8), iterations=1)
            if np.any(m > 0):
                inp = cv2.inpaint(img, m, int(2 + t * 4), cv2.INPAINT_TELEA)
                blend = np.clip(t * 0.7, 0, 1)
                result = result * (1 - blend) + inp.astype(np.float32) * blend
        img = np.clip(result, 0, 255).astype(np.uint8)
        result_pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        if alpha is not None:
            result_pil.putalpha(alpha)
        return result_pil

    fx, fy, fw, fh = feats['face_rect']
    logger.info("تم كشف وجه في (%d,%d,%d,%d)", fx, fy, fw, fh)

    def soft_blend(mask_255, fg, bg):
        """Blend fg into bg using feathered mask."""
        m = mask_255.astype(np.float32) / 255.0
        m = cv2.GaussianBlur(m, (0, 0), sigmaX=5).clip(0, 1)
        m = np.expand_dims(m, -1)
        return fg * m + bg * (1 - m)

    # --- Skin Smoothing ---
    ss = settings.get('skin_smooth', 0)
    if ss > 0:
        t = ss / 100.0
        d = max(3, int(5 + t * 10))
        sc = max(10, int(10 + t * 140))
        blurred = cv2.bilateralFilter(img, d, sc, sc)
        blend = t * 0.6
        smoothed = (img.astype(np.float32) * (1 - blend) + blurred.astype(np.float32) * blend)
        result = soft_blend(feats['skin_nf'], smoothed, result)
        logger.info("تنعيم البشرة بقوة %d (d=%d, sc=%d)", ss, d, sc)

    # --- Blemish Removal ---
    bl = settings.get('blemish', 0)
    if bl > 0:
        t = bl / 100.0
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
        lm = cv2.GaussianBlur(gray, (15, 15), 0)
        lsq = cv2.GaussianBlur(gray ** 2, (15, 15), 0)
        std = np.sqrt(np.maximum(0, lsq - lm ** 2))
        th = max(10, int(30 - t * 20))
        m = (std > th).astype(np.uint8) * 255
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        m = cv2.dilate(m, np.ones((3, 3), np.uint8), iterations=1)
        safe_skin = cv2.erode(feats['skin_nf'], np.ones((3,3), np.uint8), iterations=2)
        m = m & safe_skin
        if np.any(m > 0):
            inp = cv2.inpaint(img, m, int(2 + t * 4), cv2.INPAINT_TELEA)
            fg = (result * (1 - t * 0.7) + inp.astype(np.float32) * (t * 0.7))
            result = soft_blend(m, fg, result)
            logger.info("إزالة العيوب بقوة %d (بقع=%d)", bl, cv2.countNonZero(m))

    # --- Brightness (skin-color-aware, BGR multiplication preserves warmth) ---
    br = settings.get('brightness', 0)
    if br != 0:
        t = br / 100.0
        factor = 1 + t * 0.35
        brightened = np.clip(result.astype(np.float32) * factor, 0, 255)
        result = soft_blend(feats['skin_color'], brightened, result)
        logger.info("سطوع الوجه والرقبة بقوة %d (factor=%.2f, قناع لوني)", br, factor)

    img = np.clip(result, 0, 255).astype(np.uint8)
    result_pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    if alpha is not None:
        result_pil.putalpha(alpha)
    return result_pil


def enhance_portrait(pil_image: Image.Image, strength: int) -> Image.Image:
    """Simple global denoise/sharpen (backward compat, maps to advanced)."""
    if strength == 0:
        return pil_image
    if 1 <= strength <= 40:
        t = strength / 40.0
        return enhance_portrait_advanced(pil_image, {
            'skin_smooth': int(t * 70), 'blemish': int(t * 80),
        })
    elif 61 <= strength <= 100:
        alpha = None
        if pil_image.mode == "RGBA":
            alpha = pil_image.getchannel("A")
            pil_image = pil_image.convert("RGB")
        import cv2
        import numpy as np
        img = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
        t = (strength - 60) / 40.0
        k = max(1, int(5 - t * 4))
        blurred = cv2.GaussianBlur(img, (k * 2 + 1, k * 2 + 1), 0)
        amt = 0.5 + t * 1.5
        img = cv2.addWeighted(img, 1 + amt, blurred, -amt, 0)
        result = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        if alpha is not None:
            result.putalpha(alpha)
        return result
    return pil_image


def enhance_auto_remini(pil_image: Image.Image) -> Image.Image:
    """Safe auto enhancement for ID photos.

    Face detection is used ONLY for exposure metering (gamma correction).
    All operations are global — no masking to prevent edge halos/artifacts.
    """
    alpha = None
    if pil_image.mode == "RGBA":
        alpha = pil_image.getchannel("A")
        pil_image = pil_image.convert("RGB")
    import cv2
    import numpy as np
    img = np.array(pil_image, dtype=np.uint8)
    img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

    feats = _face_features(img_bgr)
    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB)
    l = lab[:, :, 0].astype(np.float32)

    # Stage 1: Face-aware gamma correction (global, no mask)
    if feats is not None:
        fx, fy, fw, fh = feats['face_rect']
        face_l = l[fy:fy+fh, fx:fx+fw]
        mean_face = np.mean(face_l)
        logger.info("تحسين تلقائي: متوسط وجه=%d", int(mean_face))
        if 20 < mean_face < 140:
            gamma = np.log(155/255.0) / np.log(max(mean_face/255.0, 0.05))
            gamma = np.clip(gamma, 0.5, 1.5)
            lut = np.array([((i/255.0)**gamma)*255 for i in range(256)], dtype=np.uint8)
            l = cv2.LUT(l.astype(np.uint8), lut).astype(np.float32)
            logger.info("تحسين تلقائي: غاما=%.2f", gamma)
    else:
        logger.info("تحسين تلقائي: لم يتم كشف وجه")

    # Mild histogram stretch (prevents flat/ washed-out look)
    low = np.percentile(l, 2)
    high = np.percentile(l, 98)
    if high - low > 15:
        l = np.clip((l - low) * 255.0 / (high - low), 0, 255)

    lab[:, :, 0] = l.astype(np.uint8)
    img_bgr = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    # Stage 2: Very mild global sharpening (no mask)
    blur = cv2.GaussianBlur(img_bgr, (0, 0), sigmaX=0.6)
    img_bgr = np.clip(img_bgr.astype(np.float32) + 0.12 * (img_bgr.astype(np.float32) - blur.astype(np.float32)), 0, 255).astype(np.uint8)

    result = Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))

    # Stage 3: Professional skin smoothing + blemish removal
    # Reuses the proven enhance_portrait_advanced (skin_nf mask + soft_blend)
    result = enhance_portrait_advanced(result, {
        'skin_smooth': 60, 'blemish': 70, 'brightness': 0,
    })
    logger.info("تحسين تلقائي: تنعيم بشرة + إزالة عيوب")

    if alpha is not None:
        result.putalpha(alpha)
    return result


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


def auto_fit_card_size(pil_image: Image.Image,
                       w_mm: float = 35, h_mm: float = 45,
                       tolerance: float = 0.12) -> Image.Image:
    """تلقائياً: تحويل أي صورة إلى مقاس البطاقة 4.5×3.5 عند الإضافة.

    إذا كانت نسبة أبعاد الصورة قريبة من مقاس البطاقة (3.5:4.5 ≈ 0.777)
    تُترك كما هي كما هو مطلوب. وإلا تقصّ إلى النسبة وتُصغّر للمقاس.
    """
    if pil_image.size[1] == 0:
        return pil_image
    ratio = pil_image.width / pil_image.height
    target_ratio = w_mm / h_mm
    if abs(ratio - target_ratio) <= tolerance:
        logger.info("الصورة أصلاً بمقاس البطاقة (نسبة %.3f) — تُترك كما هي", ratio)
        return pil_image
    logger.info("حول تلقائياً إلى مقاس البطاقة (نسبة %.3f → %.3f)", ratio, target_ratio)
    out = pil_image.convert("RGB")
    if ratio > target_ratio:
        new_w = int(out.height * target_ratio)
        left = (out.width - new_w) // 2
        out = out.crop((left, 0, left + new_w, out.height))
    else:
        new_h = int(out.width / target_ratio)
        top = (out.height - new_h) // 3
        out = out.crop((0, top, out.width, min(top + new_h, out.height)))
    out = resize_to_photo_size(out, w_mm, h_mm)
    return out


def crop_to_id_photo(pil_image: Image.Image,
                     head_ratio: float = 0.22,
                     shoulder_ratio: float = 0.70) -> Image.Image:
    """قص الصورة إلى إطار بطاقة الهوية مع تناسب الجسم مع الرأس.

    النسبة المستهدفة: الرأس يشغل ~22% من ارتفاع الصورة (شخص صغير متناسب)،
    الكتفين ~70% من العرض، مع هامش مريح فوق الرأس كي لا يُقصّ.
    لا يملأ المربع بالكامل — يظهر الجسم بتناسب واقعي داخل الإطار.
    """
    import cv2
    import numpy as np

    img = pil_image.convert("RGB")
    w, h = img.size
    bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

    # كشف الوجه
    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    faces = cascade.detectMultiScale(gray, 1.1, 5, minSize=(50, 50))

    target_ratio = 3 / 4  # عرض:ارتفاع

    if len(faces) == 0:
        # لا يوجد وجه — قص بسيط مع هامش
        if w / h > target_ratio:
            new_w = int(h * target_ratio)
            left = (w - new_w) // 2
            return pil_image.crop((left, 0, left + new_w, h))
        else:
            new_h = int(w / target_ratio)
            top = int(h * 0.05)
            return pil_image.crop((0, top, w, min(top + new_h, h)))

    # اختيار أكبر وجه
    x, y, fw, fh = max(faces, key=lambda r: r[2] * r[3])
    face_cx = x + fw // 2

    # عرض القص بناءً على عرض الكتفين (لا الملاءمة للعرض الكامل)
    crop_w = int(fw / shoulder_ratio)
    crop_left = max(0, face_cx - crop_w // 2)
    crop_right = min(w, crop_left + crop_w)
    if crop_right - crop_left < crop_w:
        crop_left = max(0, crop_right - crop_w)

    # الارتفاع: الرأس ~22% من الإطار مع هامش علوي مريح (الرأس كامل داخل الإطار)
    head_h = int(fh / head_ratio)
    # هامش فوق الرأس يعادل نصف ارتفاع الرأس
    top_margin = int(fh * 0.5)
    crop_top = max(0, y - top_margin)
    crop_bottom = min(h, y + head_h - top_margin) if head_h > top_margin else h

    # ضبط النسبة إلى 3:4، مع التأكد من أن الرأس كامل داخل الإطار
    crop_w_actual = crop_right - crop_left
    crop_h_actual = crop_bottom - crop_top
    if crop_w_actual / crop_h_actual > target_ratio:
        new_h = int(crop_w_actual / target_ratio)
        # توسيع نحو الأسفل فقط كي يبقى الرأس العلوي ثابتاً
        wanted = crop_top + new_h
        crop_bottom = min(h, wanted)
        # إن لم نتسع للأسفل، وسّع للأعلى مع إبقاء الرأس كامل (بهامش علوي)
        if crop_bottom - crop_top < crop_w_actual / target_ratio:
            need = int(crop_w_actual / target_ratio) - (crop_bottom - crop_top)
            crop_top = max(0, crop_top - need)
            crop_bottom = min(h, crop_top + int(crop_w_actual / target_ratio))
    else:
        new_w = int(crop_h_actual * target_ratio)
        crop_left = max(0, face_cx - new_w // 2)
        crop_right = min(w, crop_left + new_w)

    result = pil_image.crop((crop_left, crop_top, crop_right, crop_bottom))
    logger.info("قصبطاقة الهوية: (%d×%d) → (%d×%d) مع وجه في المركز",
                w, h, result.size[0], result.size[1])
    return result