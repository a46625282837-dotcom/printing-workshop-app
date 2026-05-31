import logging
from PIL import Image

logger = logging.getLogger(__name__)


def order_points(pts):
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


def _find_card_from_edges(pil_image: Image.Image):
    import cv2
    import numpy as np
    img = np.array(pil_image.convert("RGB"))
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    median = np.median(blurred)

    canny_params = [
        (int(max(0, 0.3 * median)), int(min(255, 0.9 * median))),
        (int(max(0, 0.5 * median)), int(min(255, 1.5 * median))),
        (int(max(0, 0.7 * median)), int(min(255, 2.0 * median))),
        (10, 50),
        (30, 100),
    ]

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    epsilons = (0.005, 0.01, 0.015, 0.02, 0.03, 0.04, 0.05, 0.07, 0.1)
    h, w = gray.shape
    img_area = w * h
    min_area = max(2000, int(img_area * 0.03))

    for lower, upper in canny_params:
        edged = cv2.Canny(blurred, lower, upper)
        edged = cv2.dilate(edged, kernel, iterations=2)
        contours, hierarchy = cv2.findContours(edged, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue

        candidates = []
        for cnt in contours:
            area = int(cv2.contourArea(cnt))
            if area < min_area:
                continue
            if area > img_area * 0.98:
                continue
            peri = cv2.arcLength(cnt, True)
            for eps in epsilons:
                approx = cv2.approxPolyDP(cnt, eps * peri, True)
                if len(approx) == 4:
                    pts = approx.reshape(4, 2)
                    rect = order_points(pts)
                    (tl, tr, br, bl) = rect
                    ww = np.linalg.norm(tr - tl)
                    wh = np.linalg.norm(bl - tl)
                    if ww < 1 or wh < 1:
                        continue
                    aspect = ww / wh if ww > wh else wh / ww
                    if aspect < 1.2 or aspect > 3.0:
                        continue
                    candidates.append((area, eps, rect))
                    break

        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            warped = four_point_transform(img_bgr, candidates[0][2])
            warped_rgb = cv2.cvtColor(warped, cv2.COLOR_BGR2RGB)
            logger.info("تم استخراج البطاقة عبر الحواف (Canny %d/%d)", lower, upper)
            return Image.fromarray(warped_rgb)

        for cnt in contours:
            area = int(cv2.contourArea(cnt))
            if area < min_area or area > img_area * 0.98:
                continue
            rect = cv2.minAreaRect(cnt)
            box = cv2.boxPoints(rect)
            box = order_points(box)
            (tl, tr, br, bl) = box
            ww = np.linalg.norm(tr - tl)
            wh = np.linalg.norm(bl - tl)
            if ww < 1 or wh < 1:
                continue
            aspect = ww / wh if ww > wh else wh / ww
            if aspect < 1.2 or aspect > 3.0:
                continue
            img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            warped = four_point_transform(img_bgr, box)
            warped_rgb = cv2.cvtColor(warped, cv2.COLOR_BGR2RGB)
            logger.info("تم استخراج البطاقة عبر الحواف (minAreaRect %d/%d)", lower, upper)
            return Image.fromarray(warped_rgb)

    return None


def _find_card_from_alpha(pil_image: Image.Image):
    from .photo_processor import remove_background
    import cv2
    import numpy as np
    bg_removed = remove_background(pil_image)
    bg_rgba = bg_removed.convert("RGBA")
    alpha = np.array(bg_rgba.split()[-1])
    if int(np.sum(alpha > 0)) <= 10:
        return None

    alpha_smooth = cv2.GaussianBlur(alpha, (5, 5), 0)
    _, binary = cv2.threshold(alpha_smooth, 20, 255, cv2.THRESH_BINARY)
    binary = binary.astype(np.uint8)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    min_area = max(2000, int(pil_image.size[0] * pil_image.size[1] * 0.015))
    epsilons = (0.005, 0.01, 0.015, 0.02, 0.03, 0.04, 0.05, 0.07, 0.1)
    img_area = pil_image.size[0] * pil_image.size[1]
    largest = max(contours, key=cv2.contourArea)
    area = int(cv2.contourArea(largest))

    if area <= min_area:
        return None

    peri = cv2.arcLength(largest, True)
    for eps in epsilons:
        approx = cv2.approxPolyDP(largest, eps * peri, True)
        if len(approx) == 4 and area < img_area * 0.98:
            pts = approx.reshape(4, 2).astype(np.float32)
            rect = order_points(pts)
            (tl, tr, br, bl) = rect
            ww = np.linalg.norm(tr - tl)
            wh = np.linalg.norm(bl - tl)
            if ww >= 1 and wh >= 1:
                aspect = ww / wh if ww > wh else wh / ww
                if 1.2 <= aspect <= 3.0:
                    img_bgr = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
                    warped = four_point_transform(img_bgr, rect)
                    warped_rgb = cv2.cvtColor(warped, cv2.COLOR_BGR2RGB)
                    logger.info("تم استخراج البطاقة عبر AI + تصحيح المنظور")
                    return Image.fromarray(warped_rgb)

    rect = cv2.minAreaRect(largest)
    box = cv2.boxPoints(rect)
    box = order_points(box)
    (tl, tr, br, bl) = box
    ww = np.linalg.norm(tr - tl)
    wh = np.linalg.norm(bl - tl)
    if ww >= 1 and wh >= 1:
        aspect = ww / wh if ww > wh else wh / ww
        if 1.2 <= aspect <= 3.0:
            img_bgr = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
            warped = four_point_transform(img_bgr, box)
            warped_rgb = cv2.cvtColor(warped, cv2.COLOR_BGR2RGB)
            logger.info("تم استخراج البطاقة عبر AI + minAreaRect")
            return Image.fromarray(warped_rgb)

    hull = cv2.convexHull(largest)
    hull_peri = cv2.arcLength(hull, True)
    for eps in epsilons:
        approx = cv2.approxPolyDP(hull, eps * hull_peri, True)
        if len(approx) == 4:
            pts = approx.reshape(4, 2).astype(np.float32)
            img_bgr = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
            warped = four_point_transform(img_bgr, order_points(pts))
            warped_rgb = cv2.cvtColor(warped, cv2.COLOR_BGR2RGB)
            logger.info("تم استخراج البطاقة عبر AI + convexHull")
            return Image.fromarray(warped_rgb)

    bbox = Image.fromarray(alpha).getbbox()
    if bbox is not None:
        x1, y1, x2, y2 = bbox
        pad = int(max(pil_image.size) * 0.01)
        x1 = max(0, x1 - pad)
        y1 = max(0, y1 - pad)
        x2 = min(pil_image.width, x2 + pad)
        y2 = min(pil_image.height, y2 + pad)
        cropped = bg_rgba.crop((x1, y1, x2, y2))
        logger.info("تم استخراج البطاقة عبر AI + الاقتصاص (%d×%d) RGBA", x2 - x1, y2 - y1)
        return cropped
    return None


def _find_card_from_color(pil_image: Image.Image):
    import cv2
    import numpy as np
    img = np.array(pil_image.convert("RGB"))
    h, w = img.shape[:2]
    pixels = img.reshape(-1, 3).astype(np.float32)

    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
    _, labels, centers = cv2.kmeans(pixels, 4, None, criteria, 5, cv2.KMEANS_RANDOM_CENTERS)

    labels_2d = labels.reshape(h, w)
    center_mask = np.zeros((h, w), dtype=np.uint8)

    center_y, center_x = h // 2, w // 2
    center_label = labels_2d[center_y, center_x]
    center_mask[labels_2d == center_label] = 255

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    center_mask = cv2.morphologyEx(center_mask, cv2.MORPH_CLOSE, kernel)
    center_mask = cv2.morphologyEx(center_mask, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(center_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    img_area = w * h
    min_area = max(2000, int(img_area * 0.03))
    largest = max(contours, key=cv2.contourArea)
    area = int(cv2.contourArea(largest))

    if area < min_area or area > img_area * 0.98:
        return None

    epsilons = (0.005, 0.01, 0.015, 0.02, 0.03, 0.04, 0.05, 0.07, 0.1)
    peri = cv2.arcLength(largest, True)
    for eps in epsilons:
        approx = cv2.approxPolyDP(largest, eps * peri, True)
        if len(approx) == 4:
            pts = approx.reshape(4, 2).astype(np.float32)
            rect = order_points(pts)
            img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            warped = four_point_transform(img_bgr, rect)
            warped_rgb = cv2.cvtColor(warped, cv2.COLOR_BGR2RGB)
            logger.info("تم استخراج البطاقة عبر الألوان + تصحيح المنظور")
            return Image.fromarray(warped_rgb)

    rect = cv2.minAreaRect(largest)
    box = cv2.boxPoints(rect)
    box = order_points(box)
    (tl, tr, br, bl) = box
    ww = np.linalg.norm(tr - tl)
    wh = np.linalg.norm(bl - tl)
    if ww >= 1 and wh >= 1:
        aspect = ww / wh if ww > wh else wh / ww
        if 1.2 <= aspect <= 3.0:
            img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            warped = four_point_transform(img_bgr, box)
            warped_rgb = cv2.cvtColor(warped, cv2.COLOR_BGR2RGB)
            logger.info("تم استخراج البطاقة عبر الألوان + minAreaRect")
            return Image.fromarray(warped_rgb)

    x, y, bw, bh = cv2.boundingRect(largest)
    if bw > 10 and bh > 10:
        cropped = img[y:y + bh, x:x + bw]
        logger.info("تم استخراج البطاقة عبر الألوان + مستطيل محدق (%d×%d)", bw, bh)
        return Image.fromarray(cropped)

    return None


def extract_card(pil_image: Image.Image) -> Image.Image:
    try:
        result = _find_card_from_alpha(pil_image)
        if result is not None:
            return result
        result = _find_card_from_edges(pil_image)
        if result is not None:
            return result
        result = _find_card_from_color(pil_image)
        if result is not None:
            return result
        white = Image.new("RGBA", pil_image.size, (255, 255, 255, 255))
        composite = Image.alpha_composite(white, pil_image.convert("RGBA"))
        logger.info("تم استخراج البطاقة عبر الخلفية البيضاء (فشلت جميع الطرق)")
        return composite.convert("RGB")
    except Exception as e:
        logger.error("فشل استخراج البطاقة", exc_info=True)
        return pil_image