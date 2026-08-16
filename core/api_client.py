import requests
import base64
import json
import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

_SERVER_URL = "http://localhost:5000"
_token = None
_token_id = None  # last known tid from JWT, sent as logout_token_id on next login
_username = None
_session_expired_callback = None


def set_server_url(url):
    global _SERVER_URL
    _SERVER_URL = url.rstrip("/")


def get_server_url():
    return _SERVER_URL


def _decode_token_id(token):
    """Extract tid claim from a JWT without verifying signature."""
    if not token:
        return None
    try:
        payload_b64 = token.split(".")[1]
        pad = 4 - len(payload_b64) % 4
        if pad != 4:
            payload_b64 += "=" * pad
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("tid")
    except Exception:
        return None


def set_token(token):
    global _token
    _token = token


def _update_token_id(token):
    global _token_id
    tid = _decode_token_id(token)
    if tid:
        _token_id = tid


def get_token():
    return _token


def set_username(username):
    global _username
    _username = username


def get_token_id():
    return _token_id


def set_token_id(tid):
    global _token_id
    _token_id = tid


def set_session_expired_callback(cb):
    global _session_expired_callback
    _session_expired_callback = cb


def get_username():
    return _username


def _headers():
    h = {"Content-Type": "application/json"}
    if _token:
        h["Authorization"] = f"Bearer {_token}"
    return h


def _request(method, path, **kwargs):
    url = f"{_SERVER_URL}{path}"
    fire_session_expired = kwargs.pop("_fire_session_expired", True)
    try:
        resp = requests.request(method, url, headers=_headers(), timeout=10, **kwargs)
        body = resp.text or ""
        try:
            payload = resp.json()
        except Exception:
            payload = None
        if resp.status_code >= 400:
            if isinstance(payload, dict):
                err_msg = payload.get("error", f"خطأ في الخادم ({resp.status_code})")
                if payload.get("session_expired") and fire_session_expired:
                    set_token(None)
                    set_username(None)
                    if _session_expired_callback:
                        _session_expired_callback()
                    return None, err_msg
                logger.error("API error %s %s: %s", method, path, err_msg)
                return None, err_msg
            logger.error("API error %s %s: status %s", method, path, resp.status_code)
            return None, _server_error_message(resp.status_code)
        if payload is not None:
            return payload, None
        return None, _server_error_message(resp.status_code)
    except requests.ConnectionError:
        logger.error("Cannot connect to server at %s", _SERVER_URL)
        return None, "لا يمكن الاتصال بالخادم"
    except Exception as e:
        logger.error("API request failed: %s", e)
        return None, str(e)


def _server_error_message(status):
    if status == 404:
        return "الخادم لا يتعرف على هذه العملية. تأكد من تحديث الخادم لأحدث إصدار ثم أعد المحاولة"
    if status == 405:
        return "الخادم لا يقبل هذه العملية. تأكد من تحديث الخادم لأحدث إصدار"
    if status == 500:
        return "حدث خطأ في الخادم (500). حاول مجدداً أو تواصل مع المالك"
    if status == 502 or status == 503 or status == 504:
        return "الخادم غير متاح حالياً. حاول بعد قليل"
    return f"خطأ في الخادم ({status})"


def _login_raw(username, password, **extra):
    payload = {"username": username, "password": password, **extra}
    if _token_id:
        payload["logout_token_id"] = _token_id
    data, err = _request("POST", "/api/auth/login", json=payload, _fire_session_expired=False)
    if data:
        set_token(data["token"])
        _update_token_id(data["token"])
        set_username(data["username"])
    return data, err


def login(username, password, force_login=False):
    extra = {"force_login": True} if force_login else {}
    return _login_raw(username, password, **extra)


def login_check_force(username, password):
    data, err = _login_raw(username, password)
    if err and "أجهزة حالياً" in err:
        return data, err, True
    return data, err, False


def register(username, password, shop_name, phone):
    data, err = _request("POST", "/api/auth/register", json={
        "username": username, "password": password,
        "shop_name": shop_name, "phone": phone,
    })
    if data:
        set_token(data["token"])
        _update_token_id(data["token"])
        set_username(data["username"])
    return data, err


def check_auth():
    return _request("GET", "/api/auth/check")


def get_users():
    return _request("GET", "/api/users")


def get_subscriptions(username):
    return _request("GET", f"/api/subscriptions/{username}")


def set_subscription(username, days):
    return _request("POST", "/api/subscriptions/set", json={
        "username": username, "days": days,
    })


def delete_user(username):
    return _request("DELETE", f"/api/users/{username}")


def change_password(new_password):
    return _request("PUT", "/api/users/password", json={
        "new_password": new_password,
    })


def reset_password(username, new_password):
    return _request("POST", "/api/users/reset-password", json={
        "username": username, "new_password": new_password,
    })


def update_profile(shop_name, phone):
    return _request("PUT", "/api/users/profile", json={
        "shop_name": shop_name, "phone": phone,
    })


def upload_pixmap(pixmap_bytes):
    b64 = base64.b64encode(pixmap_bytes).decode() if pixmap_bytes else ""
    return _request("POST", "/api/users/pixmap", json={"pixmap": b64})


def get_banners():
    return _request("GET", "/api/banners")


def set_banner(side, pixmap_bytes, link=""):
    b64 = base64.b64encode(pixmap_bytes).decode() if pixmap_bytes else ""
    return _request("POST", f"/api/banners/{side}", json={
        "pixmap": b64, "link": link,
    })


def delete_banner(side):
    return _request("DELETE", f"/api/banners/{side}")


def clear_pending():
    return _request("POST", "/api/pending/clear")


def logout():
    return _request("POST", "/api/auth/logout")



def get_user_sessions(username):
    return _request("GET", f"/api/users/{username}/sessions")


def set_max_devices(username, max_devices):
    return _request("POST", f"/api/users/{username}/max-devices", json={
        "max_devices": max_devices,
    })


def get_settings():
    return _request("GET", "/api/settings")


def set_subscription_required(enabled):
    return _request("POST", "/api/settings/subscription-required", json={
        "enabled": bool(enabled),
    })


def get_notifications():
    return _request("GET", "/api/notifications")


def create_notification(ntype, text, link_url="", link_label="", question=""):
    return _request("POST", "/api/notifications", json={
        "type": ntype, "text": text, "link_url": link_url,
        "link_label": link_label, "question": question,
    })


def mark_notification_read(notification_id=None, mark_all=False):
    return _request("POST", "/api/notifications/read", json={
        "notification_id": notification_id, "all": mark_all,
    })


def reply_notification(notification_id, reply_text):
    return _request("POST", f"/api/notifications/{notification_id}/reply", json={
        "reply_text": reply_text,
    })


def get_notification_replies():
    return _request("GET", "/api/notifications/replies")


def delete_notification_reply(reply_id):
    return _request("DELETE", f"/api/notifications/replies/{reply_id}")


def delete_notification(notification_id):
    return _request("DELETE", f"/api/notifications/{notification_id}")


def get_user_details(username):
    return _request("GET", f"/api/users/{username}")
