"""الوكيل المحلي: YOLOv8-nano لقصّ الهوية على الجهاز بلا إنترنت.

- يُحمَّل ملف النموذج المدرَّب (.pt) عبر ultralytics مرة واحدة (singleton).
- يكشف مربّع البطاقة (أعلى ثقة) ثم يقصّه هندسياً عبر Pillow إلى مستطيل
  A4 (95×55mm) بنفس نظام القصّ المستخدم في بقية الوكيلات.
- في النسخة المجمّعة (بدون ultralytics) يعمل كشف المحتوى عبر نموذج ONNX
  المضمّن (card_detector) تلقائياً.
- إن لم يوجد ملف النموذج أو تعطّل تحميله: يقع القصّ تلقائياً على الحواف.

سرعة فائقة: نموذج nano (~4M معاملات) يعمل على المعالج مباشرة بلا إنترنت.
"""

import json
import logging
import os
import threading

from typing import Optional

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


def resource_path(rel: str) -> str:
    """مسار مورد داخل التطبيق المجمّع أو المصدر — مفوَّض إلى الطبقة الموحّدة.

    في النسخة المجمّعة (PyInstaller) يُربط كل نموذج/ملف إعداد من جذر
    ``_internal`` إن وُجد، وإلا من المسار المصدر — نفس ما كان يعمل به سابقاً
    تماماً، لكن عبر ``core.runtime`` كامرجع وحيد يمنع التشتت.
    """
    from core.runtime import resource_path as _unified
    return _unified(rel)


# الموردان المحلّيان يمرّان عبر resource_path دائماً، فيُفهم البناء المجمّع
# الملفات من _internal\models تماماً كما يفهمها Python الخام من models\.
CONFIG_PATH = resource_path(os.path.join("core", "ai_config.json"))
DEFAULT_MODEL_PATH = resource_path(os.path.join("models", "id_card.pt"))
DEFAULT_ONNX_MODEL_PATH = resource_path(os.path.join("models", "id_card.onnx"))

# ذاكرة مؤقتة للنموذج المحمّل (تجنّب إعادة التحميل الثقيل لكل صورة)
_model = None
_model_path_loaded = None
_model_lock = threading.Lock()
# قفل الاستدلال: ultralytics model.predict غير آمن للخيوط، والقصّ يعمل بعدّة
# عمال متزامنين — يُسلسَل الاستدلال فلا تسقط صور عشوائياً عند الإقلاع.
_infer_lock = threading.Lock()


def _cfg() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_local_model_path() -> str:
    """مسار ملف النموذج المحلي (افتراضياً: models/id_card.pt بجانب التطبيق)."""
    return _cfg().get("local_model_path") or DEFAULT_MODEL_PATH


def set_local_model_path(path: str) -> None:
    data = _cfg()
    data["local_model_path"] = (path.strip() or DEFAULT_MODEL_PATH)
    _save(data)


def has_local_model() -> bool:
    """يتحقق من توفر نموذج YOLO Segment الفعلي (الطرف الأساسي للقصّ).

    - .pt (عبر ultralytics) إن وُجد الملف، أو
    - id_card.onnx (نسخة مجمّعة بلا ultralytics) — نفس النموذج المدرَّب.
    """
    p = get_local_model_path()
    if p and os.path.isfile(p):
        return True
    try:
        from core.yolo_onnx import onnx_model_available
        return onnx_model_available()
    except Exception:
        return False


def _onnx_model():
    """نموذج YOLO Segment عبر onnxruntime (id_card.onnx) — للمبني المجمّع بلا ultralytics."""
    try:
        from core.yolo_onnx import predict_onnx, onnx_model_available
        if not onnx_model_available():
            return None
    except Exception as e:
        logger.warning("ONNX YOLO unavailable: %s", e)
        return None
    return predict_onnx


def _onnx_model():
    """نموذج YOLO Segment عبر onnxruntime (id_card.onnx) — للمبني المجمّع بلا ultralytics."""
    try:
        from core.yolo_onnx import predict_onnx, onnx_model_available
        if not onnx_model_available():
            return None
    except Exception as e:
        logger.warning("ONNX YOLO unavailable: %s", e)
        return None
    return predict_onnx


class _OnnxYoloModel:
    """غلاف يطابق واجهة model.predict() الخاصة بـ ultralytics فوق predict_onnx.

    يتيح لكاشف القناع (id_card.onnx) العمل مباشرة بكل مسارات local_yolo
    (البحث عن الزوايا، القصّ بالقناع...) دون أي تعديل إضافي."""

    def predict(self, source=None, conf=0.6, imgsz=640, verbose=False, **kw):
        from core.yolo_onnx import predict_onnx
        if source is None and "img" in kw:
            source = kw["img"]
        return predict_onnx(source, conf=conf, imgsz=imgsz, verbose=verbose)


def get_model():
    """يُحمّل نموذج YOLO مرة واحدة ويعيده، أو None عند غياب/فشل التحميل.

    - في بيئة بايثون (ultralytics مثبّتة): النموذج .pt عبر ultralytics.
    - في النسخة المجمّعة (بدون ultralytics): نموذج يولو Segment id_card.onnx
      عبر onnxruntime — نفس الكشف/القناع بنتائج مطابقة لنسخة بايثون.

    محميّ بقفل مع فحص مزدوج: الخيوط المتزامنة (العدة العاملة في القصّ) تنتظر
    اكتمال التحميل الأول بدل تحميل النموذج عدّة مرات concurrently (غير آمن)
    أو رؤية قيمة نصف جاهزة — فيمنع سقوط بعض الصور عند الإقلاع الأول.
    """
    global _model, _model_path_loaded
    path = get_local_model_path()
    if _model is not None and _model_path_loaded == path:
        return _model
    with _model_lock:
        if _model is not None and _model_path_loaded == path:
            return _model
        # مسار النموذج المطلوب: .pt مُحدَّد أو بديل ONNX المضمّن
        if not os.path.isfile(path):
            logger.info("لا يوجد ملف نموذج محلي عند: %s", path)
            # البناء المجمّع: لا يوجد .pt هنا لكن يوجد id_card.onnx → نعتمد عليه
            if _onnx_model() is not None:
                _model = _OnnxYoloModel()
                _model_path_loaded = "_onnx_"
                logger.info("تم تحميل نموذج YOLO Segment (ONNX): %s",
                            DEFAULT_ONNX_MODEL_PATH)
                return _model
            _model = None
            _model_path_loaded = None
            return None
        try:
            from ultralytics import YOLO

            model = YOLO(path)
            _model = model
            _model_path_loaded = path
            logger.info("تم تحميل نموذج YOLOv8 المحلي: %s", path)
            return model
        except Exception as e:
            _model = None
            _model_path_loaded = None
            logger.warning("فشل تحميل نموذج YOLO المحلي (%s): %s", path, e)
            # في النسخة المجمّعة تفشل الاستيراد داخلياً → ننتقل للـ ONNX Segment
            if _onnx_model() is not None:
                _model = _OnnxYoloModel()
                _model_path_loaded = "_onnx_"
                logger.info("استُعمل نموذج YOLO Segment (ONNX) بديلاً عن .pt الفاشل: %s",
                            DEFAULT_ONNX_MODEL_PATH)
                return _model
            return None


def _to_numpy(x):
    """يتساهل مع ناتج yolov8 سواء كان Tensor أو numpy (يُسهل الاختبار والتصحيح)."""
    if hasattr(x, "cpu"):
        x = x.cpu()
    if hasattr(x, "detach"):
        x = x.detach()
    return np.asarray(x) if not isinstance(x, np.ndarray) else x


def _rotation_sources(img_rgb):
    """توليد (زاوية، صورة RGB) للكشف عبر الإتجاهات الأفقية (0/270/90)."""
    yield 0, img_rgb
    im = Image.fromarray(img_rgb)
    yield 270, np.asarray(im.rotate(270, expand=True))
    yield 90, np.asarray(im.rotate(90, expand=True))


def _rotated_rgb(img_rgb, angle):
    if angle == 0:
        return img_rgb
    return np.asarray(Image.fromarray(img_rgb).rotate(angle, expand=True))


def _best_detection(img_rgb, conf=0.6) -> Optional[list]:
    """أفضل (ثقة، زاوية، مربع، مضلع) عبر الزوايا الثلاث عند عتبة conf، أو None.

    - المضلع (polygon) يُؤخذ من masks.xy — إحداثياته **بالمقياس الأصلي**
      للصورة المدوّرة (لا يُعتمد على masks.data لأنه بمقياس 640×640).
    - مع نموذج كشف عادي (بدون Segmentation) يكون المضلع None.
    """
    model = get_model()
    if model is None:
        return None
    best = None
    # الكشف الأفقي أولاً — الغالبية الساحقة أفقيّة بلا تدوير، فيتوقف فوراً
    angles = [0, 270, 90]
    for angle in angles:
        src = img_rgb if angle == 0 else _rotated_rgb(img_rgb, angle)
        with _infer_lock:
            results = model.predict(source=src, conf=conf, imgsz=640, verbose=False)
        if results and results[0].boxes is not None and len(results[0].boxes) > 0:
            boxes = results[0].boxes
            xyxy = _to_numpy(boxes.xyxy).reshape(-1, 4)
            confs = _to_numpy(boxes.conf).reshape(-1)
            i = int(confs.argmax())
            poly = None
            masks = getattr(results[0], "masks", None)
            if masks is not None and getattr(masks, "xy", None) is not None:
                try:
                    p = np.asarray(masks.xy[i], dtype=float)
                    if p is not None and len(p) >= 3:
                        poly = p
                except Exception:
                    poly = None
            cand = (float(confs[i]), angle, [float(v) for v in xyxy[i]], poly)
            if best is None or cand[0] > best[0]:
                best = cand
            # كشف أفقي واثق = البطاقة أفقية — لا حاجة لتدوير الصورة بعد الآن
            if angle == 0 and cand[0] >= conf:
                break
    return best


SAFE_PAD = 4  # هامش قصّ صغير (بكسل) — يُطبق على كل مسارات القصّ

# هامش البديل البسيط (Standard Bounding Box) حصرياً عند فشل فحص القناع/الـ Warp
FALLBACK_PAD = 5

# تمديد ثابت لأبعاد وجهة الـ Warp (1.5%) ليمتلئ الإطار بلا فراغات محسوسة
WARP_DEST_SCALE = 1.015

# إطار عريض مؤقت حول الصورة يسبق أي استخراج زوايا/قصّ: الهوية الملتصقة بأطراف
# الصورة (بلا اتساع جانبي) تجعل نسبة مساحة القناع/الصورة تتجاوز CARD_REL_MAX فيفشل
# التحقق ويهبط الـ Warp. توسعة الصورة بهذا الحد يمنح القناع فراغاً كافياً حوله
# فتنجح استخراج الزوايا وتعديل الميلان دائماً — والإطار لا يظهر في الناتج.
CARD_FRAME_MARGIN = 50

# عتبات التحقق الوقائي قبل warpPerspective — بطاقة هوية أفقية بجوار 85.6×54mm
CARD_ASPECT_MIN = 1.15   # أصغر نسبة عرض/طول مقبولة
CARD_ASPECT_MAX = 2.40   # أكبر نسبة عرض/طول مقبولة
CARD_FILL_MIN = 0.40     # المضلع يجب أن يملأ رباعي الزوايا على الأقل بهذه النسبة
CARD_FILL_MAX = 1.02     # وألا يتجاوزه عملياً (مضلع ممزق/محدّب بالخطأ)
CARD_REL_MIN = 0.01      # مساحة البطاقة ≥ 1% من مساحة الصورة
CARD_REL_MAX = 0.95      # ولا تعادل الصورة كاملة (كشف خاطئ للخلفية)


def _id_card_quad_valid(image, rect_pts, contour):
    """تحقق أمان قبل تطبيق warpPerspective على رباعي البطاقة.

    يرفض الـwarp عند:
    1) ناتج عمودي (العرض < الطول) = البطاقة مقلوبة عرضياً (transposed).
    2) نسبة العرض/الطول خارج نطاق بطاقة الهوية (1.15–2.4).
    3) مساحة المضلع صغيرة جداً/كبيرة جداً مقارنة برباعي الزوايا (نسبة التعبئة
       خارج 0.40–1.02) أو مقارنة بالصورة كاملة (خارج 1%–95%).

    عند الرفض يستعيض التطبيق تلقائياً بصندوق أفقي آمن (boundingRect).
    يُرجع True إذا كان الرباعي جديراً بالتفرد بالمنظور."""
    height_img, width_img = image.shape[:2]
    if height_img < 4 or width_img < 4:
        return False
    import cv2
    (tl, tr, br, bl) = rect_pts
    w_top = float(np.hypot(tr[0] - tl[0], tr[1] - tl[1]))
    w_bot = float(np.hypot(br[0] - bl[0], br[1] - bl[1]))
    h_left = float(np.hypot(bl[0] - tl[0], bl[1] - tl[1]))
    h_right = float(np.hypot(br[0] - tr[0], br[1] - tr[1]))
    w = (w_top + w_bot) / 2.0
    h = (h_left + h_right) / 2.0
    if w < 4 or h < 4:
        return False
    # 1) البطاقة عَرْضية — أي ناتج عمودي = مقلوب/معكوس
    if w < h:
        logger.warning("تحقق البطاقة: ناتج عمودي (مقلوب عرضياً) w=%.1f h=%.1f", w, h)
        return False
    # 2) نسبة الأبعاد ضمن نطاق بطاقة الهوية
    aspect = w / h
    if not (CARD_ASPECT_MIN <= aspect <= CARD_ASPECT_MAX):
        logger.warning("تحقق البطاقة: نسبة أبعاد شاذة %.2f (%s)", aspect,
                       "أقل من الطبيعي" if aspect < CARD_ASPECT_MIN else "أعلى من الطبيعي")
        return False
    # 3) مساحة المضلع قريبة من رباعيه (نسبة التعبئة) ومناسبة للصورة
    area = float(cv2.contourArea(contour))
    if area <= 0:
        return False
    fill = area / (w * h)
    if not (CARD_FILL_MIN <= fill <= CARD_FILL_MAX):
        logger.warning("تحقق البطاقة: مساحة مضلع غير متسقة مع الزوايا %.3f", fill)
        return False
    rel = area / (float(height_img) * width_img)
    if not (CARD_REL_MIN <= rel <= CARD_REL_MAX):
        logger.warning("تحقق البطاقة: مساحة البطاقة %.3f من مساحة الصورة خارج النطاق", rel)
        return False
    return True


def _bbox_fallback_crop(image, contour, pad=FALLBACK_PAD):
    """Standard Bounding Box: أصغر مستطيل أفقي (boundingRect + هامش بسيط).

    يُطبق حصرياً عند فشل فحص القناع/الـ Warp لضمان قصّ طبيعي. يضمن أبداً
    عدم إرجاع None: قناع تالف/غير عددي (NaN) أو ناتج فارغ ⇒ يُرجَع نسخة
    من الصورة كاملة (الأخير الاحتياطي الطبيعي)."""
    import cv2
    try:
        if not np.all(np.isfinite(contour)):
            logger.warning("Safe Guard: إحداثيات قناع غير عددية (NaN) — صورة كاملة")
            return image.copy()
        x, y, w, h = cv2.boundingRect(contour)
        if w < 1 or h < 1:
            return image.copy()
        img_h, img_w = image.shape[:2]
        x0 = max(0, x - pad)
        y0 = max(0, y - pad)
        x1 = min(img_w, x + w + pad)
        y1 = min(img_h, y + h + pad)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return image.copy()
        return image[y0:y1, x0:x1].copy()
    except Exception as e:
        logger.warning("Safe Guard: خطأ في القصّ البسيط (%s) — صورة كاملة", e)
        return image.copy()


def order_card_corners(points):
    """واجهة عامة: ترتيب النقاط الأربع الحقيقية للبطاقة بأي انحراف.

    هندسة بحتة من زوايا أقصر مستطيل مائل (minAreaRect): تُخرج
    TL/TR/BR/BL بتوجيه عَرْضي دائم (الضلع الطويل = العرض) بلا انقلاب
    حتى ~90°."""
    import cv2
    pts = np.asarray(points, dtype=np.float32).reshape(4, 2)
    pts = pts.astype(np.float32)
    try:
        rect = cv2.minAreaRect(pts.copy().reshape(-1, 1, 2))
    except Exception:
        rect = None
    if rect is not None:
        pts = cv2.boxPoints(rect).astype(np.float32)
    return _landscape_order(pts)


def _landscape_order(rect_pts):
    """TL/TR/BR/BL ثم توجيه عَرْضي دائم (الضلع الطويل = العرض).

    ترتيب رباعي محدّب بأي انحراف عبر الزاوية حول المركز (atan2) ثم فرز CCW،
    فتبقى الحواف متجاورة فعلياً بأي ميلان حتى ~90°. البداية = أعلى نقطة
    (أصغر y، ثم أصغر x عند التساوي) فتخرج البطاقة باتجاه "أعلى الصورة"
    بلا انقلاب 180° عند الميلان الحاد. CCW يحفظ الاتجاهية (det المنظور موجب)
    فلا تُنتج صورة معكوسة أبداً."""
    pts = np.asarray(rect_pts, dtype=np.float32).reshape(4, 2)
    c = pts.mean(axis=0)
    ang = np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0])
    ordered = pts[np.argsort(ang)]
    ys = ordered[:, 1]
    k = int(np.argmin(ys))
    if abs(ys[k] - ys[(k + 3) % 4]) < 1e-6:
        if ordered[(k + 3) % 4, 0] < ordered[k, 0]:
            k = int((k + 3) % 4)
    ordered = np.roll(ordered, -k, axis=0)
    tl, tr, br, bl = ordered
    top = float(np.hypot(tr[0] - tl[0], tr[1] - tl[1]))
    side = float(np.hypot(br[0] - tr[0], br[1] - tr[1]))
    if top < side:
        tl, tr, br, bl = bl, tl, tr, br
    return np.array([tl, tr, br, bl], dtype=np.float32)


def auto_warp_id_card(image, contour, used_bbox_out=None, corners_out=None):
    """تفريدة الهوية عبر Perspective Warp — هندسة رياضية بحتة.

    القاعدة الحصرية والوحيدة: **زوايا أقصر مستطيل مائل يحيط بالهوية
    (cv2.minAreaRect على convexHull للقناع)** تُستخرج زاوية الميلان بدقة
    تامة بغضّ النظر عن عيوب/اعوجاج قناع YOLO، ثم warpPerspective من هذه
    الزوايا الأربع فقط إلى مستطيل أفقي مستقيم — فيخرج كل شيء أفقياً
    مستقيماً 100% (حتى المائل أو الملتصق بأطراف الصورة) وكأنه ممسوح ضوئياً.

    used_bbox_out (اختياري): قائمة تُملأ بـ [True] إذا هبطت النتيجة إلى
    البديل البسيط (Safe Guard) — ليعرف المتصل أن الميلان لم يُعدَّل فيلجأ
    إلى استخراج الزوايا من محتوى الصورة.

    خطوات:
    0. إطار عريض (CARD_FRAME_MARGIN=50) حول الصورة + إزاحة القناع بنفس
       المقدار — للهوية الملتصقة بأطراف الصورة (نسبتها تتجاوز عتبة التحقق)
       فيُمنحها فراغاً فتنجح استخراج الزوايا دائماً؛ والإطار لا يظهر في الناتج.
    1. convexHull للقناع يمسح تعرّجات الحافة، ثم minAreaRect يجد أقصر مستطيل
       مائل يحيط بها → الزوايا الأربع الحقيقية بدقة Sub-Pixel (التعريف
       الرياضي لأطراف البطاقة، مستقر لأي ميلان/ضوضاء). هذا المصدر الوحيد.
    2. _landscape_order تُخرجها TL/TR/BR/BL بتوجيه عَرْضي دائم بلا انقلاب.
    3. warpPerspective حصرياً من هذه الزوايا إلى مستطيل أفقي مستقيم (مع
       تمديد 1.5% ثابت ليمتلئ الإطار بلا فراغات محسوسة).
    4. Safe Guard: قناع تالف/مقلوب (ارتفاع>>عرض)/أي خطأ ⇒ قصّ مستطيل بسيط
       (boundingRect) بهامش FALLBACK_PAD (لا None أبداً).
    """
    import cv2

    def _safe_bbox():
        if used_bbox_out is not None:
            used_bbox_out[:] = [True]
        return _bbox_fallback_crop(image, contour, pad=FALLBACK_PAD)

    try:
        if cv2.contourArea(contour) <= 0 or not np.all(np.isfinite(contour)):
            logger.warning("Safe Guard: قناع تالف (صفر/مفقود/NaN) — قصّ بسيط (Standard Bounding Box)")
            return _safe_bbox()

        m = int(CARD_FRAME_MARGIN)
        border_img = cv2.copyMakeBorder(
            image, m, m, m, m,
            borderType=cv2.BORDER_CONSTANT, value=(255, 255, 255))
        contour_shift = (contour.astype(np.float32) + m)

        # 1) أقصر مستطيل مائل يحيط بالبدن المحدّب — مصدر الزوايا الأربع الحقيقي
        hull = cv2.convexHull(contour_shift)
        rect = cv2.minAreaRect(hull)
        rect_pts = _landscape_order(cv2.boxPoints(rect))

        # Safe Guard: نسبة أبعاد/مساحة غير منطقية قبل البدء بالـ Warp
        if not _id_card_quad_valid(border_img, rect_pts, hull):
            logger.warning("Safe Guard: قناع ملتوٍ/أبعاد غير منطقية — قصّ بسيط بدل الـ Warp")
            return _safe_bbox()

        (tl, tr, br, bl) = rect_pts
        widthA = float(np.hypot(br[0] - bl[0], br[1] - bl[1]))
        widthB = float(np.hypot(tr[0] - tl[0], tr[1] - tl[1]))
        heightA = float(np.hypot(tr[0] - br[0], tr[1] - br[1]))
        heightB = float(np.hypot(tl[0] - bl[0], tl[1] - bl[1]))
        maxWidth = max(int(widthA), int(widthB))
        maxHeight = max(int(heightA), int(heightB))

        # Safe Guard: أبعاد مقلوبة (ارتفاع >> عرض) — بديل بسيط
        if maxHeight > maxWidth * 1.1:
            logger.warning("Safe Guard: ناتج مقلوب (ارتفاع >> عرض) — قصّ بسيط")
            return _safe_bbox()
        if maxWidth < 2 or maxHeight < 2:
            return _safe_bbox()

        scale = max(1.0, float(WARP_DEST_SCALE))
        maxWidth = max(1, int(round(maxWidth * scale)))
        maxHeight = max(1, int(round(maxHeight * scale)))

        dst = np.array([
            [0, 0],
            [maxWidth - 1, 0],
            [maxWidth - 1, maxHeight - 1],
            [0, maxHeight - 1]], dtype="float32")

        M = cv2.getPerspectiveTransform(rect_pts, dst)
        warped = cv2.warpPerspective(border_img, M, (maxWidth, maxHeight))

        # Safe Guard: ناتج الـ Warp عمودي — بديل بسيط بدل القناع الملتوٍ
        if warped.shape[0] > warped.shape[1] * 1.05:
            logger.warning("Safe Guard: ناتج الـ Warp عمودي — قصّ بسيط بدل القناع الملتوٍ")
            return _safe_bbox()

        if corners_out is not None:
            mapped = cv2.perspectiveTransform(
                rect_pts.reshape(-1, 1, 2).astype(np.float32), M)
            corners_out[:] = [tuple(float(v) for v in p[0]) for p in mapped]

        return warped
    except Exception as e:
        logger.warning("Safe Guard: خطأ في مسار الـ Warp (%s) — قصّ بسيط (Standard Bounding Box)", e)
        return _safe_bbox()


def _warp_card(rotated_rgb, poly, used_bbox_out=None, corners_out=None):
    """يمرّر إحداثيات مضلع القناع (masks.xy) إلى auto_warp_id_card مباشرة."""
    if poly is None or len(poly) < 4:
        return None
    import cv2
    cnt = np.asarray(poly, dtype=np.float32).reshape(-1, 1, 2)
    if cv2.contourArea(cnt) <= 0:
        return None
    return auto_warp_id_card(rotated_rgb, cnt, used_bbox_out=used_bbox_out,
                             corners_out=corners_out)


def _straighten_box(rotated_rgb, box, pad=SAFE_PAD):
    """يفرد مربّع الكشف عبر نفس القاعدة الهندسية الموحّدة (auto_warp_id_card).

    زوايا مربّع الكشف تُعالَج بحسب قاعدة minAreaRect نفسها المستعمَلة في
    auto_warp_id_card (المصدر الوحيد للتفريد + Safe Guard) — يُرجع صورة
    ملونة (H', W', 3) مستقيمة عند النجاح، أو None.
    """
    if box is None:
        return None
    import cv2
    try:
        x1, y1, x2, y2 = box
        if x2 - x1 < 4 or y2 - y1 < 4:
            return None
        pts = np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float32)
        cnt = pts.reshape(-1, 1, 2)
        # نفس القاعدة الهندسية الموحّدة: minAreaRect + warpPerspective + Safe Guard
        return auto_warp_id_card(rotated_rgb, cnt)
    except Exception as e:
        logger.warning("Safe Guard (_straighten_box): خطأ في مسار الـ Warp (%s) — قصّ بسيط", e)
        return _bbox_fallback_crop(rotated_rgb, cnt, pad=FALLBACK_PAD)


def _pad_outer(x1, y1, x2, y2, W, H, pad=3):
    """تمديد خارجي لهامش أمان صغير (لا تقليص) لمسار الكشف العادي."""
    return (
        max(0, int(round(x1 - pad))),
        max(0, int(round(y1 - pad))),
        min(W, int(round(x2 + pad))),
        min(H, int(round(y2 + pad))),
    )


def _pad_box(x1, y1, x2, y2, W, H, pad_ratio=0.01):
    """هوامش صغيرة حول المربع حتى لا تُقتطع أطراف البطاقة أثناء القص."""
    pw = max(2.0, (x2 - x1) * pad_ratio)
    ph = max(2.0, (y2 - y1) * pad_ratio)
    return (
        max(0, int(round(x1 - pw))),
        max(0, int(round(y1 - ph))),
        min(W, int(round(x2 + pw))),
        min(H, int(round(y2 + ph))),
    )


def detect_corners(pil_image: Image.Image) -> Optional[list]:
    """الزوايا الأربع (x, y) بالمقياس الأصلي من كشف YOLO، أو None."""
    if get_model() is None:
        return None
    try:
        img_rgb = np.asarray(pil_image.convert("RGB"))
        total_h, total_w = img_rgb.shape[:2]
        result = _best_detection(img_rgb)
        if not result or result[1] != 0:
            if result and result[1] != 0:
                logger.info("كشف YOLO تطلّب تدوير الصورة (%d°)", result[1])
            return None
        _, angle, box, _ = result
        H, W = total_h, total_w
        x1, y1, x2, y2 = _pad_box(box[0], box[1], box[2], box[3], W, H)
        return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    except Exception as e:
        logger.warning("خطأ أثناء كشف YOLO: %s", e)
        return None


def _try_content_corners(src_rgb):
    """استخراج الزوايا الأربع الحقيقية تلقائياً من محتوى الصورة (مثل النقاط الخضراء).

    عندما لا يحمل كشف YOLO أي ميلان (قناع مفقود أو ناتج Safe Guard حاملٍ
    لصورة مائلة) تُحدَّد زوايا البطاقة من محتوى الصورة نفسها ثم تُفرد:
    1) الأولوية: نموذج OBB المتخصص (card_detector.onnx) يعيد الزوايا الأربع
       الحقيقية المائلة مباشرة → auto_warp_id_card يفرُدها بالمستطيل المستقيم.
    2) الاحتياط: _detect_rect_edges_then_extract (كشف حواف هندسي صارم) يفرُد
       الزوايا بالمنظور — يَضبط الميلان الحاد حتى ~30°.
    3) الأخير: extract_card — يُرفض ناتجه إن غطّى ~90% من الصورة (فشل مكتوم
       يعيد الصورة كاملة) أو لم يكن بنسبة بطاقة معقولة.
    تُرجع ndarray RGB مستقيمة أفقياً، أو None عند الفشل الكامل.
    """
    sh, sw = src_rgb.shape[:2]
    try:
        from core.card_detector import detect_card_quad, card_model_available
        if card_model_available():
            quad = detect_card_quad(Image.fromarray(src_rgb).convert("RGB"))
            if quad is not None:
                q = np.asarray(quad, dtype=np.float32).reshape(-1, 2)
                if len(q) == 4:
                    warped = auto_warp_id_card(src_rgb, q.reshape(-1, 1, 2))
                    if warped is not None:
                        logger.info("المحتوى: فرد الزوايا الأربع (OBB) — %dx%d",
                                    warped.shape[1], warped.shape[0])
                        return warped
    except Exception as e:
        logger.warning("Safe Guard: فشل كاشف البطاقة OBB (%s) — محاولة الحواف", e)
    try:
        from core.id_extractor import _detect_rect_edges_then_extract
        tight = _detect_rect_edges_then_extract(Image.fromarray(src_rgb).convert("RGB"))
        if tight is not None:
            rgb = np.asarray(tight.convert("RGB"))
            h, w = rgb.shape[:2]
            if w > h:
                logger.info("المحتوى: فرد الزوايا بالحواف — %dx%d", w, h)
                return rgb
    except Exception as e:
        logger.warning("Safe Guard: كشف الحواف فشل (%s) — محاولة extract_card", e)
    try:
        from core.id_extractor import extract_card
    except Exception:
        return None
    try:
        res = extract_card(Image.fromarray(src_rgb).convert("RGB"))
        if res is None:
            return None
        rgb = np.asarray(res.convert("RGB"))
        h, w = rgb.shape[:2]
        if h < 8 or w < 8 or sw < 2 or sh < 2:
            return None
        if (w * h) / float(sw * sh) > 0.9:
            logger.warning("Safe Guard: استخراج المحتوى عاد صورة كاملة — تجاهل")
            return None
        aspect = max(w, h) / float(min(w, h))
        if not (1.0 <= aspect <= 2.9):
            logger.warning("Safe Guard: نسبة أبعاد مستخرجة شاذة %.2f — تجاهل", aspect)
            return None
        if w < h:  # عمودية ← أعد تدويرها أفقياً (مثل _fit_to_card_slot)
            rgb = np.asarray(Image.fromarray(rgb).rotate(-90, expand=True).convert("RGB"))
        return rgb
    except Exception as e:
        logger.warning("Safe Guard: فشل استخراج الزوايا من المحتوى (%s)", e)
        return None


# تجربة: قصّ هامش فعلي من حواف ناتج القصّ قبل ملء A4 — إزالة الخلفية
# التي يلتقطها القناع حول البطاقة المائلة.
_CROP_INWARD_FRAC = 0.01


def _crop_inward(img):
    """قصّ هامش نسبي من كل حافة حتى لا يتسرب إطار الخلفية للنتيجة."""
    if img is None:
        return None
    h, w = img.shape[:2]
    tx = max(1, int(round(w * _CROP_INWARD_FRAC)))
    ty = max(1, int(round(h * _CROP_INWARD_FRAC)))
    if w > 2 * tx and h > 2 * ty:
        return img[ty:h - ty, tx:w - tx]
    return img


def _map_corners_to_result(coords, src_w, src_h, dpi=600):
    """يسقط نقاط (x, y) من إحداثيات ناتج الـ warp إلى إحداثيات الصورة النهائية.

    يطبق نفس التحويلين اللاحقين على القصّ المحلي:
    1) _crop_inward: طرح هامش (tx, ty) من كل حافة.
    2) _fit_to_card_slot: دوران عمودي (إن لزم) + مقياس تعبئة أو Contain أبيض،
       وفق منطق card_cutter نفسه — فيبقى موقع كل نقطة مطابقاً للصورة المعروضة.
    """
    from core.card_cutter import _target_px
    tx = max(1, int(round(src_w * _CROP_INWARD_FRAC)))
    ty = max(1, int(round(src_h * _CROP_INWARD_FRAC)))
    pts = []
    for (x, y) in coords:
        pts.append((float(x) - tx, float(y) - ty))
    iw = src_w - 2 * tx
    ih = src_h - 2 * ty
    tw, th = _target_px(dpi)
    rotated = ih > iw and tw > th
    if rotated:
        pts = [(y, iw - 1 - x) for (x, y) in pts]
        iw, ih = ih, iw
    if iw < 2 or ih < 2 or tw < 2 or th < 2:
        return [(0.0, 0.0)] * len(pts)
    sx = tw / iw
    sy = th / ih
    ratio = (max(sx, sy) / min(sx, sy)) if min(sx, sy) > 0 else 1.0
    if 1.0 < ratio <= 1.15:
        return [(x * sx, y * sy) for (x, y) in pts]
    scale = min(sx, sy)
    new_w = max(1, int(round(iw * scale)))
    new_h = max(1, int(round(ih * scale)))
    left = (tw - new_w) // 2
    top = (th - new_h) // 2
    return [((x * scale) + left, (y * scale) + top) for (x, y) in pts]


def _v143_postprocess(warped_bgr):
    """تصحيح الميلان وتقطيع الخلفية الباقية بعد قصّ YOLO — طريقة v1.4.3.

    يأخذ مصفوفة BGR مقصوصة من YOLO ويعيد مصفوفة BGR مُصحّحة ميلانها.
    لا يُعيق أبداً: يُعيد المصفوفة الأصلية عند أي خطأ أو فشل تحقق."""
    try:
        from PIL import Image as _Image
        from core.id_extractor import perspective_correct
        rgb = cv2.cvtColor(warped_bgr, cv2.COLOR_BGR2RGB)
        pil_in = _Image.fromarray(rgb)
        pil_out = perspective_correct(pil_in)
        arr = np.asarray(pil_out.convert("RGB"))
        h, w = arr.shape[:2]
        oh, ow = warped_bgr.shape[:2]
        if h < 20 or w < 20:
            return warped_bgr
        aspect = w / h if h > 0 else 0
        if not (1.1 <= aspect <= 2.6):
            return warped_bgr
        if w < ow * 0.4 or h < oh * 0.4:
            return warped_bgr
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    except Exception:
        return warped_bgr


def _fit_card_final(arr_bgr, dpi):
    """تصحيح v1.4.3 + لصق البطاقة — نقطة الإرجاع الموحدة لـ cut_card_local."""
    from core.card_cutter import _fit_to_card_slot
    arr_bgr = _v143_postprocess(arr_bgr)
    return _fit_to_card_slot(Image.fromarray(_crop_inward(arr_bgr)), dpi)


# هامش القصّ حول البطاقة بعد كشف YOLO (بكسل نسبة من أطول ضلع) — 2سم تقريباً
MARGIN_FRAC = 0.18

# أبعاد بطاقة الهوية القياسية بالملليمتر لتحويل الهوامش من بكسل إلى سم
CARD_W_MM = 85.6
CARD_H_MM = 54.0
# عتبة "الخلفية شبه المعدومة": كل الهوامش حول البطاقة أقل من هذه (سم) → YOLO مباشرة
NEAR_MARGIN_CM = 2.0


# عتبة نمط الزوايا: أعلى = بطاقة بزوايا أكثر حدة (انحسار أصغر عن المستطيل المحيط)
CORNER_SHARP_MAX_INSET = 0.0045


def _deglare_rgb(img_rgb):
    """يقلّل الوهج/السطوع القوي: CLAHE على قناة الإضاءة L يعيد تباين حواف البطاقة.

    يخصّص قنوات BGR→Lab ثم يساوي المدرج التكراري للضوء محلياً (clipLimit،
    tileGridSize) ويقلل 1% من الأطراف (Percentile Clip) لتخفيف الحرق الضوئي
    أمام عدسة الكاميرا. الأبعاد لا تتغير — الإحداثيات المعاد كشفها تبقى مطابقة."""
    import cv2
    try:
        lab = cv2.cvtColor(img_rgb, cv2.COLOR_BGR2LAB)
        l_ch, a_ch, b_ch = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        l2 = clahe.apply(l_ch)
        lab2 = cv2.merge([l2, a_ch, b_ch])
        out = cv2.cvtColor(lab2, cv2.COLOR_LAB2BGR)
        # تقليم الأطراف بنسبة 1% لكل قناة — يذيب البريق المبيض للأبيض الصافي
        out2 = cv2.convertScaleAbs(out, alpha=1.0, beta=0.0)
        for j in range(out2.shape[2]):
            ch = out2[:, :, j]
            lo, hi = np.percentile(ch, (1, 99))
            out2[:, :, j] = np.clip((ch.astype(np.float32) - lo)
                                    * (255.0 / max(hi - lo, 1.0)), 0, 255).astype(np.uint8)
        return out2
    except Exception as e:
        logger.warning("YOLO: فشل إزالة الوهج (%s) — إرجاع الصورة كما هي", e)
        return img_rgb


def _resample_contour(cnt, points=512):
    """يعيد توليد المضلع بنقاط موزعة بانتظام على المحيط (يثخّن مضلع YOLO الخفيف)."""
    import cv2
    peri = float(cv2.arcLength(cnt, True))
    if peri <= 1:
        return cnt
    sampled = np.zeros((points, 2), dtype=np.float32)
    for i in range(points):
        arc = peri * i / points
        dist = 0.0
        prev = cnt[0][0]
        for j in range(1, len(cnt) + 1):
            cur = cnt[j % len(cnt)][0]
            seg = float(np.hypot(cur[0] - prev[0], cur[1] - prev[1]))
            if dist + seg >= arc:
                t = (arc - dist) / max(seg, 1e-9)
                sampled[i] = prev + t * (cur - prev)
                break
            dist += seg
            prev = cur
        else:
            sampled[i] = cnt[len(cnt) - 1][0]
    return sampled.reshape(-1, 1, 2)


def _corner_style(poly) -> Optional[str]:
    """نمط زوايا البطاقة من قناع YOLO: 'sharp' (حادة) / 'rounded' (مستديرة) / None.

    المقياس: "انحسار" كل زاوية عن زاوية المستطيل المائل المحيط (minAreaRect).
    الزاوية الحادة تملأ ركن المستطيل حتى نهايته (انحسار ≈ 0)؛ الزاوية
    المستديرة تنحسر عن الركن بمقدار يتناسب مع نصف قطر التدوير. المضلع يُثخَّن
    أولاً حتى لا يبتلع التقريب الخفيف الفرق (approxPolyDP يفشل على 10 نقاط)."""
    if poly is None:
        return None
    n_orig = len(poly)
    if n_orig < 4:
        return None
    try:
        import cv2
        cnt = np.asarray(poly, dtype=np.float32).reshape(-1, 1, 2)
        if not np.all(np.isfinite(cnt)):
            return None
        dense = _resample_contour(cnt)
        rect = cv2.minAreaRect(dense)
        card_w = max(float(rect[1][0]), float(rect[1][1]))
        if card_w <= 1:
            return None
        box = cv2.boxPoints(rect).astype(np.float32)
        M = dense.reshape(-1, 2).mean(axis=0)
        probe = int(round(card_w * 0.04)) + 2
        insets = []
        for b in box:
            dv = np.array(b, dtype=np.float32) - M
            norm = float(np.linalg.norm(dv))
            if norm <= 1e-9:
                continue
            dv = dv / norm
            found = None
            for t in np.linspace(0, probe, 96):
                px = int(round(b[0] - dv[0] * t))
                py = int(round(b[1] - dv[1] * t))
                d = cv2.pointPolygonTest(dense, (float(px), float(py)), False)
                if d > 0:
                    found = t
                    break
            insets.append(0.0 if found is None else found)
        if not insets:
            return None
        ratio = float(np.mean(insets)) / card_w
        logger.info("YOLO: انحسار زوايا القناع = %.4f (نقاط المضلع %d)",
                    ratio, n_orig)
        return "sharp" if ratio <= CORNER_SHARP_MAX_INSET else "rounded"
    except Exception as e:
        logger.warning("YOLO: فشل تقدير نمط الزوايا (%s)", e)
        return None


def _near_from_box(img_shape, box) -> bool:
    """near=True إذا كل هوامش الخلفية حول البطاقة <2سم (تُحسب من أبعاد بطاقة 85.6×54مم)."""
    try:
        h, w = img_shape[0], img_shape[1]
        x0, y0, x1b, y1b = [float(v) for v in box]
        bw = x1b - x0
        bh = y1b - y0
        if bw <= 10 or bh <= 10:
            return False
        landscape = bw >= bh
        cm_w_mm = CARD_W_MM if landscape else CARD_H_MM
        cm_h_mm = CARD_H_MM if landscape else CARD_W_MM
        px_per_mm_x = bw / cm_w_mm
        px_per_mm_y = bh / cm_h_mm
        left_cm = (float(x0) / px_per_mm_x) / 10.0
        right_cm = (float(w - x1b) / px_per_mm_x) / 10.0
        top_cm = (float(y0) / px_per_mm_y) / 10.0
        bottom_cm = (float(h - y1b) / px_per_mm_y) / 10.0
        max_margin = max(left_cm, right_cm, top_cm, bottom_cm)
        logger.info("YOLO: هوامش البطاقة (سم) يسار=%.1f يمين=%.1f أعلى=%.1f أسفل=%.1f",
                    left_cm, right_cm, top_cm, bottom_cm)
        return max_margin < NEAR_MARGIN_CM
    except Exception as e:
        logger.warning("YOLO: فشل حساب هوامش القرب (%s)", e)
        return False


def _detect_with_frame(img_rgb):
    """كشف واحد على الصورة (بإطار أبيض مؤقت للملاصق للأطراف) — إحداثياته بالمقياس الأصلي.

    في النسخة المجمّعة (بدون ultralytics) يعود None فوراً — الزوايا تُستخرج
    لاحقاً من نموذج ONNX المضمّن (card_detector) عبر _try_content_corners."""
    import cv2
    pad_m = int(CARD_FRAME_MARGIN)
    detect_src = img_rgb
    if get_model() is not None and img_rgb.shape[0] > 2 * pad_m and img_rgb.shape[1] > 2 * pad_m:
        detect_src = cv2.copyMakeBorder(
            img_rgb, pad_m, pad_m, pad_m, pad_m,
            borderType=cv2.BORDER_CONSTANT, value=(255, 255, 255))
    result = None
    if get_model() is None:
        return result  # بدون ultralytics لا كشف .pt — يتبع المحتوى (ONNX)
    for conf in (0.6, 0.4, 0.25):
        result = _best_detection(detect_src, conf)
        if result is not None:
            break
    if result is None:
        logger.info("YOLO: الكشف العادي فشل — تجربة إزالة الوهج (CLAHE)")
        enhanced = _deglare_rgb(detect_src)
        for conf in (0.6, 0.4, 0.25):
            result = _best_detection(enhanced, conf)
            if result is not None:
                logger.info("YOLO: نجح الكشف بعد إزالة الوهج بثقة %.2f", result[0])
                break
    if result is not None and detect_src is not img_rgb:
        conf, angle, box, poly = result
        box = [float(v) - pad_m for v in box]
        if poly is not None:
            poly = poly - pad_m
        result = (conf, angle, box, poly)
    return result


def _window_from_box(img_rgb, box):
    """نافذة تقريب حول البطاقة بهامش MARGIN_FRAC (لطريقة 1.4.3) أو None."""
    h, w = img_rgb.shape[:2]
    x0, y0, x1b, y1b = [float(v) for v in box]
    bw = x1b - x0
    bh = y1b - y0
    if bw < 20 or bh < 20:
        return None
    margin = int(round(max(bw, bh) * MARGIN_FRAC))
    margin = max(4, min(margin, 500))
    cx0 = max(0, int(x0) - margin)
    cy0 = max(0, int(y0) - margin)
    cx1 = min(w, int(x1b) + margin)
    cy1 = min(h, int(y1b) + margin)
    if cx1 - cx0 < 40 or cy1 - cy0 < 40:
        return None
    return img_rgb[cy0:cy1, cx0:cx1]


def _detect_and_margin_crop(pil_image: Image.Image):
    """يكتشف YOLO البطاقة ويعيد (نافذة حولها بهامش، near) أو (None, False).

    - near = True: كل هوامش الخلفية حول البطاقة أقل من 2سم (الخلفية شبه
      معدومة) → يستخدم YOLO القصّ المباشر، فلا حاجة لتقريب النافذة.
    - وإلا: نافذة تقريب (البطاقة + خلفية كافية) تُقطع بهامش → طريقة 1.4.3.
    """
    try:
        import cv2
        img_rgb = np.asarray(pil_image.convert("RGB"))
        if img_rgb.ndim != 3 or img_rgb.shape[0] < 40 or img_rgb.shape[1] < 40:
            return None, False
        result = _detect_with_frame(img_rgb)
        if result is None:
            return None, False
        conf, angle, box, poly = result
        if not box or len(box) < 4:
            return None, False
        near = _near_from_box(img_rgb.shape, box)
        if near:
            logger.info("YOLO: الخلفية شبه معدومة (<%dسم) — قصّ مباشر", NEAR_MARGIN_CM)
            return None, True
        window = _window_from_box(img_rgb, box)
        if window is None:
            return None, False
        logger.info("YOLO: نافذة اقتصاص حول البطاقة — %dx%d",
                    window.shape[1], window.shape[0])
        return Image.fromarray(window), False
    except Exception as e:
        logger.warning("YOLO: فشل قصّ النافذة حول البطاقة (%s)", e)
        return None, False


def cut_card_local(pil_image: Image.Image, dpi: int = 600,
                   corners_out: Optional[list] = None) -> Optional[Image.Image]:
    """قصّ دقيق بالمنظور (Perspective Warp) إلى مستطيل A4 (95×55mm) عبر Pillow.

    القاعدة الدائمة: **لا قصّ مباشر على صورة مائلة تحت أي ظرف** — تُعدَّل
    الميلان تلقائياً من الزوايا الأربع الحقيقية (مثل النقاط الخضراء) ثم
    تُلصق النتيجة بزوايا مستطيل A4:
    1. قناع YOLO (masks.xy): auto_warp_id_card يحدد الزوايا الأربع من القناع
       (minAreaRect على البدن المحدّب) ويفردها بالـ Perspective Warp.
    2. فشل القناع/لم يحمل ميلان (Safe Guard أو كشف بلا قناع): تُستخرج الزوايا
       الأربع تلقائياً من محتوى الصورة (الحواف/OBB) عبر _try_content_corners
       — نفس فكرة النقر اليدوي على الحواف الأربع لكن تلقائياً بالكامل.
    3. إن تعذّر فردّ المحتوى: صندوق أمان مستقيم (Standard Bounding Box) ثم
       _fit_to_card_slot تعبئة كاملة داخل المستطيل.
    4. كل ناتج يُلصق بزوايا مستطيل A4 فعلياً عبر _fit_to_card_slot.
    """
    from core.card_cutter import _fit_to_card_slot
    import cv2

    img_rgb = np.asarray(pil_image.convert("RGB"))

    result = _detect_with_frame(img_rgb)
    if result is None:
        # بدون كشف .pt (ultralytics غائب في النسخة المجمّعة) أو لم يجد YOLO شيء:
        # الزوايا تخرج من محتوى الصورة (ONNX OBB card_detector ثم الحواف).
        logger.info("YOLO لم يجد كشفاً في الصورة — استخراج الزوايا من المحتوى (ONNX/الحواف)")
        derived = _try_content_corners(img_rgb)
        if derived is not None:
            if corners_out is not None:
                corners_out[:] = [(0.0, 0.0), (derived.shape[1], 0.0),
                                  (derived.shape[1], derived.shape[0]),
                                  (0.0, derived.shape[0])]
            return _fit_card_final(derived, dpi)
        if corners_out is not None:
            corners_out[:] = []
        return None
    conf, angle, box, poly = result
    rotated = _rotated_rgb(img_rgb, angle)

    if poly is not None:
        # القناع: Perspective Warp عبر مضلع القناع + هامش أمان صغير (4 بكسل)
        used_bbox = []
        wcorners = []
        warped = _warp_card(rotated, poly, used_bbox_out=used_bbox,
                            corners_out=wcorners)
        if warped is not None and not used_bbox:
            logger.info("YOLO: فرد المنظور من قناع — %dx%d", warped.shape[1], warped.shape[0])
            if corners_out is not None:
                corners_out[:] = _map_corners_to_result(
                    wcorners or [(0.0, 0.0), (warped.shape[1], 0.0),
                                 (warped.shape[1], warped.shape[0]),
                                 (0.0, warped.shape[0])],
                    warped.shape[1], warped.shape[0], dpi)
            return _fit_card_final(warped, dpi)
        if used_bbox:
            logger.warning("YOLO: Safe Guard على القناع — استخراج الزوايا من المحتوى")
            derived = _try_content_corners(rotated)
            if derived is not None:
                if corners_out is not None:
                    corners_out[:] = _map_corners_to_result(
                        [(0.0, 0.0), (derived.shape[1], 0.0),
                         (derived.shape[1], derived.shape[0]),
                         (0.0, derived.shape[0])],
                        derived.shape[1], derived.shape[0], dpi)
                return _fit_card_final(derived, dpi)
            if corners_out is not None:
                corners_out[:] = _map_corners_to_result(
                    [(0.0, 0.0), (warped.shape[1], 0.0),
                     (warped.shape[1], warped.shape[0]),
                     (0.0, warped.shape[0])],
                    warped.shape[1], warped.shape[0], dpi)
            return _fit_card_final(warped, dpi)
        warped = _straighten_box(rotated, box, pad=SAFE_PAD)
        if warped is not None:
            logger.info("YOLO: فرد مربّع الكشف — %dx%d", warped.shape[1], warped.shape[0])
            if corners_out is not None:
                corners_out[:] = _map_corners_to_result(
                    [(0.0, 0.0), (warped.shape[1], 0.0),
                     (warped.shape[1], warped.shape[0]),
                     (0.0, warped.shape[0])],
                    warped.shape[1], warped.shape[0], dpi)
            return _fit_card_final(warped, dpi)
        if corners_out is not None:
            corners_out[:] = []
        return None

    # بلا قناع (كشف ميلان على زوايا المربع فقط) → استخراج الزوايا من المحتوى
    # (نفس فكرة النقر اليدوي على الحواف الأربع لكن تلقائياً) ثم الـ Warp.
    derived = _try_content_corners(rotated)
    if derived is not None:
        logger.info("YOLO: استخراج الزوايا من المحتوى (بلا قناع) — %dx%d",
                    derived.shape[1], derived.shape[0])
        if corners_out is not None:
            corners_out[:] = _map_corners_to_result(
                [(0.0, 0.0), (derived.shape[1], 0.0),
                 (derived.shape[1], derived.shape[0]),
                 (0.0, derived.shape[0])],
                derived.shape[1], derived.shape[0], dpi)
        return _fit_card_final(derived, dpi)

    warped = _straighten_box(rotated, box, pad=SAFE_PAD)
    if warped is not None:
        logger.info("YOLO: فرد مربّع الكشف (بديل) — %dx%d", warped.shape[1], warped.shape[0])
        if corners_out is not None:
            corners_out[:] = _map_corners_to_result(
                [(0.0, 0.0), (warped.shape[1], 0.0),
                 (warped.shape[1], warped.shape[0]),
                 (0.0, warped.shape[0])],
                warped.shape[1], warped.shape[0], dpi)
        return _fit_card_final(warped, dpi)
    if corners_out is not None:
        corners_out[:] = []
    return None