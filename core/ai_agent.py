"""وكيل قصّ الهويات — أمامي محلي (YOLO) فقط.

لا يعتمد على أي خدمة سحابية: YOLOv8 المدرب يقصّ مباشرة عبر العارض
المحلي (ultralytics إن وُجد، وإلا نسخة ONNX المضمّنة card_detector).
احتياط أخير: طريقة 1.4.3 على الصورة كاملة.
"""

import datetime
import logging
import os

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_crop_log.txt")

# مؤقت للتجربة: إيقاف احتياط 1.4.3 لتقييم مسار البايثون YOLO وحده فقط.
# أُرجع القيمة إلى True لتفعيل الاحتياط من جديد.
_V143_FALLBACK_ENABLED = False


class StatusBridge(QObject):
    """جسر إشارات من خيوط العمل إلى الواجهة (يطبع حالة الجاري على الشاشة)."""
    status = Signal(str)


status_bridge = StatusBridge()


def _notify_status(text: str) -> None:
    try:
        status_bridge.status.emit(text)
    except Exception:
        pass


_ai_ok_count = 0
_ai_fail_count = 0
_ai_used_this_batch = False


def reset_batch_tracking() -> None:
    """يُستدعى قبل بدء دفعة قصّ جديدة."""
    global _ai_ok_count, _ai_fail_count, _ai_used_this_batch
    _ai_ok_count = 0
    _ai_fail_count = 0
    _ai_used_this_batch = False


def mark_ai_used() -> None:
    global _ai_used_this_batch, _ai_ok_count
    _ai_used_this_batch = True
    _ai_ok_count += 1


def mark_ai_failed() -> None:
    global _ai_fail_count
    _ai_fail_count += 1


def batch_counts():
    return {"ok": _ai_ok_count, "fail": _ai_fail_count}


def ai_used_this_batch() -> bool:
    return _ai_used_this_batch


def get_mode() -> str:
    """يُحتفظ بها للتوافق — الوكيل الوحيد هو المحلي (YOLO)."""
    return "local"


def set_mode(mode: str) -> None:
    """يُحتفظ بها للتوافق — الوكيل الوحيد هو المحلي (YOLO)."""
    return None


# --------------------------------------------------------------------------
# سجلّ التأكيد (أي محرك قصّ الصورة)
# --------------------------------------------------------------------------
def log_crop(image_name: str, engine: str, detail: str = "") -> None:
    """يكتب سطراً في سجل ai_crop_log.txt: المحرك + التفاصيل."""
    entry = "[{ts}] {engine} | {name}{detail}".format(
        ts=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        engine=engine,
        name=image_name or "?",
        detail=(" | " + detail) if detail else "",
    )
    logger.info(entry)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except OSError:
        pass


def get_crop_log(max_lines: int = 50) -> str:
    try:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        return "\n".join(lines[-max_lines:])
    except OSError:
        return "لا يوجد سجل بعد"


# --------------------------------------------------------------------------
# القص الرئيسي
# --------------------------------------------------------------------------
def ai_cut_card(pil_image, image_name: str = "", dpi: int = 600,
                corners_out=None):
    """قصّ الهوية عبر الوكيل المحلي فقط (YOLO + احتياط 1.4.3).

    - تنجح: تُرجع صورة مقصوصة نظيفة.
    - تفشل: تُرجع None ولا تُقصّ بشيء.
    يكتب في السجلّ السببَ ويُحدّث عدّادات الدفعة.
    """
    try:
        from PIL import ImageOps
        pil_image = ImageOps.exif_transpose(pil_image)
    except Exception:
        pass
    return _local_yolo_cut(pil_image, image_name, dpi, corners_out=corners_out)


def _local_yolo_cut(pil_image, image_name: str = "", dpi: int = 600,
                    corners_out=None):
    """الوكيل المحلي: YOLO المدرب يقصّ مباشرة، و1.4.3 احتياط أخير فقط.

    1) YOLO المدرب يقصّ كل صورة يكتشفها مباشرة cut_card_local
       (بالمحلي: ultralytics إن وُجد، وإلا card_detector.onnx المضمّنة).
    2) إن لم يجد YOLO بطاقة إطلاقاً: 1.4.3 على الصورة كاملة احتياط أخير.
    """
    import numpy as np

    try:
        mark_ai_used()
        from core.local_yolo import cut_card_local, has_local_model
        from core.v143_extractor import extract_card_v143
        from core.card_cutter import _fit_to_card_slot

        from core.card_detector import card_model_available
        if has_local_model() or card_model_available():
            _notify_status("وكيل YOLOv8 المحلي يعالج {}...".format(image_name or "الصورة"))
            result = cut_card_local(pil_image, dpi, corners_out=corners_out)
            if result is not None:
                log_crop(image_name, "محلي-YOLO8",
                         "{}x{}".format(result.width, result.height))
                logger.info("تم القصّ عبر YOLOv8 — %dx%d",
                            result.width, result.height)
                return result

        if _V143_FALLBACK_ENABLED:
            _notify_status("الوكيل المحلي (طريقة 1.4.3) يعالج {}...".format(image_name or "الصورة"))
            result = extract_card_v143(pil_image.convert("RGB"))

            if result is not None:
                log_crop(image_name, "طريقة 1.4.3",
                         "{}x{}".format(result.width, result.height))
                logger.info("تم القصّ عبر طريقة 1.4.3 — %dx%d",
                            result.width, result.height)
                if corners_out is not None:
                    from core.local_yolo import _map_corners_to_result
                    from core.card_cutter import _target_px
                    tw, th = _target_px(dpi)
                    corners_out[:] = _map_corners_to_result(
                        [(0.0, 0.0), (tw, 0.0), (tw, th), (0.0, th)],
                        tw, th, dpi)
                return _fit_to_card_slot(result, dpi)

        mark_ai_failed()
        log_crop(image_name, "مرفوض", "YOLO + 1.4.3 لم يتعرفا على البطاقة")
        _notify_status("الوكيل المحلي لم يجد بطاقة في {}".format(image_name or "الصورة"))
        return None
    except Exception as e:
        mark_ai_failed()
        log_crop(image_name, "مرفوض", "خطأ YOLO+1.4.3: {}".format(e))
        logger.warning("الوكيل المحلي (YOLO+1.4.3) فشل (%s)", e)
        _ = np  # noqa: F841 — يُبقي الاستيراد واضحاً للاستخدام مستقبلاً
        return None