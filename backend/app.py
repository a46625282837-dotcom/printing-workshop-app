import logging
import base64
import uuid
from datetime import date, timedelta
from flask import Flask, request, jsonify, send_from_directory
import os
from flask_jwt_extended import (
    JWTManager, create_access_token, jwt_required, get_jwt_identity,
    get_jwt,
)
from . import config
from .database import (
    init_db, get_user, get_all_users, create_user, update_password,
    update_profile, delete_user, verify_password,
    get_subscriptions, add_subscription, compute_remaining_days,
    get_pending_messages, add_pending_message, clear_pending,
    save_profile_pixmap, get_profile_pixmap,
    save_banner_pixmap, get_banner_pixmaps, delete_banner_pixmap,
    update_token_id,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["JWT_SECRET_KEY"] = config.JWT_SECRET
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=config.JWT_EXPIRY_HOURS)
jwt = JWTManager(app)


@jwt.token_in_blocklist_loader
def _session_check(jwt_header, jwt_payload):
    username = jwt_payload.get("sub")
    token_id = jwt_payload.get("tid")
    if not username or not token_id:
        return True
    user = get_user(username)
    if not user:
        return True
    return user.get("token_id", "") != token_id


@jwt.revoked_token_loader
def _session_expired(jwt_header, jwt_payload):
    return jsonify({"error": "الحساب شغال في لابتوب آخر", "session_expired": True}), 401


init_db()


_DOWNLOAD_URL = os.environ.get("DOWNLOAD_URL", "https://www.mediafire.com/file/ja23567ua050tp8/%D9%88%D8%B1%D8%B4%D8%A9+%D8%B7%D8%A8%D8%A7%D8%B9%D8%A9.exe/file")


@app.route("/")
def landing():
    return f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ورشة طباعة</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI',sans-serif;background:linear-gradient(135deg,#1a73e8,#0d47a1);min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}}
.card{{background:#fff;border-radius:20px;padding:50px;max-width:500px;width:100%;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,.3)}}
h1{{color:#1a73e8;font-size:32px;margin-bottom:10px}}
p{{color:#555;font-size:16px;line-height:1.8;margin-bottom:25px}}
.btn{{display:inline-block;background:#1a73e8;color:#fff;padding:16px 40px;border-radius:50px;font-size:18px;text-decoration:none;transition:.3s}}
.btn:hover{{background:#0d47a1;transform:translateY(-2px)}}
.steps{{text-align:right;background:#f5f5f5;border-radius:12px;padding:20px;margin:25px 0;font-size:14px;color:#444}}
.steps li{{margin-bottom:8px}}
.footer{{color:#999;font-size:13px;margin-top:20px}}
</style>
</head>
<body>
<div class="card">
<h1>🖨️ ورشة طباعة</h1>
<p>برنامج تصميم وطباعة البطاقات الشخصية<br>بأعلى جودة واحترافية</p>
<div class="steps">
<strong>طريقة التحميل:</strong>
<ol>
<li>حمل الملف من الرابط أدناه</li>
<li>فك الضغط عن الملف</li>
<li>شغّل <strong>ورشة طباعة.exe</strong></li>
<li>سجل حساب جديد وابدأ الاستخدام</li>
</ol>
</div>
<a class="btn" href="{_DOWNLOAD_URL}">📥 تحميل التطبيق</a>
<p class="footer">للاشتراك والتواصل: <strong>07865402819</strong> واتساب</p>
</div>
</body>
</html>"""


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    if not username or not password:
        return jsonify({"error": "اسم المستخدم وكلمة المرور مطلوبان"}), 400
    if not verify_password(username, password):
        return jsonify({"error": "اسم المستخدم أو كلمة المرور غير صحيحة"}), 401
    user = get_user(username)
    token_id = str(uuid.uuid4())
    update_token_id(username, token_id)
    token = create_access_token(identity=username, additional_claims={"tid": token_id})
    return jsonify({
        "token": token,
        "username": username,
        "shop_name": user.get("shop_name", ""),
        "is_admin": bool(user.get("is_admin")),
        "reg_date": user.get("reg_date", ""),
    })


@app.route("/api/auth/register", methods=["POST"])
def api_register():
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    shop_name = data.get("shop_name", "").strip()
    phone = data.get("phone", "").strip()
    if not username or not password:
        return jsonify({"error": "جميع الحقول مطلوبة"}), 400
    if not username.isascii() or not username.replace("_", "").isalnum() or username[0].isdigit():
        return jsonify({"error": "الاسم يجب أن يبدأ بحرف ويحتوي على أحرف إنجليزية وأرقام فقط"}), 400
    if username.lower() == "ahmed":
        return jsonify({"error": "لا يمكن استخدام هذا الاسم"}), 400
    if create_user(username, password, shop_name, phone):
        token_id = str(uuid.uuid4())
        update_token_id(username, token_id)
        token = create_access_token(identity=username, additional_claims={"tid": token_id})
        return jsonify({"token": token, "username": username}), 201
    return jsonify({"error": "اسم المستخدم موجود مسبقاً"}), 409


@app.route("/api/auth/check", methods=["GET"])
@jwt_required()
def api_check():
    username = get_jwt_identity()
    user = get_user(username)
    if not user:
        return jsonify({"error": "المستخدم غير موجود"}), 404
    pending = get_pending_messages(username)
    remaining = compute_remaining_days(username) if not user.get("is_admin") else 0
    pixmap_bytes = get_profile_pixmap(username)
    pixmap_b64 = base64.b64encode(pixmap_bytes).decode() if pixmap_bytes else None
    return jsonify({
        "username": username,
        "shop_name": user.get("shop_name", ""),
        "phone": user.get("phone", ""),
        "reg_date": user.get("reg_date", ""),
        "is_admin": bool(user.get("is_admin")),
        "remaining_days": remaining,
        "pending_messages": pending,
        "profile_pixmap": pixmap_b64,
    })


@app.route("/api/users", methods=["GET"])
@jwt_required()
def api_get_users():
    if get_user(get_jwt_identity()).get("is_admin") != 1:
        return jsonify({"error": "صلاحية مطلوبة"}), 403
    users = get_all_users()
    result = []
    for u in users:
        if u["username"] == "ahmed":
            continue
        remaining = compute_remaining_days(u["username"])
        subs = get_subscriptions(u["username"])
        latest = subs[-1]["start_date"] if subs else None
        result.append({
            "username": u["username"],
            "shop_name": u["shop_name"],
            "phone": u["phone"],
            "reg_date": u["reg_date"],
            "remaining_days": remaining,
            "latest_sub_start": latest,
        })
    return jsonify(result)


@app.route("/api/subscriptions/<username>", methods=["GET"])
@jwt_required()
def api_get_subscriptions(username):
    current = get_jwt_identity()
    user = get_user(current)
    if current != username and (not user or user.get("is_admin") != 1):
        return jsonify({"error": "صلاحية مطلوبة"}), 403
    subs = get_subscriptions(username)
    remaining = compute_remaining_days(username)
    return jsonify({"subscriptions": subs, "remaining_days": remaining})


@app.route("/api/subscriptions/add", methods=["POST"])
@jwt_required()
def api_add_subscription():
    if get_user(get_jwt_identity()).get("is_admin") != 1:
        return jsonify({"error": "صلاحية مطلوبة"}), 403
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    days = int(data.get("days", 0))
    if not username or days <= 0:
        return jsonify({"error": "بيانات غير صالحة"}), 400
    today = date.today()
    start = today.isoformat()
    end = (today + timedelta(days=days)).isoformat()
    add_subscription(username, start, end, days)
    add_pending_message(username, f"تم زيادة اشتراكك {days} يوم من {start} إلى {end}")
    remaining = compute_remaining_days(username)
    return jsonify({"message": f"تمت إضافة {days} أيام", "remaining_days": remaining})


@app.route("/api/users/<username>", methods=["DELETE"])
@jwt_required()
def api_delete_user(username):
    if get_user(get_jwt_identity()).get("is_admin") != 1:
        return jsonify({"error": "صلاحية مطلوبة"}), 403
    if username == "ahmed":
        return jsonify({"error": "لا يمكن حذف المالك"}), 400
    delete_user(username)
    return jsonify({"message": "تم حذف المستخدم"})


@app.route("/api/users/password", methods=["PUT"])
@jwt_required()
def api_change_password():
    data = request.get_json() or {}
    username = get_jwt_identity()
    new_pw = data.get("new_password", "").strip()
    if len(new_pw) < 8:
        return jsonify({"error": "الرقم السري يجب أن لا يقل عن 8 أحرف"}), 400
    import re
    if not re.search(r'[a-zA-Z]', new_pw) or not re.search(r'[0-9]', new_pw):
        return jsonify({"error": "الرقم السري يجب أن يحتوي على حروف وأرقام"}), 400
    update_password(username, new_pw)
    return jsonify({"message": "تم تغيير الرقم السري"})


@app.route("/api/users/reset-password", methods=["POST"])
@jwt_required()
def api_reset_password():
    if get_user(get_jwt_identity()).get("is_admin") != 1:
        return jsonify({"error": "صلاحية مطلوبة"}), 403
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    new_pw = data.get("new_password", "").strip()
    if not username or len(new_pw) < 8:
        return jsonify({"error": "بيانات غير صالحة"}), 400
    import re
    if not re.search(r'[a-zA-Z]', new_pw) or not re.search(r'[0-9]', new_pw):
        return jsonify({"error": "الرقم السري يجب أن يحتوي على حروف وأرقام"}), 400
    update_password(username, new_pw)
    return jsonify({"message": "تم استعادة الرقم السري"})


@app.route("/api/users/profile", methods=["PUT"])
@jwt_required()
def api_update_profile():
    data = request.get_json() or {}
    username = get_jwt_identity()
    shop_name = data.get("shop_name", "").strip()
    phone = data.get("phone", "").strip()
    update_profile(username, shop_name, phone)
    return jsonify({"message": "تم حفظ التغييرات"})


@app.route("/api/users/pixmap", methods=["POST"])
@jwt_required()
def api_upload_pixmap():
    data = request.get_json() or {}
    username = get_jwt_identity()
    pixmap_b64 = data.get("pixmap", "")
    if pixmap_b64:
        pixmap_bytes = base64.b64decode(pixmap_b64)
        save_profile_pixmap(username, pixmap_bytes)
    else:
        save_profile_pixmap(username, None)
    return jsonify({"message": "تم تحديث الصورة"})


@app.route("/api/banners", methods=["GET"])
@jwt_required()
def api_get_banners():
    banners = get_banner_pixmaps()
    result = {}
    for side, info in banners.items():
        result[side] = {
            "link": info["link"],
            "pixmap": base64.b64encode(info["pixmap"]).decode() if info["pixmap"] else None,
        }
    return jsonify(result)


@app.route("/api/banners/<side>", methods=["POST"])
@jwt_required()
def api_set_banner(side):
    if get_user(get_jwt_identity()).get("is_admin") != 1:
        return jsonify({"error": "صلاحية مطلوبة"}), 403
    if side not in ("left", "right"):
        return jsonify({"error": "side must be left or right"}), 400
    data = request.get_json() or {}
    pixmap_b64 = data.get("pixmap", "")
    link = data.get("link", "")
    pixmap_bytes = base64.b64decode(pixmap_b64) if pixmap_b64 else None
    save_banner_pixmap(side, pixmap_bytes, link)
    return jsonify({"message": f"تم تحديث صورة المستطيل {side}"})


@app.route("/api/banners/<side>", methods=["DELETE"])
@jwt_required()
def api_delete_banner(side):
    if get_user(get_jwt_identity()).get("is_admin") != 1:
        return jsonify({"error": "صلاحية مطلوبة"}), 403
    delete_banner_pixmap(side)
    return jsonify({"message": f"تم حذف صورة المستطيل {side}"})


@app.route("/api/pending/clear", methods=["POST"])
@jwt_required()
def api_clear_pending():
    clear_pending(get_jwt_identity())
    return jsonify({"message": "تم"})


@app.route("/api/send-verification", methods=["POST"])
@jwt_required()
def api_send_verification():
    user = get_user(get_jwt_identity())
    if not user.get("is_admin"):
        return jsonify({"error": "صلاحية مطلوبة"}), 403
    data = request.get_json() or {}
    phone = data.get("phone", "")
    code = data.get("code", "")
    if not phone or not code:
        return jsonify({"error": "phone and code required"}), 400
    from core.notifier import send_verification_code
    ok = send_verification_code(phone, code)
    return jsonify({"sent": ok})


@app.route("/api/notifier-config", methods=["GET", "POST"])
@jwt_required()
def api_notifier_config():
    user = get_user(get_jwt_identity())
    if not user.get("is_admin"):
        return jsonify({"error": "صلاحية مطلوبة"}), 403
    from core.notifier import _load_config, _save_config
    if request.method == "POST":
        data = request.get_json() or {}
        _save_config(data)
        return jsonify({"message": "تم الحفظ"})
    return jsonify(_load_config())

def run_server(host="0.0.0.0", port=5000, debug=False):
    logger.info("Starting backend server on %s:%s", host, port)
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run_server(debug=True)
