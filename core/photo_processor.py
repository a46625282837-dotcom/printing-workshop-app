import logging, os, sys, shutil
from PIL import Image

logger = logging.getLogger(__name__)

TARGET_DPI = 600
_MODEL_FILENAME = "u2netp.onnx"
_HOME_DIR = os.path.expanduser("~")
_MODEL_TARGET_DIR = os.path.join(_HOME_DIR, ".u2net")
_MODEL_TARGET_PATH = os.path.join(_MODEL_TARGET_DIR, _MODEL_FILENAME)

_rembg_session = None
_ort_session = None


def photo_px_size(w_mm, h_mm, dpi=TARGET_DPI):
    return int(w_mm / 25.4 * dpi), int(h_mm / 25.4 * dpi)


def _get_bundled_model_path():
    if getattr(sys, 'frozen', False):
        path = os.path.join(sys._MEIPASS, 'models', _MODEL_FILENAME)
    else:
        path = os.path.join(os.path.dirname(__file__), '..', 'models', _MODEL_FILENAME)
    if os.path.exists(path):
        return path
    return None


def _ensure_model_file():
    if os.path.exists(_MODEL_TARGET_PATH):
        return
    bundled = _get_bundled_model_path()
    if bundled:
        os.makedirs(_MODEL_TARGET_DIR, exist_ok=True)
        shutil.copy2(bundled, _MODEL_TARGET_PATH)
        logger.info("تم نسخ النموذج %s إلى %s", _MODEL_FILENAME, _MODEL_TARGET_DIR)
    else:
        logger.info("النموذج غير موجود في الحزمة، سيتم التحميل من الإنترنت")


def ensure_rembg_ready() -> bool:
    global _rembg_session
    try:
        os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
        _ensure_model_file()
        from rembg import new_session
        _rembg_session = new_session("u2netp")
        logger.info("نموذج rembg (u2netp) جاهز")
        return True
    except Exception as e:
        logger.warning("rembg غير متاح (%s)", e)
        return False


def _remove_bg_ai(pil_image: Image.Image) -> Image.Image:
    global _rembg_session
    os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
    _ensure_model_file()
    from rembg import remove as _ai_remove, new_session
    if _rembg_session is None:
        _rembg_session = new_session("u2netp")
    result = _ai_remove(pil_image, session=_rembg_session)
    logger.info("تمت إزالة الخلفية عبر AI (u2netp, %s)", pil_image.size)
    return result


def _remove_bg_direct(pil_image: Image.Image) -> Image.Image:
    global _ort_session
    import numpy as np
    _ensure_model_file()
    os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
    import onnxruntime
    if _ort_session is None:
        _ort_session = onnxruntime.InferenceSession(
            _MODEL_TARGET_PATH,
            providers=['CPUExecutionProvider'],
        )
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


def remove_background(pil_image: Image.Image) -> Image.Image:
    try:
        from rembg import remove as _ai_remove
        result = _remove_bg_ai(pil_image)
        if result.mode == "RGBA":
            alpha = result.getchannel("A")
            ext = alpha.getextrema()
            if ext[0] == 255 and ext[1] == 255:
                logger.warning("إزالة الخلفية عبر AI أنتجت alpha كلها 255 (فشل صامت)")
                raise ValueError("AI background removal produced all-255 alpha (silent failure)")
        return result
    except Exception as e:
        logger.warning("rembg فشل (%s), تجربة onnxruntime مباشرة", e)
        try:
            return _remove_bg_direct(pil_image)
        except Exception as e2:
            logger.warning("onnxruntime مباشرة فشل (%s), تجربة grabCut", e2)
            try:
                return _remove_bg_grabcut(pil_image)
            except Exception as e3:
                logger.error("جميع الطرق فشلت (%s), إرجاع الصورة بدون خلفية", e3, exc_info=True)
                return pil_image.convert("RGBA")


def composite_white_bg(pil_image: Image.Image) -> Image.Image:
    if pil_image.mode == "RGBA":
        bg = Image.new("RGB", pil_image.size, (255, 255, 255))
        bg.paste(pil_image, mask=pil_image.split()[3])
        logger.info("تم تركيب الصورة على خلفية بيضاء (%s)", pil_image.size)
        return bg
    if pil_image.mode != "RGB":
        return pil_image.convert("RGB")
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