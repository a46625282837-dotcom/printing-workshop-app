"""Professional Photo Enhancement — GFPGAN + Real-ESRGAN (Remini-quality).

Uses PyTorch deep learning models for face restoration and detail enhancement.
GFPGAN v1.4 for face restoration, RealESRGAN for background detail sharpening.
Falls back to MediaPipe + OpenCV if models unavailable.
"""
import logging
import os
import sys
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

WEIGHTS_DIR = os.path.join(os.path.expanduser("~"), ".cache", "wwk_ai")

_GFPGANER = None
_REALESRGANER = None
_init_done = False


def is_available() -> bool:
    try:
        import torch
        import cv2
        return True
    except ImportError:
        return False


def _init_models():
    global _GFPGANER, _REALESRGANER, _init_done
    if _init_done:
        return
    _init_done = True

    try:
        import torch
        from gfpgan import GFPGANer

        model_path = os.path.join(WEIGHTS_DIR, "GFPGANv1.4.pth")
        if not os.path.exists(model_path):
            _download(
                "https://huggingface.co/Apex-X/gfpgan.pth/resolve/main/GFPGANv1.4.pth",
                model_path,
            )

        _GFPGANER = GFPGANer(
            model_path=model_path,
            upscale=1,
            arch="clean",
            channel_multiplier=2,
            bg_upsampler=None,
        )
        logger.info("GFPGAN v1.4 loaded")
    except Exception as e:
        logger.warning("GFPGAN init failed: %s", e)

    try:
        import torch
        from basicsr.archs.rrdbnet_arch import RRDBNet
        from realesrgan import RealESRGANer

        model_path = os.path.join(WEIGHTS_DIR, "RealESRGAN_x4plus.pth")
        if not os.path.exists(model_path):
            _download(
                "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
                model_path,
            )

        model = RRDBNet(
            num_in_ch=3,
            num_out_ch=3,
            num_feat=64,
            num_block=23,
            num_grow_ch=32,
            scale=4,
        )
        _REALESRGANER = RealESRGANer(
            scale=4,
            model_path=model_path,
            model=model,
            tile=400,
            tile_pad=10,
            pre_pad=0,
            half=False,
        )
        logger.info("RealESRGAN loaded")
    except Exception as e:
        logger.warning("RealESRGAN init failed: %s", e)


def _download(url, dest):
    import urllib.request
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    logger.info("تحميل %s …", os.path.basename(dest))
    urllib.request.urlretrieve(url, dest)
    logger.info("تم التحميل → %s (%.1f MB)", dest, os.path.getsize(dest) / 1024 / 1024)


def _color_correct(original, enhanced, face_mask=None):
    import cv2

    orig_lab = cv2.cvtColor(original, cv2.COLOR_BGR2LAB).astype(np.float32)
    enh_lab = cv2.cvtColor(enhanced, cv2.COLOR_BGR2LAB).astype(np.float32)

    for ch in range(3):
        o_mean = np.mean(orig_lab[:, :, ch])
        o_std = np.std(orig_lab[:, :, ch]) + 1e-6
        e_mean = np.mean(enh_lab[:, :, ch])
        e_std = np.std(enh_lab[:, :, ch]) + 1e-6

        enh_lab[:, :, ch] = (enh_lab[:, :, ch] - e_mean) * (o_std / e_std) + o_mean

    result = np.clip(enh_lab, 0, 255).astype(np.uint8)
    result = cv2.cvtColor(result, cv2.COLOR_LAB2BGR)

    if face_mask is not None:
        mask_f = cv2.GaussianBlur(
            face_mask.astype(np.float32) / 255.0, (31, 31), 0
        )[:, :, np.newaxis]
        result = np.clip(
            result.astype(np.float32) * mask_f
            + enhanced.astype(np.float32) * (1 - mask_f),
            0,
            255,
        ).astype(np.uint8)

    return result


def _color_correct_full(original, enhanced):
    import cv2

    orig_lab = cv2.cvtColor(original, cv2.COLOR_BGR2LAB).astype(np.float32)
    enh_lab = cv2.cvtColor(enhanced, cv2.COLOR_BGR2LAB).astype(np.float32)

    for ch in range(3):
        o_mean = np.mean(orig_lab[:, :, ch])
        o_std = np.std(orig_lab[:, :, ch]) + 1e-6
        e_mean = np.mean(enh_lab[:, :, ch])
        e_std = np.std(enh_lab[:, :, ch]) + 1e-6
        enh_lab[:, :, ch] = (enh_lab[:, :, ch] - e_mean) * (o_std / e_std) + o_mean

    result = np.clip(enh_lab, 0, 255).astype(np.uint8)
    return cv2.cvtColor(result, cv2.COLOR_LAB2BGR)


def _detect_face_mask(bgr):
    import cv2

    try:
        import mediapipe as mp
        mesh = mp.solutions.face_mesh.FaceMesh(
            static_image_mode=True, max_num_faces=5, min_detection_confidence=0.3
        )
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        results = mesh.process(rgb)
        if results.multi_face_landmarks:
            mask = np.zeros((h, w), dtype=np.uint8)
            for face_lms in results.multi_face_landmarks:
                points = []
                for idx in [10, 338, 297, 332, 284, 251, 389, 356, 454, 323,
                            361, 288, 397, 365, 379, 378, 400, 377, 152, 148,
                            176, 149, 150, 136, 172, 58, 132, 93, 234, 127,
                            162, 21, 54, 103, 67, 109]:
                    lm = face_lms.landmark[idx]
                    points.append([int(lm.x * w), int(lm.y * h)])
                cv2.fillConvexPoly(mask, np.array(points), 255)
            mask = cv2.GaussianBlur(mask, (21, 21), 0)
            return mask
    except Exception as e:
        logger.warning("MediaPipe face mask failed: %s", e)

    try:
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        if not os.path.exists(cascade_path):
            logger.warning("Haar cascade not found at %s", cascade_path)
            return None
        cascade = cv2.CascadeClassifier(cascade_path)
        if cascade.empty():
            logger.warning("Haar cascade failed to load")
            return None
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, 1.05, 3, minSize=(40, 40))
        if len(faces) > 0:
            mask = np.zeros(bgr.shape[:2], dtype=np.uint8)
            for (fx, fy, fw, fh) in faces:
                cv2.ellipse(
                    mask,
                    (fx + fw // 2, fy + fh // 2),
                    (int(fw * 0.6), int(fh * 0.7)),
                    0, 0, 360, 255, -1,
                )
            mask = cv2.GaussianBlur(mask, (21, 21), 0)
            return mask
    except Exception as e:
        logger.warning("Haar cascade failed: %s", e)

    return None


def _remove_blemishes(img, skin_mask):
    import cv2
    if skin_mask is None or cv2.countNonZero(skin_mask) == 0:
        return img
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred_large = cv2.GaussianBlur(gray.astype(np.float32), (31, 31), 0)
    diff = blurred_large - gray.astype(np.float32)
    dark_spots = (diff > 6).astype(np.uint8) * 255
    dark_spots = cv2.bitwise_and(dark_spots, skin_mask)
    kernel_small = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    dark_spots = cv2.morphologyEx(dark_spots, cv2.MORPH_OPEN, kernel_small)
    dark_spots = cv2.morphologyEx(dark_spots, cv2.MORPH_CLOSE, kernel_small)
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(dark_spots, connectivity=8)
    count = 0
    for i in range(1, num_labels):
        area = stats[i, cv2.CC_STAT_AREA]
        if area < 3 or area > 500:
            continue
        x = stats[i, cv2.CC_STAT_LEFT]
        y = stats[i, cv2.CC_STAT_TOP]
        w_cc = stats[i, cv2.CC_STAT_WIDTH]
        h_cc = stats[i, cv2.CC_STAT_HEIGHT]
        pad = max(w_cc, h_cc) // 2 + 4
        y1 = max(0, y - pad)
        y2 = min(img.shape[0], y + h_cc + pad)
        x1 = max(0, x - pad)
        x2 = min(img.shape[1], x + w_cc + pad)
        roi = img[y1:y2, x1:x2]
        if roi.size == 0:
            continue
        roi_blur = cv2.GaussianBlur(roi, (9, 9), 0)
        comp_mask = (labels[y1:y2, x1:x2] == i).astype(np.float32)
        comp_mask = cv2.GaussianBlur(comp_mask, (9, 9), 0)[:, :, np.newaxis]
        img[y1:y2, x1:x2] = np.clip(
            roi.astype(np.float32) * (1 - comp_mask) + roi_blur.astype(np.float32) * comp_mask,
            0, 255
        ).astype(np.uint8)
        count += 1
    logger.info("إزالة الحبوب: %d منطقة", count)
    return img


def _smooth_skin(img, mask, strength=0.4):
    import cv2
    if mask is None or cv2.countNonZero(mask) == 0:
        return img
    smoothed = cv2.bilateralFilter(img, 7, 40, 40)
    mask_f = cv2.GaussianBlur(mask.astype(np.float32) / 255.0, (15, 15), 0)
    mask_f = np.clip(mask_f * strength, 0, 1)[:, :, np.newaxis]
    result = img.astype(np.float32) * (1 - mask_f) + smoothed.astype(np.float32) * mask_f
    return np.clip(result, 0, 255).astype(np.uint8)


def _reduce_shadows(img, face_mask):
    import cv2
    if face_mask is None or cv2.countNonZero(face_mask) == 0:
        return img
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l_f = l.astype(np.float32)
    face_f = cv2.GaussianBlur(face_mask.astype(np.float32), (31, 31), 0) / 255.0
    local_mean = cv2.GaussianBlur(l_f, (51, 51), 0)
    shadow_map = np.clip(local_mean - l_f, 0, 255)
    shadow_mask = np.clip(shadow_map / 30.0, 0, 1.0) * face_f
    l_lifted = l_f + shadow_mask * 20
    l_final = np.clip(l_f * (1 - shadow_mask) + l_lifted * shadow_mask, 0, 255)
    lab = cv2.merge([l_final.astype(np.uint8), a, b])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def enhance_image(pil_image, fidelity_weight=0.50):
    import cv2

    _init_models()

    img = np.array(pil_image.convert("RGB"))
    img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    original_bgr = img_bgr.copy()

    face_mask = _detect_face_mask(img_bgr)

    if _GFPGANER is not None:
        try:
            import torch
            torch.set_num_threads(2)
            _, _, restored = _GFPGANER.enhance(
                img_bgr, has_aligned=False, only_center_face=False, paste_back=True
            )
            if restored is not None:
                restored_uint8 = np.clip(restored * 255, 0, 255).astype(np.uint8) if restored.dtype == np.float32 else restored
                img_bgr = _color_correct(original_bgr, restored_uint8, face_mask)
                logger.info("GFPGAN face restoration done")
            else:
                logger.info("GFPGAN: no face detected, using original")
        except Exception as e:
            logger.warning("GFPGAN enhance failed: %s", e)

    if face_mask is not None:
        skin_mask = face_mask.copy()
        eye_mask_left = np.zeros_like(skin_mask)
        eye_mask_right = np.zeros_like(skin_mask)
        try:
            import mediapipe as mp
            mesh = mp.solutions.face_mesh.FaceMesh(
                static_image_mode=True, max_num_faces=5, min_detection_confidence=0.3
            )
            rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
            results = mesh.process(rgb)
            if results.multi_face_landmarks:
                h, w = skin_mask.shape
                for fl in results.multi_face_landmarks:
                    for idx in [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398,
                                33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246]:
                        lm = fl.landmark[idx]
                        px, py = int(lm.x * w), int(lm.y * h)
                        if idx < 100:
                            cv2.circle(eye_mask_left, (px, py), 15, 255, -1)
                        else:
                            cv2.circle(eye_mask_right, (px, py), 15, 255, -1)
        except Exception:
            pass
        eye_mask = cv2.bitwise_or(eye_mask_left, eye_mask_right)
        skin_only = cv2.bitwise_and(skin_mask, cv2.bitwise_not(eye_mask))

        img_bgr = _remove_blemishes(img_bgr, skin_only)
        img_bgr = _reduce_shadows(img_bgr, face_mask)
        img_bgr = _smooth_skin(img_bgr, skin_only, strength=0.35)

    if _REALESRGANER is not None:
        try:
            import torch
            torch.set_num_threads(2)
            h, w = img_bgr.shape[:2]
            max_side = 1200
            if max(h, w) > max_side:
                scale = max_side / max(h, w)
                small = cv2.resize(img_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
            else:
                small = img_bgr

            output, _ = _REALESRGANER.enhance(small, outscale=1)
            if output is not None:
                output_uint8 = np.clip(output * 255, 0, 255).astype(np.uint8) if output.dtype == np.float32 else output
                output_resized = cv2.resize(output_uint8, (w, h), interpolation=cv2.INTER_LANCZOS4)

                if face_mask is not None:
                    mask_f = cv2.GaussianBlur(
                        face_mask.astype(np.float32) / 255.0, (31, 31), 0
                    )[:, :, np.newaxis]
                    img_bgr = np.clip(
                        img_bgr.astype(np.float32) * mask_f
                        + output_resized.astype(np.float32) * (1 - mask_f),
                        0, 255,
                    ).astype(np.uint8)
                else:
                    img_bgr = output_resized
                logger.info("RealESRGAN detail enhancement done")
        except Exception as e:
            logger.warning("RealESRGAN enhance failed: %s", e)

    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8))
    l = clahe.apply(l.astype(np.uint8)).astype(np.float32)
    lab = cv2.merge([l, a, b])
    img_bgr = cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)

    img_bgr = _color_correct_full(original_bgr, img_bgr)

    kernel = np.array([[0, -0.3, 0], [-0.3, 2.2, -0.3], [0, -0.3, 0]], dtype=np.float32)
    img_bgr = cv2.filter2D(img_bgr, -1, kernel)

    result_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    logger.info("التحسين المكتمل")
    return Image.fromarray(result_rgb)
