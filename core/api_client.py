import requests
import base64
import json
import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

_SERVER_URL = "http://localhost:5000"
_token = None
_username = None
_session_expired_callback = None


def set_server_url(url):
    global _SERVER_URL
    _SERVER_URL = url.rstrip("/")


def get_server_url():
    return _SERVER_URL


def set_token(token):
    global _token
    _token = token


def get_token():
    return _token


def set_username(username):
    global _username
    _username = username


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
        if resp.status_code >= 400:
            err = resp.json()
            err_msg = err.get("error", "خطأ في الاتصال")
            if err.get("session_expired") and fire_session_expired:
                set_token(None)
                set_username(None)
                if _session_expired_callback:
                    _session_expired_callback()
                return None, err_msg
            logger.error("API error %s %s: %s", method, path, err_msg)
            return None, err_msg
        return resp.json(), None
    except requests.ConnectionError:
        logger.error("Cannot connect to server at %s", _SERVER_URL)
        return None, "لا يمكن الاتصال بالخادم"
    except Exception as e:
        logger.error("API request failed: %s", e)
        return None, str(e)


def _login_raw(username, password, **extra):
    payload = {"username": username, "password": password, **extra}
    data, err = _request("POST", "/api/auth/login", json=payload, _fire_session_expired=False)
    if data:
        set_token(data["token"])
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


def check_version():
    return _request("GET", "/api/app/version")


def get_user_sessions(username):
    return _request("GET", f"/api/users/{username}/sessions")


def set_max_devices(username, max_devices):
    return _request("POST", f"/api/users/{username}/max-devices", json={
        "max_devices": max_devices,
    })
