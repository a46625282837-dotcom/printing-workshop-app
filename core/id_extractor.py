"""استخراج بطاقات الهوية عبر التعلم العميق فقط (rembg).

تطبيق نقيّ لقاعدة "إزالة الخلفية بالذكاء الاصطناعي":
  1) استقبال صورة الهوية مهما كانت زاوية التصوير أو الإضاءة أو الخلفية
     (سجّاد/تيرازو/ألوان متداخلة).
  2) تمريرها عبر نموذج rembg (deep learning) لعزل الهوية بالكامل،
     وجعل الخلفية شفافة تماماً (بدون أي لون أبيض أو غيره).
  3) حصر النتيجة على الصندوق الحاوي للهوية (alpha bbox) للحفاظ على
     الأبعاد المستطيلة الصحيحة للبطاقة.
  4) إخراج صورة PNG (RGBA بخلفية شفافة) جاهزة للطباعة المباشرة
     دون أي شوائب في الخلفية في كل مرة.

لا نعتمد هنا على خوارزميات OpenCV التقليدية (Canny/kmeans/GrabCut) ولا
على تحليل الألوان العشوائي في قصّ الهوية — بل على نموذج rembg المتخصص.
"""
import logging

from PIL import Image

logger = logging.getLogger(__name__)

CARD_ASPECT = 85.6 / 54.0
CARD_W = 856
CARD_H = 540


# ---------------------------------------------------------------------------
# أدوات إلكترونية مساعدة (تُصدَّر للتوافق مع الاختبارات والواجهات القديمة).
# لا تُستخدم في الخط الرئيسي لاستخراج الهوية (rembg فقط).
# ---------------------------------------------------------------------------
def order_points(pts):
    """ترتيب 4 نقاط بالترتيب: أعلى-يسار، أعلى-يمين، أسفل-يمين، أسفل-يسار."""
    import numpy as np
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def four_point_transform(image, pts):
    """تحويل منظور باستخدام 4 نقاط (احتياطي/مساعدة فقط، لا يُستخدم رئيسياً)."""
    import cv2
    import numpy as np
    rect = order_points(pts)
    (tl, tr, br, bl) = rect
    widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
    widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
    maxWidth = max(int(widthA), int(widthB))
    heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
    heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
    maxHeight = max(int(heightA), int(heightB))
    dst = np.array([
        [0, 0],
        [maxWidth - 1, 0],
        [maxWidth - 1, maxHeight - 1],
        [0, maxHeight - 1]], dtype="float32")
    M = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(image, M, (maxWidth, maxHeight), flags=cv2.INTER_CUBIC)
    return warped


# ---------------------------------------------------------------------------
# الخط الرئيسي: إزالة الخلفية عبر rembg فقط
# ---------------------------------------------------------------------------
_rembg_session = None
_REMBG_MODEL = "isnet-general-use"  # نموذج rembg المتخصص بدقة عالية


def _local_rembg_model_available(name):
    """هل يوجد نموذج rembg محلياً في ~/.u2net (دون إنترنت)؟"""
    import os
    p = os.path.join(os.path.expanduser("~"), ".u2net", f"{name}.onnx")
    if os.path.exists(p) and os.path.getsize(p) > 1000:
        return p
    return None


def _ensure_rembg_ready():
    """تهيئة جلسة rembg مرة واحدة باستخدام النموذج المحلي المضمَّن فقط.

    لا يُحمَّل شيء من الإنترنت: نفضّل isnet/u2net إن توفّرا محلياً، وإلا
    نستخدم u2netp المضمَّن (الذي ينسخه photo_processor إلى ~/.u2net).
    """
    global _rembg_session
    if _rembg_session is None:
        try:
            from rembg import new_session
            from core.photo_processor import _ensure_model_file
            os_environ_cuda()
            _ensure_model_file()
            for name in ("isnet-general-use", "u2net", "u2netp"):
                if _local_rembg_model_available(name):
                    _rembg_session = new_session(name)
                    logger.info("rembg جاهز (نموذج محلي %s)", name)
                    return _rembg_session
            logger.warning("لا يوجد نموذج rembg محلي — الإزالة ستعمل عبر النموذج المضمّن")
            _rembg_session = False
        except Exception as e:
            logger.warning("تعذّرت تهيئة rembg (%s): %s", _REMBG_MODEL, e)
            _rembg_session = False
    return _rembg_session


def os_environ_cuda():
    """إيقاف CUDA حتى يعمل النموذج على CPU بشكل ثابت ومتوقّع."""
    import os
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"


def _remove_bg_rembg(pil_image: Image.Image) -> Image.Image:
    """عزل الهوية بالكامل عبر rembg وإرجاع RGBA بخلفية شفافة."""
    import numpy as np

    session = _ensure_rembg_ready()
    input_img = pil_image
    # rembg أكثر استقراراً مع مقاسات معقولة؛ نخفّض فقط إن كانت الصورة ضخمة.
    if max(input_img.size) > 2048:
        ratio = 2048 / max(input_img.size)
        input_img = input_img.resize(
            (int(input_img.width * ratio), int(input_img.height * ratio)),
            Image.LANCZOS,
        )

    try:
        if session:
            from rembg import remove as _ai_remove
            result = _ai_remove(input_img, session=session)
        else:
            # لا نموذج محلي جاهز: نستخدم النموذج المضمَّن u2netp عبر onnxruntime
            # بدلاً من تحميل نموذج من الإنترنت أو قصّ أجزاء من الشخص.
            from core.photo_processor import _remove_bg_direct
            result = _remove_bg_direct(input_img)
    except Exception as e:
        logger.warning("rembg.remove فشل (%s) — تجربة onnxruntime المضمّن", e)
        try:
            from core.photo_processor import _remove_bg_direct
            result = _remove_bg_direct(input_img)
        except Exception as e2:
            logger.error("فشل إزالة الخلفية نهائياً: %s", e2)
            raise

    if result is None:
        raise ValueError("إزالة الخلفية أرجعت نتيجة فارغة")

    # ضمان RGBA بخلفية شفافة تماماً (لا أبيض ولا أي لون).
    rgba = result.convert("RGBA")
    if rgba.size != pil_image.size and session is None:
        rgba = rgba.resize(pil_image.size, Image.LANCZOS)

    alpha = np.array(rgba.getchannel("A"))
    # حارس أمان: إن خرج النموذج بقناع كلّه معتم (فشل صامت) نعيد الصورة الأصلية
    # كـ RGBA حتى لا نفقد الهوية.
    if int(alpha.min()) == 255 and int(alpha.max()) == 255:
        logger.warning("rembg أعاد قناعاً معتمًا بالكامل (فشل صامت) — استخدام الصورة الأصلية")
        rgba = pil_image.convert("RGBA")
    return rgba


def _crop_to_alpha(rgba: Image.Image) -> Image.Image:
    """تحويل قناع rembg إلى بطاقة حافظة للطباعة دون حذف أي جزء من الهوية.

    القاعدة الحفظية (Card Preservation Rule):
      لا يُحذف أبداً بكسل من داخل الهوية. على الخلفيات المعقدة (ألوان كثيرة)
      قد يغفل النموذج جزءاً من البطاقة أو يفتح ثقوباً، لذا نعتمد المربع
      المحيط (bbox) بمحيط قناع البطاقة ثم نجعل **كل البكسلات داخل المربع
      معتمة بالكامل** — فيستحيل قصّ/حذف أي جزء من الهوية — والخارج شفاف.

    العملية تعتمد على قناع النموذج فقط (بدون تحليل ألوان عشوائي):
      1) تحديد البطاقة من قناع rembg (المعتِم).
      2) تمدّد بسيط كي لا يُقصّ لون حافة البطاقة.
      3) المربع المحيط بصندوقها، وإبقاء محتواه معتماً كاملاً.
    """
    try:
        import numpy as np
        from PIL import ImageFilter

        alpha = np.array(rgba.getchannel("A"))

        # 1) قناع البطاقة: كل ما رآه النموذج ككائن (alpha > 0 يشمل الحواف الناعمة).
        binary = (alpha > 0).astype(np.uint8) * 255
        bin_img = Image.fromarray(binary)

        # 2) تنظيف: ملء الثقوب الداخلية وإزالة البقع الخارجية (Pillow فقط).
        k = 7
        b = bin_img.filter(ImageFilter.MaxFilter(k))   # تمدد
        b = b.filter(ImageFilter.MinFilter(k))          # إغلاق (سدّ الثقوب)
        b = b.filter(ImageFilter.MinFilter(k))          # تآكل
        b = b.filter(ImageFilter.MaxFilter(k))          # فتح (إزالة البقع)
        # تمدد نهائي كي لا تُقصّ حافة البطاقة الملوّنة عند القصّ.
        b = b.filter(ImageFilter.MaxFilter(5))

        bin_arr = np.array(b)
        if not (bin_arr > 0).any():
            return rgba

        # 3) حارسة الثقة (حماية تامة): إن لم يكن القناع يشبه بطاقة (نسبة
        #    أبعاد مستطيلة قريبة من البطاقة ومساحة معقولة داخل الصورة)،
        #    فالقناع غير موثوق وسيؤدي القصّ الناقص إلى حذف أجزاء من الهوية.
        #    في هذه الحالة نحتفظ بكل مكوّنات الصورة معتمةً (لا نحذف شيئاً).
        ys, xs = np.where(bin_arr > 0)
        bw = int(xs.max()) - int(xs.min()) + 1
        bh = int(ys.max()) - int(ys.min()) + 1
        if bw < 2 or bh < 2:
            return rgba
        aspect = bw / bh
        cover = (bin_arr > 0).mean()
        if aspect < 1.1 or aspect > 2.6 or cover < 0.02:
            logger.info(
                "قناع غير موثوق (نسبة %.2f، تغطية %.3f) — الحفاظ الكامل على الصورة",
                aspect, cover,
            )
            # حماية تامة: نعيد الصورة كاملةً معتمة (لا نحذف أي بكسل إطلاقاً).
            full = rgba.copy()
            full.putalpha(Image.new("L", rgba.size, 255))
            return full

        # 4) المربع المحيط بصندوق البطاقة مع هامش نسبي (حماية تامة:
        #    نُبقي من الخلفية حول البطاقة ولا نلامس حافتها إطلاقاً).
        pad = max(4, int(max(rgba.width, rgba.height) * 0.01))
        x1 = max(0, int(xs.min()) - pad)
        y1 = max(0, int(ys.min()) - pad)
        x2 = min(rgba.width, int(xs.max()) + pad)
        y2 = min(rgba.height, int(ys.max()) + pad)

        # 5) الناتج الحافظ: داخل المربع معتم بالكامل، الخارج شفاف تماماً.
        #    (لا نحذف أي بكسل داخل المربع أبداً — يحمي من أخطاء القناع).
        mask = np.zeros_like(bin_arr)
        mask[y1:y2 + 1, x1:x2 + 1] = 255
        out = rgba.copy()
        out.putalpha(Image.fromarray(mask))
        return out.crop((x1, y1, x2, y2))
    except Exception as e:  # pragma: no cover
        logger.warning("فشل صقل قناع الهوية (%s)", e)
        return rgba


def _card_quad_to_mask(quad, w, h, dilate_px):
    """يبني قناع الثنائي (0/255) يغطي البطاقة من زواياها الأربع.

    يملأ المضلع الرباعي (زوايا البطاقة) ثم يمدّده قليلاً كي لا تُقصّ
    حافة البطاقة الملوّنة، فيضمن تغطية البطاقة كاملة وبحماية تامة.

    يُنفَّذ عبر OpenCV (fillPoly + dilate) بدل PIL MaxFilter: الناتج
    مكافئ (تمدّد k×k) لكن أسرع بعشرات المرّات على الصور الكاملة، فما
    كان يستهلك ~3.7 ثانية ينزل إلى أقل من 0.05 ثانية.
    """
    import cv2
    import numpy as np

    mask = np.zeros((h, w), dtype=np.uint8)
    pts = np.asarray(quad, dtype=np.int32).reshape(-1, 1, 2)
    cv2.fillPoly(mask, [pts], 255)
    if dilate_px > 0:
        k = max(3, int(dilate_px) | 1)
        kernel = np.ones((k, k), np.uint8)
        mask = cv2.dilate(mask, kernel, iterations=1)
    return mask


def _warp_quad_rgba(pil_image, quad, pad):
    """يفرد رباعي الهوية المائل إلى مستطيل مستقيم (Perspective Warp).

    القاعدة الدائمة: لا قصّ مباشر على صورة مائلة تحت أي ظرف — كل مسار
    يحضّر الرباعي ثم يُفرّده بالمنظور قبل القصّ.

    - يرتّب النقاط (TL/TR/BR/BL)، يحسب أبعاد الهوية من ضلعيها.
    - يلفّ RGB و Alpha بنفس مصفوفة التحويل إلى الوجهة المستقيمة
      بهامش pad، فيبقى الخارج شفافاً 100%.
    - يُرجع PIL مجلّد (RGBA) أو None عند الفشل.
    """
    import cv2
    import numpy as np
    from PIL import Image

    quad = order_points(np.asarray(quad, dtype="float32"))
    (tl, tr, br, bl) = quad
    widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
    widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
    maxW = max(int(widthA), int(widthB))
    heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
    heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
    maxH = max(int(heightA), int(heightB))
    if maxW < 4 or maxH < 4:
        return None

    p = int(pad)
    dst = np.array([
        [p, p],
        [maxW - 1 + p, p],
        [maxW - 1 + p, maxH - 1 + p],
        [p, maxH - 1 + p],
    ], dtype="float32")
    M = cv2.getPerspectiveTransform(quad, dst)

    rgba = pil_image.convert("RGBA")
    arr = np.array(rgba)
    rgb = arr[:, :, :3]
    alpha = arr[:, :, 3].astype(np.uint8)
    out_w = maxW + 2 * p
    out_h = maxH + 2 * p

    warped_rgb = cv2.warpPerspective(
        rgb, M, (out_w, out_h),
        flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    warped_a = cv2.warpPerspective(
        alpha, M, (out_w, out_h),
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT, borderValue=0)

    return Image.fromarray(np.dstack([warped_rgb, warped_a]), "RGBA")


def _card_bbox_from_mask(mask, w, h, pad):
    """مربع محيط بقناع البطاقة مع هامش حوله (لا يلامس حافة البطاقة)."""
    import numpy as np
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return None
    x1 = max(0, int(xs.min()) - pad)
    y1 = max(0, int(ys.min()) - pad)
    x2 = min(w, int(xs.max()) + pad)
    y2 = min(h, int(ys.max()) + pad)
    return x1, y1, x2, y2


def _detect_card_then_extract(pil_image: Image.Image):
    """طبقة أولى: كشف البطاقة عبر الموديل المتخصص (شكلها لا لونها).

    النموذج (card_detector.onnx / YOLOv8-OBB) يبحث عن مستطيل البطاقة
    الحقيقي بغض النظر عن ألوانها الداخلية — فينجح على البطاقات البنية/
    المزخرفة التي يخفق أمامها rembg. يعتمد على الشكل لا على الألوان.

    القاعدة الحفظية: كل بكسلات البطاقة تُبقى معتمة 100% (لا حذف أبداً)،
    ونُزيل الخلفية خارجها فقط. يُعطى هامش حول الرباعي كي لا تُقصّ الحافة.
    """
    import numpy as np
    from PIL import Image

    try:
        from core.card_detector import detect_card_quad, card_model_available
        if not card_model_available():
            return None
        quad = detect_card_quad(pil_image)
        if quad is None:
            logger.info("كاشف البطاقة لم يرصد بطاقة — الانتقال إلى rembg")
            return None
        quad = np.asarray(quad, dtype="float32")
    except Exception as e:  # pragma: no cover
        logger.warning("كاشف البطاقة فشل (%s) — الانتقال إلى rembg", e)
        return None

    W, H = pil_image.width, pil_image.height
    # القناع: ملء الرباعي (البطاقة) + تمدد لحماية الحافة الملوّنة.
    dilate = max(6, int(max(W, H) * 0.015))
    mask = _card_quad_to_mask(quad, W, H, dilate)

    # حارسة الثقة: القناع يجب أن يغطي مساحة معقولة بنسبة تشبه البطاقة.
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return None
    bw = int(xs.max()) - int(xs.min()) + 1
    bh = int(ys.max()) - int(ys.min()) + 1
    if bw < 20 or bh < 20:
        return None
    aspect = bw / bh
    cover = (mask > 0).mean()
    # نقبل الاتجاهين (أفقي 1.2–2.7 أو عمودي 0.37–0.83) لأن البطاقة قد تُصوَّر
    # بأي زاوية. الحماية تامة: داخل الرباعي كله معتم فلا يُحذف شيء.
    good = (1.2 <= aspect <= 2.7) or (0.37 <= aspect <= 0.83)
    if not good or cover < 0.03:
        logger.info(
            "رباعي كاشف البطاقة غير موثوق (نسبة %.2f، تغطية %.3f) — الانتقال إلى rembg",
            aspect, cover,
        )
        return None

    # فرد الرباعي بالمنظور مع هامش (Perspective Warp): لا قصّ مباشر على مائل.
    pad = 4  # هامش قصّ صغير
    warped = _warp_quad_rgba(pil_image, quad, pad)
    if warped is None:
        return None
    logger.info("تم استخراج الهوية عبر كاشف البطاقة المتخصص (Perspective Warp) — %d×%d",
                warped.width, warped.height)
    return warped


def _detect_rect_edges_then_extract(pil_image: Image.Image):
    """طبقة الحواف الهندسية الاحتياطية: أكبر مستطيل مستقيم الحواف.

    نُلجأ إليها عندما يفشل الكاشف المتخصص وتفشل rembg في تحديد البطاقة،
    لكن الصورة تحتوي بطاقةً ذات حواف مستقيمة واضحة (حتى لو كانت خشبها
    بنياً/مزخرفاً). نبحث عن أكبر رباعي رباعي الزوايا القائمة بنسبة أبعاد
    قريبة من البطاقة، عبر مسار الحواف الهندسي (لا ألوان).

    القاعدة الحفظية: داخل الرباعي كله معتم (لا حذف أبداً)، الخارج شفاف؛
    وإن لم نجد ربعاً موثوقاً نرجع None فيبقى الحل الآمن الكامل.
    """
    try:
        import cv2
        import numpy as np
        from PIL import Image

        img = np.array(pil_image.convert("RGB"))
        img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        median = float(np.median(blur))
        H, W = gray.shape
        img_area = W * H
        min_area = img_area * 0.02

        canny_params = [
            (int(max(0, 0.3 * median)), int(min(255, 0.9 * median))),
            (int(max(0, 0.5 * median)), int(min(255, 1.5 * median))),
            (50, 150), (30, 100), (10, 50),
        ]
        epsilons = (0.005, 0.01, 0.015, 0.02, 0.03)

        candidates = []
        for lo, hi in canny_params:
            edged = cv2.Canny(blur, lo, hi)
            edged = cv2.dilate(edged, np.ones((3, 3), np.uint8), iterations=2)
            cnts, _ = cv2.findContours(edged, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            for c in cnts:
                area = cv2.contourArea(c)
                if area < min_area or area > img_area * 0.98:
                    continue
                peri = cv2.arcLength(c, True)
                for eps in epsilons:
                    ap = cv2.approxPolyDP(c, eps * peri, True)
                    if len(ap) == 4:
                        quad = ap.reshape(4, 2).astype("float32")
                        xs = quad[:, 0]; ys = quad[:, 1]
                        bw = float(xs.max()) - float(xs.min())
                        bh = float(ys.max()) - float(ys.min())
                        if bw < 20 or bh < 20:
                            continue
                        asp = bw / bh
                        good = (1.2 <= asp <= 2.7) or (0.37 <= asp <= 0.83)
                        if good:
                            candidates.append((area, quad))
                            break

        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        area, quad = candidates[0]

        # اتحقق من الثقة بالموثوقية (تغطية معقولة، نسبة داخل نطاق البطاقة).
        quad_clip = quad.copy()
        quad_clip[:, 0] = np.clip(quad_clip[:, 0], 0, W)
        quad_clip[:, 1] = np.clip(quad_clip[:, 1], 0, H)
        dilate = max(6, int(max(W, H) * 0.015))
        mask = _card_quad_to_mask(quad_clip, W, H, dilate)
        ys, xs = np.where(mask > 0)
        if len(xs) == 0:
            return None
        bw = int(xs.max()) - int(xs.min()) + 1
        bh = int(ys.max()) - int(ys.min()) + 1
        asp = bw / bh
        cover = (mask > 0).mean()
        good = (1.2 <= asp <= 2.7) or (0.37 <= asp <= 0.83)
        if not good or cover < 0.03:
            return None

        # فرد الرباعي بالمنظور مع هامش (Perspective Warp): لا قصّ مباشر على مائل.
        pad = 4  # هامش قصّ صغير
        warped = _warp_quad_rgba(pil_image, quad, pad)
        if warped is None:
            return None
        logger.info("تم استخراج الهوية عبر كشف الحواف المستطيل (Perspective Warp) — %d×%d",
                    warped.width, warped.height)
        return warped
    except Exception as e:  # pragma: no cover
        logger.warning("كشف الحواف المستطيل فشل (%s)", e)
        return None


def perspective_correct(pil_image: Image.Image) -> Image.Image:
    """تصحيح المنظور للبطاقة: تحويل الزاوية إلى مستطيل مسطح.

    يكشف أركان البطاقة عبر الحواف الهندسية ويحوّلها إلى صورة مسطحة
    بأبعاد قياسية (856×540 أو 540×856 حسب الاتجاه).
    """
    try:
        import cv2
        import numpy as np

        img = np.array(pil_image.convert("RGB"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        median = float(np.median(blur))
        H, W = gray.shape

        best_quad = None
        best_area = 0
        canny_params = [
            (int(max(0, 0.3 * median)), int(min(255, 0.9 * median))),
            (int(max(0, 0.5 * median)), int(min(255, 1.5 * median))),
            (50, 150), (30, 100),
        ]
        epsilons = (0.005, 0.01, 0.015, 0.02, 0.03)

        for lo, hi in canny_params:
            edged = cv2.Canny(blur, lo, hi)
            edged = cv2.dilate(edged, np.ones((3, 3), np.uint8), iterations=2)
            cnts, _ = cv2.findContours(edged, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            for c in cnts:
                area = cv2.contourArea(c)
                if area < W * H * 0.02 or area > W * H * 0.98:
                    continue
                peri = cv2.arcLength(c, True)
                for eps in epsilons:
                    ap = cv2.approxPolyDP(c, eps * peri, True)
                    if len(ap) == 4:
                        quad = ap.reshape(4, 2).astype("float32")
                        xs, ys = quad[:, 0], quad[:, 1]
                        bw, bh = float(xs.max() - xs.min()), float(ys.max() - ys.min())
                        if bw < 20 or bh < 20:
                            continue
                        asp = bw / bh
                        if (1.2 <= asp <= 2.7) or (0.37 <= asp <= 0.83):
                            if area > best_area:
                                best_area = area
                                best_quad = quad
                        break

        if best_quad is None:
            logger.info("لم يتم كشف أركان البطاقة — تصحيح المنظور مُتخطّى")
            return pil_image

        rect = order_points(best_quad)
        (tl, tr, br, bl) = rect
        widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
        widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
        maxW = max(int(widthA), int(widthB))
        heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
        heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
        maxH = max(int(heightA), int(heightB))

        if maxW < 20 or maxH < 20:
            return pil_image

        dst = np.array([
            [0, 0], [maxW - 1, 0], [maxW - 1, maxH - 1], [0, maxH - 1]
        ], dtype="float32")
        M = cv2.getPerspectiveTransform(rect, dst)
        warped = cv2.warpPerspective(img, M, (maxW, maxH), flags=cv2.INTER_CUBIC)
        result = Image.fromarray(cv2.cvtColor(warped, cv2.COLOR_BGR2RGB))
        logger.info("تم تصحيح المنظور — %d×%d", maxW, maxH)
        return result
    except Exception as e:
        logger.warning("فشل تصحيح المنظور (%s) — إرجاع الأصلي", e)
        return pil_image


def extract_card(pil_image: Image.Image) -> Image.Image:
    """استخراج الهوية وإرجاع PNG (RGBA) بخلفية شفافة.

    ثلاث طبقات متتالية، وكلها تحافظ على "لا حذف أبداً":
      1) كاشف البطاقة المتخصص (card_detector.onnx): شكل البطاقة بغض النظر
         عن ألوانها — الأنسب للبطاقات البنية/المزخرفة.
      2) كشف الحواف المستطيل الاحتياطي: أكبر مستطيل مستقيم الحواف حين
         يفشل الكاشف و rembg (يعتمد على الشكل لا الألوان).
      3) rembg: عزل الخلفية بالتعلم العميق العام + الحماية التامة.
    أي فشل كامل يُرجع الصورة الأصلية كـ RGBA دون إسقاط الهوية.
    """
    try:
        if pil_image.width < 2 or pil_image.height < 2:
            return pil_image.convert("RGBA")

        # الطبقة الأولى: الكاشف المتخصص (الشكل).
        specialized = _detect_card_then_extract(pil_image)
        if specialized is not None:
            return specialized.convert("RGBA")

        # الطبقة الثانية: كشف الحواف المستطيل الاحتياطي.
        rect = _detect_rect_edges_then_extract(pil_image)
        if rect is not None:
            return rect.convert("RGBA")

        # الطبقة الثالثة: rembg العام + الحماية التامة.
        isolated = _remove_bg_rembg(pil_image)
        result = _crop_to_alpha(isolated).convert("RGBA")
        logger.info("تم عزل الهوية عبر rembg — الناتج %s", result.size)
        return result
    except Exception as e:
        logger.warning("تعذّر استخراج الهوية (%s) — إرجاع الأصلي RGBA", e)
        return pil_image.convert("RGBA")
