"""إلباس افتراضي مجاني عبر Hugging Face Gradio Spaces (بدون إنترنت مسبق الدفع).

يعتمد على يكاتب gradio_client للاتصال بصفحات Spaces العامة التي تقدم نموذج
Virtual Try-On (IDM-VTON / OOTDiffusion / Kolors). يُحاول عدة Spaces بديلاً
تلقائياً عند تعذر أحدها، مع إعادة محاولة عند جرعات (الـ Spaces المجانية تنام).

الإعدادات تُحفظ تحت مفتاح "tryon_free" في data/app_config.json.
"""

from __future__ import annotations

import os
import time
import json

from PIL import Image

try:
    from gradio_client import Client, handle_file
    _CLIENT_AVAILABLE = True
except Exception:  # pragma: no cover
    _CLIENT_AVAILABLE = False

from core.runtime import data_dir as _runtime_data_dir

CONFIG_PATH = os.path.join(_runtime_data_dir(), "app_config.json")

DEFAULT_SPACES = [
    "Kwai-Kolors/Kolors-Virtual-Try-On",
    "yisol/IDM-VTON",
    "Nymbo/Virtual-Try-On",
    "Oshuboi/virtual-try-on",
]

_GARMENT_PROMPT_HINT = {
    "suit": "a tailored suit",
    "thobe": "a traditional thobe",
    "abaya": "an abaya",
    "shirt": "a shirt",
    "coat": "a coat",
    "bisht": "a bisht",
}


def _load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("tryon_free", {}) if cfg else {}
    except Exception:
        return {}


def _save_config(cfg: dict) -> None:
    try:
        full = {}
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            full = json.load(f)
        full = full or {}
        full["tryon_free"] = cfg
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(full, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_settings() -> dict:
    return _load_config()


def get_space() -> str:
    return _load_config().get("space") or DEFAULT_SPACES[0]


def set_space(name: str) -> None:
    cfg = _load_config()
    cfg["space"] = name
    _save_config(cfg)


def get_max_retries() -> int:
    return int(_load_config().get("max_retries") or 2)


def add_space_custom(name: str) -> None:
    cfg = _load_config()
    names = cfg.get("spaces") or list(DEFAULT_SPACES)
    if name and name not in names:
        names.append(name)
    cfg["spaces"] = names
    _save_config(cfg)


def list_spaces() -> list:
    cfg = _load_config()
    names = cfg.get("spaces") or list(DEFAULT_SPACES)
    if not names:
        names = list(DEFAULT_SPACES)
    return names


def _deduce_garment_hint(path: str) -> str:
    low = os.path.basename(path).lower()
    for key, prompt in _GARMENT_PROMPT_HINT.items():
        if key in low:
            return prompt
    return "the garment in the image"


def try_on_free(person_pil: Image.Image, garment_pil: Image.Image,
                garment_path: str | None = None,
                progress=None, timeout: int = 180) -> Image.Image:
    """يلبس الملابس عبر أحد Spaces المجانية ويعيد صورة result.

    يجرّب Space بعد Space (الإعداد الرئيسي ثم البدائل) مع إعادة محاولة
    عند جرعات/نوم، ويرفع تقارير تقدم عبر callback progress(نص).
    """
    if not _CLIENT_AVAILABLE:
        raise RuntimeError("gradio_client غير مثبتة — نفّذ: pip install gradio_client")

    # خزّن مؤقتاً
    tmp = _runtime_data_dir()
    os.makedirs(tmp, exist_ok=True)
    person_path = os.path.join(tmp, "_tryon_person.png")
    garment_file = os.path.join(tmp, "_tryon_garm.png")
    person_pil.convert("RGB").save(person_path, "PNG")
    garment_pil.convert("RGB").save(garment_file, "PNG")

    hint = _deduce_garment_hint(garment_path or garment_file)

    spaces = list_spaces()
    preferred = get_space()
    # ضع المفضل أولاً
    if preferred in spaces:
        spaces.remove(preferred)
        spaces.insert(0, preferred)

    last_err = None
    for space in spaces:
        attempts = max(get_max_retries(), 0) + 1
        for attempt in range(attempts):
            if progress:
                progress(f"الاتصال بـ {space} (محاولة {attempt + 1}/{attempts})…")
            try:
                image = _call_idm_vton(space, person_path, garment_file, hint, timeout)
                if image is not None:
                    return image
                raise RuntimeError("لم تُرجع الخدمة صورة")
            except Exception as e:
                last_err = e
                if progress:
                    progress(f"{space} غير متاح: {str(e)[:80]}")
                time.sleep(1)

    raise RuntimeError(f"فشلت جميع خدمات الإلباس المجانية: {last_err}")


def _call_idm_vton(space: str, person_path: str, garment_path: str,
                   hint: str, timeout: int) -> Image.Image:
    """استدعاء Space من نوع IDM-VTON معالجة للصيغ المتنوعة."""
    try:
        client = Client(space, verbose=False)
    except Exception as e:
        raise RuntimeError(f"تعذّر الاتصال بـ {space}: {str(e)[:120]}")

    # تأكد من وجود نقطة صالحة (is_valid) وليست 'private' — وإلا نفشل بشكل واضح
    valid_endpoints = []
    try:
        eps = client.endpoints
        for idx, ep in eps.items():
            an = ep.api_name
            if an and an not in (False, "/load_example", "/load_example_1", "/load_example_2"):
                if getattr(ep, "is_valid", True):
                    valid_endpoints.append((idx, an))
    except Exception:
        pass
    if not valid_endpoints:
        raise RuntimeError(
            f"الخدمة {space} لا تعرض نقطة إلباس صالحة حالياً "
            "(نائم أو معطّل) — جرّب لاحقاً أو اختر خدمة أخرى من «🎨 مجاني».")

    # اختر النقطة الصالحة الأولى
    fn_index, api_name = valid_endpoints[0]

    person = os.path.abspath(person_path)
    garment = os.path.abspath(garment_path)
    editor = dict(background=person, layers=[person], composite=person)

    result = None
    # أسلوب IDM-VTON مع ImageEditor (أولوية)
    try:
        result = client.predict(
            editor,
            garment,
            hint,
            True, True, 30, 42,
            api_name=api_name, fn_index=fn_index,
        )
    except Exception:
        # أسلوب Kolors: صور متجهة كمسار، seed، randomize
        try:
            result = client.predict(
                person,
                garment,
                0, False,
                api_name=api_name, fn_index=fn_index,
            )
        except Exception:
            # أسلوب بسيط: filepath مباشرة
            result = client.predict(
                handle_file(person),
                handle_file(garment),
                hint,
                True, True, 30, 42,
                api_name=api_name, fn_index=fn_index,
            )

    out = None
    if isinstance(result, (list, tuple)) and result:
        out = result[0]
    elif isinstance(result, dict):
        out = result.get("output") or result.get("image")
    elif isinstance(result, str):
        out = result
    if not out:
        raise RuntimeError("استجابة فارغة")

    if not os.path.isfile(str(out)):
        raise RuntimeError("مسار النتيجة غير صالح")
    return Image.open(str(out)).convert("RGB")
