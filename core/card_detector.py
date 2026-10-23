# مُكتشِف البطاقة بنموذج YOLO المدرب على هوياتكم.
#
# متوقّع أن يكون الملف  models/card_detector.onnx (يُنشأ عبر training/export_onnx.py).
# إن لم يوجد الملف تعيد الدالة None بهدوء حتى لا يكسر التطبيق —
# وعند وضع النموذج المدرب يصبح القصّ ذكياً تلقائياً (طبقة أولى قبل الحواف).

import logging
import os
import threading

import numpy as np

logger = logging.getLogger(__name__)

_MODEL_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models"))
_ONNX_PATH = os.path.join(_MODEL_DIR, "card_detector.onnx")
_IMGSZ = 640
_CONF = 0.30

_session = None
_session_loaded = False
_load_lock = threading.Lock()


def _load():
    """يحمل جلسة onnxruntime مرة واحدة (إن توفر الملف).

    محميّ بقفل مع فحص مزدوج: الخيوط المتزامنة (العدة العاملة في القصّ) تنتظر
    اكتمال التحميل الأول بدل إرجاع جلسة نصف مبنية (None) — يمنع سقوط بعض
    الصور عند الإقلاع الأول.
    """
    global _session, _session_loaded
    if _session_loaded:
        return _session
    with _load_lock:
        if _session_loaded:
            return _session
        if not os.path.exists(_ONNX_PATH):
            logger.info("لا يوجد نموذج مدرب (%s) — يُستخدم قصّ الحواف فقط", _ONNX_PATH)
            _session_loaded = True
            return None
        try:
            import onnxruntime as ort
            _session = ort.InferenceSession(_ONNX_PATH, providers=["CPUExecutionProvider"])
            logger.info("تم تحميل نموذج كشف البطاقة: %s", _ONNX_PATH)
        except Exception as e:  # pragma: no cover
            logger.error("فشل تحميل نموذج كشف البطاقة", exc_info=True)
            _session = None
        _session_loaded = True
        return _session


def card_model_available() -> bool:
    return _load() is not None


def _letterbox(img_bgr, size=_IMGSZ):
    import cv2
    h, w = img_bgr.shape[:2]
    r = min(size / w, size / h)
    nw, nh = int(round(w * r)), int(round(h * r))
    resized = cv2.resize(img_bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    dx, dy = (size - nw) // 2, (size - nh) // 2
    canvas[dy:dy + nh, dx:dx + nw] = resized
    return canvas, r, dx, dy


def _nms(boxes, scores, iou_thr=0.45):
    import cv2
    if len(boxes) == 0:
        return []
    idx = cv2.dnn.NMSBoxes(
        [(b[0], b[1], b[2] - b[0], b[3] - b[1]) for b in boxes],
        scores, 0.0, iou_thr)
    if idx is None or len(idx) == 0:
        return []
    return np.asarray(idx).flatten()


def _angle_quad(cx, cy, w, h, angle):
    """يبني 4 زوايا صندوق مائل (OBB) من مركز/عرض/ارتفاع/زاوية (راديان)."""
    cos = float(np.cos(angle))
    sin = float(np.sin(angle))
    hw, hh = w / 2.0, h / 2.0
    quad = np.array([
        [-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]
    ], dtype="float32")
    rot = np.array([[cos, -sin], [sin, cos]], dtype="float32")
    quad = quad @ rot.T + np.array([cx, cy])
    return quad


def detect_card(pil_image):
    """يرجع إطار البطاقة الأفقية (x1, y1, x2, y2) بإحداثيات الصورة أو None."""
    sess = _load()
    if sess is None:
        return None
    import cv2
    img = np.array(pil_image.convert("RGB"))
    img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    H, W = img_bgr.shape[:2]
    canvas, r, dx, dy = _letterbox(img_bgr)
    inp = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).astype("float32") / 255.0
    inp = np.transpose(inp, (2, 0, 1))[None]
    out = sess.run(None, {"images": inp})[0][0]      # [6, 8400] لتنسيق OBB
    n_ch = out.shape[0]
    boxes = out[:4]
    cls_scores = out[4:n_ch - 1].max(0) if n_ch > 5 else out[4].max(0)
    angle = out[n_ch - 1] if n_ch > 5 else np.zeros_like(cls_scores)
    keep = cls_scores > _CONF
    if not keep.any():
        return None
    cand_boxes = []
    cand_scores = []
    for i in np.where(keep)[0]:
        cx = (boxes[0, i] - dx) / r
        cy = (boxes[1, i] - dy) / r
        bw = boxes[2, i] / r
        bh = boxes[3, i] / r
        a = float(angle[i])
        quad = _angle_quad(cx, cy, bw, bh, a)
        xs = quad[:, 0]; ys = quad[:, 1]
        x1, y1 = float(xs.min()), float(ys.min())
        x2, y2 = float(xs.max()), float(ys.max())
        cand_boxes.append([x1, y1, x2, y2])
        cand_scores.append(float(cls_scores[i]))
    best_idx = _nms(cand_boxes, cand_scores)
    if len(best_idx) == 0:
        return None
    best = best_idx[0]
    x1, y1, x2, y2 = cand_boxes[best]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(W, x2), min(H, y2)
    if x2 - x1 < 20 or y2 - y1 < 20:
        return None
    return x1, y1, x2, y2


def detect_card_quad(pil_image):
    """يرجع زوايا البطاقة الأربع الحقيقية المائلة من نموذج OBB، أو None.

    هذه هي الميزة الجوهرية: بدل مستطيل أفقي يُدخل الخلفية حول بطاقة مائلة،
    نرجع النقاط الأربع الفعلية لأطراف الهوية فنقوم بتصحيح منظور مثالي لا
    يُبقي أي خلفية معها.
    """
    sess = _load()
    if sess is None:
        return None
    import cv2
    img = np.array(pil_image.convert("RGB"))
    img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    H, W = img_bgr.shape[:2]
    canvas, r, dx, dy = _letterbox(img_bgr)
    inp = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).astype("float32") / 255.0
    inp = np.transpose(inp, (2, 0, 1))[None]
    out = sess.run(None, {"images": inp})[0][0]      # [6, 8400]
    n_ch = out.shape[0]
    boxes = out[:4]
    cls_scores = out[4:n_ch - 1].max(0) if n_ch > 5 else out[4].max(0)
    angle = out[n_ch - 1] if n_ch > 5 else np.zeros_like(cls_scores)
    keep = cls_scores > _CONF
    if not keep.any():
        return None
    cand_quad = []
    cand_box = []
    cand_scores = []
    for i in np.where(keep)[0]:
        cx = (boxes[0, i] - dx) / r
        cy = (boxes[1, i] - dy) / r
        bw = boxes[2, i] / r
        bh = boxes[3, i] / r
        a = float(angle[i])
        quad = _angle_quad(cx, cy, bw, bh, a)
        quad[:, 0] = np.clip(quad[:, 0], 0, W)
        quad[:, 1] = np.clip(quad[:, 1], 0, H)
        xs = quad[:, 0]; ys = quad[:, 1]
        cand_box.append([float(xs.min()), float(ys.min()),
                         float(xs.max()), float(ys.max())])
        cand_quad.append(quad)
        cand_scores.append(float(cls_scores[i]))
    best_idx = _nms(cand_box, cand_scores)
    if len(best_idx) == 0:
        return None
    best = best_idx[0]
    return cand_quad[best]

