import base64
import io
import json
import logging
import os
import sys
import time

import requests
from PIL import Image

logger = logging.getLogger(__name__)

# FastReplica-توفر Replicate نماذج Virtual Try-On جاهزة تُدفع لكل صورة.
DEFAULT_MODEL = "cuuupid/idm-vton"
DEFAULT_VERSION = (
    "3b032a70c29aef7b9c3222f2e40b71660201d8c288336475ba326f3ca278a3e1")


class AiTryonError(Exception):
    pass


def _data_dir():
    if getattr(sys, "frozen", False):
        return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), "data")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def _config_path():
    return os.path.join(_data_dir(), "app_config.json")


def load_config():
    try:
        with open(_config_path(), encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    cfg.setdefault("ai_tryon", {})
    return cfg


def save_config(cfg):
    os.makedirs(_data_dir(), exist_ok=True)
    with open(_config_path(), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def get_settings():
    return load_config().get("ai_tryon", {})


def has_key():
    return bool((get_settings().get("api_key") or "").strip())


def set_key(key):
    cfg = load_config()
    cfg["ai_tryon"]["api_key"] = (key or "").strip()
    save_config(cfg)


def _to_data_uri(png_bytes):
    return "data:application/octet-stream;base64," + base64.b64encode(png_bytes).decode()


def _to_png(pil_img, max_side=768):
    im = pil_img.copy()
    im.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def try_on(human_pil, garment_pil, category="upper_body",
           progress=None, cancel=None, timeout=240):
    """Send a portrait + a garment to a cloud try-on model and return the photo.

    human_pil / garment_pil: PIL images.
    Returns a PIL.Image (RGB) dressed in the garment.
    """
    s = get_settings()
    key = (s.get("api_key") or "").strip()
    if not key:
        raise AiTryonError(
            "لا يوجد مفتاح API للذكاء الاصطناعي. أدخل المفتاح أولًا.")

    version = (s.get("version") or DEFAULT_VERSION).strip()
    desc = (s.get("garment_des") or "").strip() or \
        "formal upper body garment, suit jacket style, front view"
    steps = max(1, min(40, int(s.get("steps") or 30)))
    seed = int(s.get("seed") or 42)
    crop = bool(s.get("crop", True))

    def _prog(msg):
        if progress:
            progress(msg)

    _prog("تجهيز الصور وإرسالها إلى الخادم…")
    payload = {
        "human_img": _to_data_uri(_to_png(human_pil)),
        "garm_img": _to_data_uri(_to_png(garment_pil)),
        "garment_des": desc,
        "category": category,
        "crop": crop,
        "steps": steps,
        "seed": seed,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    url = "https://api.replicate.com/v1/predictions"
    try:
        resp = requests.post(url, json={"version": version, "input": payload},
                             headers=headers, timeout=90)
    except requests.RequestException as e:
        raise AiTryonError(f"تعذر الاتصال بخادم الذكاء الاصطناعي: {e}")

    if resp.status_code == 401:
        raise AiTryonError("المفتاح غير صحيح (401) — راجع مفتاح API.")
    if resp.status_code == 403:
        raise AiTryonError("الوصول مرفوض (403) — قد يحتاج النموذج اشتراكًا مدفوعًا.")
    if resp.status_code == 429:
        raise AiTryonError("الحد اليومي للطلبات على الخدمة انتهى (429).")
    if resp.status_code not in (200, 201):
        raise AiTryonError(f"فشل بدء المعالجة ({resp.status_code}): {resp.text[:300]}")

    data = resp.json()
    pred_id = data.get("id")
    get_url = (data.get("urls") or {}).get("get")
    if not pred_id or not get_url:
        raise AiTryonError("الخادم لم يُرجع رابط متابعة المعالجة.")

    cancel_url = f"https://api.replicate.com/v1/predictions/{pred_id}/cancel"
    t0 = time.time()
    _prog("المعالجة تعمل على الخادم (قد تستغرق دقيقة)…")
    while True:
        if cancel and cancel():
            try:
                requests.post(cancel_url, headers=headers, timeout=30)
            except Exception:
                pass
            raise AiTryonError("أُلغيت المعالجة.")
        try:
            r2 = requests.get(get_url, headers=headers, timeout=90)
        except requests.RequestException as e:
            raise AiTryonError(f"تعذر متابعة المعالجة: {e}")
        d = r2.json()
        st = d.get("status")
        if st == "succeeded":
            outs = d.get("output")
            out_url = outs[0] if isinstance(outs, list) else outs
            _prog("استلام النتيجة…")
            try:
                rr = requests.get(out_url, stream=True, timeout=180)
                img = Image.open(rr.raw)
            except Exception as e:
                raise AiTryonError(f"تعذر تحميل النتيجة: {e}")
            return img.convert("RGB") if img.mode != "RGB" else img
        if st in ("failed", "canceled"):
            err = d.get("error")
            msg = f"فشلت المعالجة على الخادم: {err}" if err else "فشلت المعالجة على الخادم."
            raise AiTryonError(msg)
        if time.time() - t0 > timeout:
            raise AiTryonError("انتهت مهلة الانتظار — جرّب مرة أخرى لاحقًا.")
        _prog("المعالجة تعمل على الخادم…")
        time.sleep(3)