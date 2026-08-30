import logging
import os
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageColor

logger = logging.getLogger(__name__)

SS = 3  # supersample factor for smooth garment edges
FW, FH = 320, 560  # final garment resolution (portrait)


def _img_dir():
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, 'img')
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'img')


CLOTHES_DIR = os.path.join(_img_dir(), 'clothes')

GARMENT_ITEMS = [
    ("suit_black", "بدلة سوداء"),
    ("suit_navy", "بدلة كحلية"),
    ("suit_gray", "بدلة رمادية"),
    ("shirt_white", "قميص أبيض"),
    ("thobe_white", "ثوب أبيض"),
    ("thobe_beige", "ثوب بيج"),
    ("bisht_brown", "بشت بني"),
    ("abaya_black", "عباية سوداء"),
]


def get_catalog():
    """Return list of {id, title, path}. Prefers user garments (garment_*.png)."""
    _ensure_defaults()
    try:
        user = sorted(
            f[:-4] for f in os.listdir(CLOTHES_DIR)
            if f.startswith('garment_') and f.endswith('.png'))
    except Exception:
        user = []
    if user:
        return [{"id": gid, "title": gid[len('garment_'):],
                 "path": os.path.join(CLOTHES_DIR, gid + ".png")} for gid in user]
    return [{"id": i, "title": t, "path": os.path.join(CLOTHES_DIR, i + ".png")}
            for i, t in GARMENT_ITEMS]


def _ensure_defaults():
    try:
        os.makedirs(CLOTHES_DIR, exist_ok=True)
        for gid, _title in GARMENT_ITEMS:
            p = os.path.join(CLOTHES_DIR, gid + ".png")
            if not os.path.exists(p):
                arr = load_rgba(gid)
                if arr is not None:
                    Image.fromarray(arr, "RGBA").save(p)
    except Exception as e:
        logger.warning("تعذر حفظ صور الملابس: %s", e)


_cache = {}


def load_rgba(label):
    """Return (H, W, 4) RGBA uint8 array for a garment id, or None."""
    if label in _cache:
        return _cache[label]
    arr = None
    p = os.path.join(CLOTHES_DIR, label + ".png")
    if os.path.exists(p):
        try:
            import cv2
            bgra = cv2.imread(p, cv2.IMREAD_UNCHANGED)
            if bgra is not None and bgra.ndim == 3 and bgra.shape[2] == 4:
                arr = cv2.cvtColor(bgra, cv2.COLOR_BGRA2RGBA)
        except Exception as e:
            logger.warning("fشغلة تحميل الملابس %s: %s", p, e)
            arr = None
    if arr is None:
        arr = _make_garment(label)
    if arr is None:
        return None
    arr = np.ascontiguousarray(arr)
    _cache[label] = arr
    return arr


def lay_clothes(rgb, cloth_rgba, state, face_rect=None):
    """Dress a portrait: warp the garment onto the body and blend it in.

    Unlike a flat overlay, the garment width follows the person's shoulders
    (from the detected face) and is clipped + feathered so the fabric hugs
    the torso instead of floating above the photo.
    """
    if cloth_rgba is None:
        return rgb
    H, W = rgb.shape[:2]
    cH, cW = cloth_rgba.shape[:2]
    alpha = cloth_rgba[:, :, 3]
    ys, xs = np.where(alpha > 0)
    if len(xs) < 50:
        return rgb

    eta = float(H) / 1000.0
    scale = float(state.get("scale", 100)) / 100.0
    voff = float(state.get("voff", 0)) * eta
    hoff = float(state.get("hoff", 0)) * eta

    if face_rect:
        fx, fy, fw, fh = face_rect
        cx = fx + fw / 2.0 + hoff
        neck_y = fy + fh * 1.02 + voff
        S = fw * 1.32 * scale
    else:
        cx = W / 2.0 + hoff
        neck_y = H * 0.58 + voff
        S = W * 0.30 * scale

    gf = _row_half_widths(alpha)
    top = ys.min()
    reg_end = min(int(cH * 0.45), cH - 1)
    if reg_end <= top + 2:
        reg_end = cH - 1
    shrow = top + int(np.argmax(gf[top + 2:reg_end]))
    gsh = gf[shrow]
    if gsh < 4 or S <= 0:
        return _lay_clothes_flat(rgb, cloth_rgba, state, face_rect)

    s = max(0.05, min(4.0, S / gsh))
    DW = max(8, int(round(cW * s)))
    DH = max(8, int(round(cH * s)))
    warped = cv2.resize(cloth_rgba, (DW, DH), interpolation=cv2.INTER_CUBIC)

    outA = np.zeros((DH, DW), dtype=np.float32)
    outRGB = np.zeros((DH, DW, 3), dtype=np.float32)
    n = 24
    band = DH // n
    ginfo = np.interp(np.linspace(0, cH - 1, n), np.arange(cH), gf) * s
    taper = 0.24
    for b in range(n):
        t = (b + 0.5) / n
        y0 = b * band
        y1 = DH if b == n - 1 else y0 + band
        ghw = max(2.0, ginfo[b])
        thw = S * (1 - taper * t)
        bx = min(1.9, max(0.55, thw / ghw))
        nw = max(4, int(round(DW * bx)))
        if nw > DW:
            nw = DW
        blk = warped[y0:y1]
        blk_r = cv2.resize(blk, (nw, y1 - y0), interpolation=cv2.INTER_LINEAR)
        x0b = (DW - nw) // 2
        seg_a = blk_r[:, :, 3].astype(np.float32) / 255.0
        seg_rgb = blk_r[:, :, :3].astype(np.float32)
        outA[y0:y1, x0b:x0b + nw] += seg_a
        outRGB[y0:y1, x0b:x0b + nw] += seg_rgb * seg_a[..., None]

    valid = np.maximum(outA, 1e-6)
    rgb_o = np.where(outA[..., None] > 0.001, outRGB / valid[..., None], 0.0)
    a_o = np.clip(outA, 0.0, 1.0)
    a_o = cv2.GaussianBlur(a_o, (0, 0), 0.8)
    overlay = np.dstack([np.clip(rgb_o, 0, 255).astype(np.uint8),
                         (a_o * 255).astype(np.uint8)])

    disp_top = neck_y - top * s
    x0p = int(round(cx - DW / 2.0))
    y0p = int(round(disp_top))
    mfeat = _torso_mask((H, W), cx, S, y0p, H)
    a = overlay[:, :, 3].astype(np.float32) / 255.0
    oy0c = max(0, y0p)
    oy1c = min(H, y0p + DH)
    ox0c = max(0, x0p)
    ox1c = min(W, x0p + DW)
    if ox1c > ox0c and oy1c > oy0c:
        ro = oy0c - y0p
        co = ox0c - x0p
        a_clip = a[ro:ro + (oy1c - oy0c), co:co + (ox1c - ox0c)]
        m_clip = mfeat[oy0c:oy1c, ox0c:ox1c]
        overlay[ro:ro + (oy1c - oy0c), co:co + (ox1c - ox0c), 3] = \
            (np.clip(a_clip * m_clip, 0, 1) * 255).astype(np.uint8)
    return _blend(rgb, overlay, x0p, y0p)


def _row_half_widths(alpha):
    """Per-row half width of the opaque region (pixels)."""
    h = alpha.shape[0]
    out = np.zeros(h, dtype=np.float32)
    for y in range(h):
        idx = np.where(alpha[y] > 0)[0]
        if len(idx):
            out[y] = (idx[-1] - idx[0]) / 2.0
    return out


def _torso_mask(shape, cx, S, y_top, H, down=0.985):
    """Feathered trapezoid mask that clips the garment to the torso."""
    hh, ww = shape
    y_t = max(0.0, min(float(y_top), H - 4))
    y_b = min(float(H), float(H) * down)
    if y_b <= y_t:
        y_b = y_t + 4
    hw_t = S * 1.06
    hw_b = S * 0.82
    m = np.zeros((hh, ww), dtype=np.uint8)
    cv2.fillConvexPoly(m, np.array([
        [cx - hw_t, y_t], [cx + hw_t, y_t],
        [cx + hw_b, y_b], [cx - hw_b, y_b]], dtype=np.int32), 255)
    return cv2.GaussianBlur(m, (0, 0), max(2.0, S * 0.03)).astype(np.float32) / 255.0


def _lay_clothes_flat(rgb, cloth_rgba, state, face_rect=None):
    """Simple transparent overlay (fallback if anything exotic fails)."""
    H, W = rgb.shape[:2]
    cH, cW = cloth_rgba.shape[:2]
    scale = float(state.get("scale", 100)) / 100.0
    voff = float(state.get("voff", 0)) * H / 1000.0
    hoff = float(state.get("hoff", 0)) * H / 1000.0
    nx, ny = 0.5 * cW, 0.078 * cH
    if face_rect:
        fx, fy, fw, fh = face_rect
        cx = fx + fw / 2.0 + hoff
        neck_y = fy + fh * 1.02 + voff
        target_w = fw * 3.0 * scale
    else:
        cx = W / 2.0 + hoff
        neck_y = H * 0.62 + voff
        target_w = W * 0.92 * scale
    s = target_w / cW
    import cv2
    dw = max(1, int(round(cW * s)))
    dh = max(1, int(round(cH * s)))
    resized = cv2.resize(cloth_rgba, (dw, dh), interpolation=cv2.INTER_AREA)
    x0 = int(round(cx - nx * s + hoff))
    y0 = int(round(neck_y - ny * s + voff))
    return _blend(rgb, resized, x0, y0)


def _blend(rgb, overlay, x0, y0):
    H, W = rgb.shape[:2]
    rh, rw = overlay.shape[:2]
    ox0, oy0 = max(0, x0), max(0, y0)
    ox1, oy1 = min(W, x0 + rw), min(H, y0 + rh)
    if ox1 <= ox0 or oy1 <= oy0:
        return rgb
    out = rgb.copy()
    sx, sy = ox0 - x0, oy0 - y0
    sub = overlay[sy:sy + (oy1 - oy0), sx:sx + (ox1 - ox0)]
    a = sub[:, :, 3:4].astype(np.float32) / 255.0
    src = sub[:, :, :3].astype(np.float32)
    out[oy0:oy1, ox0:ox1] = (a * src + (1 - a) * out[oy0:oy1, ox0:ox1]).astype(np.uint8)
    return out


def detect_face_rect(bgr):
    """Return (x, y, w, h) of the largest frontal face or None."""
    import cv2
    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    faces = cascade.detectMultiScale(gray, 1.1, 5, minSize=(40, 40))
    if len(faces) == 0:
        return None
    return tuple(max(faces, key=lambda r: r[2] * r[3]))


# --------------------------------------------------------------------------
# Garment generation (placeholder art, replaceable by real PNGs later)
# --------------------------------------------------------------------------

def _crm(pts, samples=16):
    """Closed Catmull-Rom curve sampling."""
    n = len(pts)
    P = pts + pts[:3]
    out = []
    for i in range(n):
        p0, p1, p2, p3 = P[i], P[i + 1], P[i + 2], P[i + 3]
        for j in range(samples):
            t = j / samples
            t2 = t * t
            t3 = t2 * t
            x = 0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * t +
                       (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 +
                       (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * t +
                       (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 +
                       (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            out.append((x, y))
    return out


_SIL = [
    (0.40, 0.075), (0.445, 0.115), (0.50, 0.125), (0.555, 0.115), (0.60, 0.075),
    (0.97, 0.165), (0.91, 0.32), (0.89, 0.60), (0.93, 1.00),
    (0.50, 1.02),
    (0.07, 1.00), (0.11, 0.60), (0.09, 0.32), (0.03, 0.165),
]


def _sil_pts(W, H):
    return _crm([(fx * W, fy * H) for fx, fy in _SIL], samples=18)


def _C(hexc, a=255):
    r, g, b = ImageColor.getrgb(hexc)
    return (r, g, b, a)


def _mix(c1, c2, f):
    return tuple(int(round(c1[i] + (c2[i] - c1[i]) * f)) for i in range(3))


def _folds(d, xs, y0, y1, rgba):
    for x in xs:
        d.line([(x * FW * SS, y0 * FH * SS), (x * FW * SS, y1 * FH * SS)],
               fill=rgba, width=int(2.2 * SS))


def _buttons(d, ys, base, dark):
    r = 2.6 * SS
    for yy in ys:
        d.ellipse([0.5 * FW * SS - r, yy * FH * SS - r,
                   0.5 * FW * SS + r, yy * FH * SS + r], fill=dark + (255,))


def _canvas():
    W, H = FW * SS, FH * SS
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))


def _suit(jacket, shirt, tie, accent):
    W, H = FW * SS, FH * SS
    img = _canvas()
    d = ImageDraw.Draw(img)
    d.polygon(_sil_pts(W, H), fill=_C(jacket))
    ny = 0.078 * H
    d.polygon([(0.455 * W, ny), (0.545 * W, ny), (0.50 * W, 0.30 * H)], fill=_C(shirt))
    d.polygon([(0.473 * W, 0.150 * H), (0.527 * W, 0.150 * H), (0.50 * W, 0.30 * H)], fill=_C(tie))
    d.ellipse([0.472 * W, 0.130 * H, 0.528 * W, 0.150 * H], fill=_C(tie))
    laps = [
        [(0.455 * W, ny * 1.001), (0.330 * W, 0.155 * H), (0.315 * W, 0.245 * H),
         (0.440 * W, 0.225 * H), (0.435 * W, 0.118 * H)],
        [(0.545 * W, ny * 1.001), (0.670 * W, 0.155 * H), (0.685 * W, 0.245 * H),
         (0.560 * W, 0.225 * H), (0.565 * W, 0.118 * H)],
    ]
    for lp in laps:
        d.polygon(lp, fill=_C(accent))
    d.polygon([(0.440 * W, 0.225 * H), (0.455 * W, 0.31 * H),
               (0.545 * W, 0.31 * H), (0.560 * W, 0.225 * H)], fill=(30, 30, 30, 60))
    _buttons(d, (0.52, 0.60), jacket, _mix(_C(jacket), (0, 0, 0), 0.3))
    _folds(d, [0.34, 0.66], 0.30, 0.97, (0, 0, 0, 45))
    d.line([(0.93 * W, 0.985 * H), (0.07 * W, 0.985 * H)],
           fill=_mix(_C(jacket), (0, 0, 0), 0.15) + (255,), width=int(2 * SS))
    return img


def _thobe(base):
    W, H = FW * SS, FH * SS
    img = _canvas()
    d = ImageDraw.Draw(img)
    d.polygon(_sil_pts(W, H), fill=_C(base))
    ny = 0.078 * H
    dark = _mix(_C(base), (0, 0, 0), 0.12)
    d.rectangle([0.44 * W, ny - 0.012 * H, 0.56 * W, ny + 0.012 * H], fill=dark + (255,))
    d.line([(0.5 * W, ny + 0.012 * H), (0.5 * W, 0.5 * H)], fill=(0, 0, 0, 60), width=int(1.5 * SS))
    d.line([(0.46 * W, ny - 0.012 * H), (0.54 * W, ny - 0.012 * H)],
           fill=_mix(_C(base), (255, 255, 255), 0.35) + (255,), width=int(1.2 * SS))
    _buttons(d, (0.22, 0.29, 0.36, 0.43), base, _mix(_C(base), (0, 0, 0), 0.25))
    _folds(d, [0.32, 0.68], 0.24, 0.97, (255, 255, 255, 40))
    return img


def _shirt(base):
    W, H = FW * SS, FH * SS
    img = _canvas()
    d = ImageDraw.Draw(img)
    d.polygon(_sil_pts(W, H), fill=_C(base))
    ny = 0.078 * H
    dc = _mix(_C(base), (0, 0, 0), 0.14)
    d.polygon([(0.445 * W, ny * 0.99), (0.50 * W, ny * 1.0), (0.472 * W, 0.062 * H)], fill=dc + (255,))
    d.polygon([(0.555 * W, ny * 0.99), (0.50 * W, ny * 1.0), (0.528 * W, 0.062 * H)], fill=dc + (255,))
    d.polygon([(0.45 * W, ny * 1.02), (0.55 * W, ny * 1.02), (0.50 * W, 0.115 * H)], fill=(0, 0, 0, 35))
    d.rectangle([0.49 * W, 0.10 * H, 0.51 * W, 0.5 * H], fill=(0, 0, 0, 40))
    _buttons(d, (0.15, 0.21, 0.27, 0.33, 0.39), base, _mix(_C(base), (0, 0, 0), 0.28))
    _folds(d, [0.30, 0.70], 0.20, 0.50, (0, 0, 0, 35))
    return img


def _bisht(outer, trim):
    W, H = FW * SS, FH * SS
    pts = _sil_pts(W, H)
    inner = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(inner).polygon(pts, fill=_C('#efe7d8'))
    outer_img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(outer_img).polygon(pts, fill=_C(outer))
    cut = Image.new("L", (W, H), 255)
    ImageDraw.Draw(cut).polygon([(0.465 * W, 0.075 * H), (0.535 * W, 0.075 * H),
                                 (0.50 * W, 0.47 * H)], 0)
    img = Image.composite(outer_img, inner, cut)
    d = ImageDraw.Draw(img)
    ny = 0.075 * H
    d.polygon([(0.465 * W, ny), (0.50 * W, ny - 0.008 * H), (0.535 * W, ny),
               (0.50 * W, ny + 0.012 * H)], fill=_C(trim))
    gold = (196, 158, 74, 255)
    d.line([(0.465 * W, ny), (0.50 * W, 0.47 * H)], fill=gold, width=int(1.8 * SS))
    d.line([(0.535 * W, ny), (0.50 * W, 0.47 * H)], fill=gold, width=int(1.8 * SS))
    d.line([(0.50 * W, ny + 0.012 * H), (0.50 * W, 0.47 * H)], fill=gold, width=int(0.8 * SS))
    d.line([(0.92 * W, 0.985 * H), (0.08 * W, 0.985 * H)], fill=gold, width=int(1.6 * SS))
    return img


def _abaya():
    W, H = FW * SS, FH * SS
    img = _canvas()
    d = ImageDraw.Draw(img)
    d.polygon(_sil_pts(W, H), fill=_C('#1b1b20'))
    ny = 0.078 * H
    d.polygon([(0.46 * W, ny), (0.54 * W, ny), (0.50 * W, 0.10 * H)], fill=(0, 0, 0, 80))
    d.arc([0.46 * W, ny - 0.01 * H, 0.54 * W, 0.10 * H], 180, 360,
          fill=_C('#4a4a52'), width=int(1.6 * SS))
    _folds(d, [0.35, 0.50, 0.65], 0.16, 0.98, (70, 70, 80, 55))
    d.line([(0.92 * W, 0.99 * H), (0.08 * W, 0.99 * H)], fill=(80, 80, 90, 255), width=int(1.2 * SS))
    return img


_MAKERS = {
    'suit_navy': lambda: _suit('#1f3a5f', '#e8e4da', '#b3282d', '#2a4a78'),
    'suit_black': lambda: _suit('#22252a', '#f0ece2', '#7a1f24', '#2f3238'),
    'suit_gray': lambda: _suit('#6b6f76', '#efece4', '#8c2026', '#7f848c'),
    'shirt_white': lambda: _shirt('#f5f2ea'),
    'thobe_white': lambda: _thobe('#f4f1ea'),
    'thobe_beige': lambda: _thobe('#dbcbb0'),
    'bisht_brown': lambda: _bisht('#6b4a2b', '#2f2318'),
    'abaya_black': lambda: _abaya(),
}


def _make_garment(label):
    fn = _MAKERS.get(label)
    if fn is None:
        return None
    img = fn().resize((FW, FH), Image.LANCZOS)
    return np.ascontiguousarray(np.array(img))