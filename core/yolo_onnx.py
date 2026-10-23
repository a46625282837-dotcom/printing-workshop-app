"""تشغيل نموذج YOLOv8n-Seg (id_card.onnx) عبر onnxruntime بلا torch/ultralytics.

النسخة المجمّعة تستثني ultralytics (حجمه الضخم)، لذا كان الكشف يهبط على
card_detector.onnx (OBB أضعف للخلفيات المعقدة). هذا الملف يعيد بناء واجهة
model.predict() الخاصة بـ ultralytics فوق onnxruntime ليعمل مسار YOLO الكامل
(قناع Segmentation دقيق) في البناء المجمّد بنفس نتائج نسخة بايثون.

المخرجات المتوقعة من id_card.onnx (صُدِّر عبر ultralytics، إدخال 640×640):
    pred    (1, 37, 8400) = [cx,cy,w,h] + cls(1) + mask_coeff(32)  (مفكوك DFL، cls sigmoid)
    protos  (1, 32, 160, 160)
"""

import logging
import os
import threading

import numpy as np

logger = logging.getLogger(__name__)

# يستورد resource_path من local_yolo لضمان تعامل مع _MEIPASS في الحزمة
# (نفس المقدار يُستخدم في config و model .pt).
from core.local_yolo import resource_path

_MODEL_DIR = resource_path("models")
_ONNX_PATH = os.path.join(_MODEL_DIR, "id_card.onnx")
_IMGSZ = 640
_STRIDE = 32
_IoU_THR = 0.45
_MASK_HW = 160  # protos 160×160 (640/4)

_session = None
_session_loaded = False
_load_lock = threading.Lock()


def onnx_model_available() -> bool:
    return os.path.exists(_ONNX_PATH) and _load() is not None


def _load():
    """جلسة onnxruntime واحدة لكاشف YOLO Segment (إن توفر الملف).

    محميّ بقفل مع فحص مزدوج لتفادي حالة التسابق عند الإقلاع الأول (الخيوط
    المتزامنة قد ترى الجلسة نصف مبنية فتُرجع None وتُسقط بعض الصور).
    """
    global _session, _session_loaded
    if _session_loaded:
        return _session
    with _load_lock:
        if _session_loaded:
            return _session
        if not os.path.exists(_ONNX_PATH):
            logger.info("لا يوجد نموذج YOLO Segment محلي (%s)", _ONNX_PATH)
            _session_loaded = True
            return None
        try:
            import onnxruntime as ort
            _session = ort.InferenceSession(_ONNX_PATH, providers=["CPUExecutionProvider"])
            logger.info("تم تحميل نموذج YOLO Segment: %s", _ONNX_PATH)
        except Exception as e:  # pragma: no cover
            logger.error("فشل تحميل نموذج YOLO Segment", exc_info=True)
            _session = None
        _session_loaded = True
        return _session


def _letterbox(img, new_shape=_IMGSZ, color=(114, 114, 114)):
    """letterbox مطابق لـ ultralytics بلا auto (إطار مربع ثابت 640×640)."""
    import cv2
    shape = img.shape[:2]
    r = min(new_shape / shape[0], new_shape / shape[1])
    new_unpad = (int(round(shape[1] * r)), int(round(shape[0] * r)))
    dw, dh = new_shape - new_unpad[0], new_shape - new_unpad[1]
    dw, dh = dw / 2.0, dh / 2.0
    resized = img
    if shape[::-1] != new_unpad:
        resized = cv2.resize(img, new_unpad, interpolation=cv2.INTER_LINEAR)
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    canvas = cv2.copyMakeBorder(resized, top, bottom, left, right,
                                cv2.BORDER_CONSTANT, value=color)
    return canvas, r, (dw, dh)


def _nms(boxes, scores, iou_thr=_IoU_THR):
    import cv2
    if len(boxes) == 0:
        return []
    idx = cv2.dnn.NMSBoxes(
        [(b[0], b[1], b[2] - b[0], b[3] - b[1]) for b in boxes],
        scores, 0.0, iou_thr)
    if idx is None or len(idx) == 0:
        return []
    return np.asarray(idx).flatten()


def _crop_mask(mask, box):
    """crop_mask من ultralytics: يصفّر خارج المربع دون تقليص الأبعاد (160×160)."""
    mask = mask.copy()
    mh, mw = mask.shape
    x1, y1, x2, y2 = [float(v) for v in box]
    x1 = max(0.0, x1)
    y1 = max(0.0, y1)
    x2 = min(float(mw), x2)
    y2 = min(float(mh), y2)
    crop_x = np.zeros((mh, mw), dtype=mask.dtype)
    crop_x[:, max(0, int(np.floor(x1))):min(mw, int(np.ceil(x2)))] = 1.0
    crop_y = np.zeros((mh, mw), dtype=mask.dtype)
    crop_y[max(0, int(np.floor(y1))):min(mh, int(np.ceil(y2))), :] = 1.0
    return mask * crop_x * crop_y


def _xywh2xyxy(boxes):
    out = np.empty_like(boxes, dtype=np.float32)
    out[:, 0] = boxes[:, 0] - boxes[:, 2] / 2.0
    out[:, 1] = boxes[:, 1] - boxes[:, 3] / 2.0
    out[:, 2] = boxes[:, 0] + boxes[:, 2] / 2.0
    out[:, 3] = boxes[:, 1] + boxes[:, 3] / 2.0
    return out


def _poly_from_mask(mask_bin):
    """أكبر محيط خارجي من قناع ثنائي ← مضلع (N,2) بإحداثيات الصورة الأصلية."""
    import cv2
    mask_u8 = (mask_bin > 0).astype(np.uint8) * 255
    contours, _ = cv2.findContours(mask_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    if not np.all(np.isfinite(c)) or len(c) < 3:
        return None
    return c.reshape(-1, 2).astype(np.float32)


class _Boxes:
    def __init__(self, xyxy=None, conf=None):
        self.xyxy = np.zeros((0, 4), dtype=np.float32) if xyxy is None else np.asarray(xyxy, dtype=np.float32)
        self.conf = np.zeros(0, dtype=np.float32) if conf is None else np.asarray(conf, dtype=np.float32)

    def __len__(self):
        return int(self.xyxy.shape[0])

    def __bool__(self):
        return self.xyxy.shape[0] > 0


class _Masks:
    def __init__(self, polys=None):
        self.xy = polys if polys is not None else []


class _Result:
    def __init__(self, boxes, masks):
        self.boxes = boxes
        self.masks = masks

def _predict_one(img, conf, dx, dy, r, out_pred, out_proto):
    """فكّ تشفير كشف واحد: يرد Result بحدود/قناع بإحداثيات الصورة الأصلية.

    يتطابق منطق فكّ الترميز مع ultralytics نفسها:
    - boxes: (ب640 - padding) / r ← إحداثيات الصورة الأصلية.
    - mask : يُعاد مقياسه أولاً إلى شبكة 640 (نفس حجم إدخال الـ letterbox)،
      ثم يُقصّ منه نافذة الصورة الفعلية غير المبطّنة (المستطيل داخل الحشوة)
      وأخيراً يُعاد مقياسه إلى أبعاد الصورة الأصلية — فلا تتشوه النسب عند
      أي نسبة أبعاد (aspect ratio) للصورة، وتبقى إحداثيات القصّ دقيقة تماماً.
    """
    import cv2
    H, W = img.shape[:2]
    pred = np.asarray(out_pred[0], dtype=np.float32)   # (37, 8400)
    protos = np.asarray(out_proto[0], dtype=np.float32)  # (32,160,160)
    n_anchors = pred.shape[1]

    if pred.shape[0] < 37:
        logger.warning("YOLO ONNX: تنسيق مخرجات غير متوقع %s", pred.shape)
        return _Result(_Boxes(), _Masks())

    box_xywh = pred[:4].T                                # (8400,4) في فضاء 640
    cls = pred[4]                                        # (8400,) بعد sigmoid
    mc = pred[5:37].T                                    # (8400,32)

    keep = np.where(cls > conf)[0]
    if keep.size == 0:
        return _Result(_Boxes(), _Masks())

    cand_boxes = _xywh2xyxy(box_xywh[keep])
    cand_scores = cls[keep].astype(float)
    order = np.argsort(-cand_scores, kind="stable")
    cand_boxes, cand_scores = cand_boxes[order], cand_scores[order]
    cand_mc = mc[keep][order]
    nms_idx = _nms(cand_boxes, list(cand_scores))

    # أبعاد الصورة الفعلية (غير المبطّنة) داخل شبكة الـ letterbox 640
    unpad_w = int(round(W * r))
    unpad_h = int(round(H * r))
    box_x0 = int(round(dx))
    box_y0 = int(round(dy))

    if len(nms_idx) == 0:
        return _Result(_Boxes(), _Masks())

    boxes = []
    masks_xy = []
    for i in nms_idx:
        b640 = cand_boxes[i]
        x1 = (b640[0] - dx) / r
        y1 = (b640[1] - dy) / r
        x2 = (b640[2] - dx) / r
        y2 = (b640[3] - dy) / r
        boxes.append([max(0.0, x1), max(0.0, y1), min(float(W), x2), min(float(H), y2)])
        try:
            mask_soft = np.matmul(cand_mc[i], protos.reshape(32, -1)).reshape(_MASK_HW, _MASK_HW)
            mask_soft = 1.0 / (1.0 + np.exp(-mask_soft))
            # crop_mask وفق المربع في شبكة 160 (div 4)
            b160 = [b640[0] / 4, b640[1] / 4, b640[2] / 4, b640[3] / 4]
            mask_soft = _crop_mask(mask_soft, b160)
            # 1) مقياس إلى شبكة 640 (نفس letterbox)
            mask_640 = cv2.resize(mask_soft, (_IMGSZ, _IMGSZ), interpolation=cv2.INTER_LINEAR)
            # 2) قصّ نافذة الصورة الفعلية من داخل الحشوة (عكس letterbox بدقة)
            if unpad_h > 0 and unpad_w > 0:
                roi = mask_640[box_y0:box_y0 + unpad_h, box_x0:box_x0 + unpad_w]
            else:
                roi = mask_640
            # 3) مقياس إلى الأبعاد الأصلية — بلا تشويه النسب
            mask_orig = cv2.resize(roi, (W, H), interpolation=cv2.INTER_LINEAR)
            poly = _poly_from_mask(mask_orig > 0.5)
            masks_xy.append(poly if poly is not None else None)
        except Exception as e:  # pragma: no cover
            logger.warning("YOLO ONNX: فشل بناء القناع (%s)", e)
            masks_xy.append(None)

    return _Result(
        _Boxes(xyxy=np.asarray(boxes, dtype=np.float32).reshape(-1, 4),
               conf=np.asarray([float(cand_scores[j]) for j in nms_idx], dtype=np.float32)),
        _Masks(masks_xy),
    )


def predict_onnx(img, conf=0.6, imgsz=_IMGSZ, verbose=False):
    """predict داخل في نسخة ultralytics: يرد قائمة [Result] بإحداثيات الصورة الأصلية.

    img: ndarray RGB (كما يمررها الوكيل المحلي من PIL). تُعكس القنوات بنفس
    معالجة ultralytics (المدخلات numpy تفترض BGR ثم تُقلب إلى RGB للشبكة).
    """
    sess = _load()
    if sess is None:
        return [_Result(_Boxes(), _Masks())]
    import cv2
    src = np.asarray(img)
    if src.ndim == 2:
        src = cv2.cvtColor(src, cv2.COLOR_GRAY2BGR)
    canvas, r, (dw, dh) = _letterbox(src, new_shape=imgsz if imgsz == _IMGSZ else _IMGSZ)
    dx, dy = float(dw), float(dh)
    # نفس تحويل ultralytics: numpy مفترض BGR ← قلب للقنوات إلى RGB ثم /255
    rgb = canvas[..., ::-1].astype(np.float32) / 255.0
    blob = np.ascontiguousarray(np.transpose(rgb, (2, 0, 1)))[None]
    try:
        out = sess.run(None, {"images": blob})
    except Exception as e:  # pragma: no cover
        logger.error("فشل تشغيل YOLO ONNX: %s", e)
        return [_Result(_Boxes(), _Masks())]
    out_pred = out[0]
    out_proto = out[1] if len(out) > 1 else None
    if out_proto is None:
        logger.warning("YOLO ONNX: لا يوجد مخرج قناع — علاج ككشف عادي بلا masks")
        return [_Result(_Boxes(), _Masks())]
    return [_predict_one(src, conf, dx, dy, r, out_pred, out_proto)]