import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "notifier_config.json"
SENDER_DEFAULT = "96478065402819"

def _load_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("فشل تحميل إعدادات الإشعارات: %s", e)
    return {}

def _save_config(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

def is_configured():
    cfg = _load_config()
    return bool(cfg.get("whatsapp_token") and cfg.get("whatsapp_phone_id"))

def send_verification_code(phone, code):
    cfg = _load_config()
    token = cfg.get("whatsapp_token", "")
    phone_id = cfg.get("whatsapp_phone_id", "")
    if token and phone_id:
        return _send_via_whatsapp_cloud(phone, code, token, phone_id)
    return False, "لم يتم إعداد واتساب Cloud API. اذهب إلى لوحة التحكم ← ⚙️ إعدادات واتساب"

def _normalize_phone(phone):
    phone = phone.replace(" ", "").lstrip("+")
    if phone.startswith("0"):
        phone = "964" + phone[1:]
    return phone

def _send_via_whatsapp_cloud(phone, code, token, phone_id):
    import requests
    phone = _normalize_phone(phone)
    url = f"https://graph.facebook.com/v18.0/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": f"رمز التحقق الخاص بك: {code}\n\nورشة طباعة"}
    }
    try:
        r = requests.post(url, json=body, headers=headers, timeout=15)
        resp = r.json()
        if r.status_code in (200, 201):
            logger.info("تم إرسال رمز التحقق عبر WhatsApp Cloud إلى %s", phone)
            return True, None
        err_detail = resp.get("error", {}).get("message", r.text[:300])
        logger.warning("WhatsApp Cloud فشل: %s - %s", r.status_code, err_detail)
        return False, f"خطأ {r.status_code} من ميتا: {err_detail}"
    except Exception as e:
        logger.warning("WhatsApp Cloud استثناء: %s", e)
        return False, f"تعذر الاتصال بميتا: {e}"

def configure(method, **kwargs):
    cfg = _load_config()
    cfg["method"] = method
    cfg.update(kwargs)
    _save_config(cfg)
    logger.info("تم حفظ إعدادات الإشعارات: %s", method)
