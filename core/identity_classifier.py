# مُصنِّف نوع الهوية (Identity Type Classifier).
#
# يميّز الهوية المقصوصة إلى أحد الأنواع المعرّفة (type_1 / type_2 / type_3)
# وهي الهويات الثلاثة التي يستخدمها عملاء التطبيق حصراً.
#
# النموذج: models/identity_type_classifier.onnx (يُنشأ عبر training/export_type_classifier.py)
# بمدخل (1,3,224,224) ومخرج softmax لأصناف الأنواع.
# إن لم يوجد الملف تعيد الدوال NULL بهدوء حتى لا يكسر التطبيق.

import json
import logging
import os
import threading

import numpy as np

logger = logging.getLogger(__name__)

_MODEL_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models"))
_ONNX_PATH = os.path.join(_MODEL_DIR, "identity_type_classifier.onnx")
_META_PATH = os.path.join(_MODEL_DIR, "identity_type_classifier.json")
_IMGSZ = 224

_session = None
_session_loaded = False
_load_lock = threading.Lock()
_CLASSES = ["type_1", "type_2", "type_3"]

# حدّ الثقة: فوقه نعتبر التصنيف موثوقاً ويعرض النوع؛ دونه نعرض "غير مؤكد".
# اختير تجريبياً نظراً لقرب تصميمات الأنواع بصرياً (متوسط الثقة ~0.63 في القياس).
CONFIDENCE_THRESHOLD = 0.55

# نورم ليزة ImageNet المستخدمة في التدريب (mobilenet transfer learning)
_MEAN = np.array([0.485, 0.456, 0.406], dtype="float32")
_STD = np.array([0.229, 0.224, 0.225], dtype="float32")


def _load_meta():
    global _CLASSES
    try:
        with open(_META_PATH, encoding="utf-8") as f:
            meta = json.load(f)
        if meta.get("classes"):
            _CLASSES = meta["classes"]
    except Exception:
        pass


def _load():
    """تحميل جلسة onnxruntime مرة واحدة (إن توفر الملف).

    محميّ بقفل مع فحص مزدوج لتفادي حالة التسابق عند الإقلاع الأول (خيوط
    متزامنة قد ترى العلم مرفوعاً قبل اكتمال التحميل فتُرجع None).
    """
    global _session, _session_loaded
    if _session_loaded:
        return _session
    with _load_lock:
        if _session_loaded:
            return _session
        if not os.path.exists(_ONNX_PATH):
            logger.info("لا يوجد مصنِّف نوع هوية (%s) — يُرجع None", _ONNX_PATH)
            _session_loaded = True
            return None
        try:
            import onnxruntime as ort
            _session = ort.InferenceSession(_ONNX_PATH, providers=["CPUExecutionProvider"])
            _load_meta()
            logger.info("تم تحميل مصنِّف نوع الهوية: %s (أصناف: %s)", _ONNX_PATH, _CLASSES)
        except Exception:  # pragma: no cover
            logger.error("فشل تحميل مصنِّف نوع الهوية", exc_info=True)
            _session = None
        _session_loaded = True
        return _session


def type_classifier_available() -> bool:
    return _load() is not None


def _preprocess(pil_image):
    """تحويل صورة PIL (مقاس عشوائي) إلى موتر (1,3,224,224) بنورم ImageNet."""
    img = pil_image.convert("RGB")
    img = img.resize((_IMGSZ, _IMGSZ))
    arr = np.asarray(img, dtype="float32") / 255.0
    arr = (arr - _MEAN) / _STD
    arr = np.transpose(arr, (2, 0, 1))  # CHW
    return arr[None].astype("float32")


def classify_type(pil_image):
    """يصنّف صورة بطاقة (مقصّاة/منتزعة) إلى أحد الأنواع.

    يعيد dict:
      {"available": False}                 إن لم يتوفر النموذج.
      {"available": True, "class": str,
       "confidence": float, "is_confident": bool}  مع مؤشر ثقة.
    """
    sess = _load()
    if sess is None:
        return {"available": False}
    try:
        inp = _preprocess(pil_image)
        out = sess.run(None, {"image": inp})[0]
        probs = np.asarray(out, dtype="float64").reshape(-1)
        idx = int(np.argmax(probs))
        conf = float(probs[idx])
        return {
            "available": True,
            "class": _CLASSES[idx] if idx < len(_CLASSES) else f"type_{idx + 1}",
            "confidence": conf,
            "is_confident": conf >= CONFIDENCE_THRESHOLD,
            "probabilities": {c: float(p) for c, p in zip(_CLASSES, probs)},
        }
    except Exception:  # pragma: no cover
        logger.error("فشل تصنيف نوع الهوية", exc_info=True)
        return {"available": False}