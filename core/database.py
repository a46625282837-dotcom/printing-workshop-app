import sqlite3
import json
import os
import sys
import logging
from datetime import date, datetime

logger = logging.getLogger(__name__)

if getattr(sys, "frozen", False):
    DATA_DIR = os.path.join(os.path.dirname(sys.executable), "data")
else:
    DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DATA_DIR, "app.db")

BLOB_FIELDS = {"profile_pixmap", "banner_left_pixmap", "banner_right_pixmap"}
SUB_FIELDS = {"subscriptions", "pending_subs"}

USE_API = False


def set_api_mode(enabled):
    global USE_API
    USE_API = enabled


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            profile_pixmap BLOB
        )
    """)
    for col in ("banner_left_pixmap", "banner_right_pixmap"):
        try:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} BLOB")
        except sqlite3.OperationalError:
            pass
    conn.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            days INTEGER NOT NULL,
            FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_subs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            message TEXT NOT NULL,
            FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            text TEXT NOT NULL DEFAULT '',
            link_url TEXT DEFAULT '',
            link_label TEXT DEFAULT '',
            question TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notification_reads (
            notification_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            read_at TEXT NOT NULL,
            PRIMARY KEY (notification_id, username)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notification_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notification_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            reply_text TEXT NOT NULL,
            replied_at TEXT NOT NULL,
            FOREIGN KEY (notification_id) REFERENCES notifications(id) ON DELETE CASCADE
        )
    """)
    _migrate_old_subscriptions(conn)
    conn.commit()
    conn.close()
    logger.info("تم تهيئة قاعدة البيانات: %s", DB_PATH)


def _migrate_old_subscriptions(conn):
    migrated = 0
    for row in conn.execute("SELECT username, data FROM users"):
        username, data_json = row
        data = json.loads(data_json)
        subs = data.pop("subscriptions", None)
        pending = data.pop("pending_subs", None)
        if subs:
            conn.execute("DELETE FROM subscriptions WHERE username = ?", (username,))
            for s in subs:
                conn.execute(
                    "INSERT INTO subscriptions (username, start_date, end_date, days) VALUES (?, ?, ?, ?)",
                    (username, s["start"], s["end"], s["days"]),
                )
            data["subscriptions"] = []
            migrated += 1
        if pending:
            conn.execute("DELETE FROM pending_subs WHERE username = ?", (username,))
            for msg in pending:
                conn.execute(
                    "INSERT INTO pending_subs (username, message) VALUES (?, ?)",
                    (username, msg),
                )
            data["pending_subs"] = []
            migrated += 1
        if subs is not None or pending is not None:
            conn.execute(
                "UPDATE users SET data = ? WHERE username = ?",
                (json.dumps(data, ensure_ascii=False), username),
            )
    if migrated:
        logger.info("تم ترحيل %d مستخدم من الاشتراكات القديمة", migrated)


def load_users():
    users = {}
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    for row in conn.execute("SELECT username, data, profile_pixmap, banner_left_pixmap, banner_right_pixmap FROM users"):
        d = json.loads(row["data"])
        for field in BLOB_FIELDS:
            if row[field]:
                d[field] = bytes(row[field])
        d["subscriptions"] = []
        d["pending_subs"] = []
        users[row["username"]] = d
    for sub_row in conn.execute("SELECT username, start_date, end_date, days FROM subscriptions ORDER BY start_date"):
        u = sub_row["username"]
        if u in users:
            users[u]["subscriptions"].append({
                "start": sub_row["start_date"],
                "end": sub_row["end_date"],
                "days": sub_row["days"],
            })
    for p_row in conn.execute("SELECT username, message FROM pending_subs"):
        u = p_row["username"]
        if u in users:
            users[u]["pending_subs"].append(p_row["message"])
    conn.close()
    logger.info("تم تحميل %d مستخدم من قاعدة البيانات", len(users))
    return users


def save_user(username, user_dict):
    profile_pix = user_dict.get("profile_pixmap")
    banner_left = user_dict.get("banner_left_pixmap")
    banner_right = user_dict.get("banner_right_pixmap")
    skip = BLOB_FIELDS | SUB_FIELDS
    data = {k: v for k, v in user_dict.items() if k not in skip}
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO users (username, data, profile_pixmap, banner_left_pixmap, banner_right_pixmap) VALUES (?, ?, ?, ?, ?)",
        (username, json.dumps(data, ensure_ascii=False),
         sqlite3.Binary(profile_pix) if profile_pix else None,
         sqlite3.Binary(banner_left) if banner_left else None,
         sqlite3.Binary(banner_right) if banner_right else None),
    )
    subs = user_dict.get("subscriptions")
    if subs is not None:
        conn.execute("DELETE FROM subscriptions WHERE username = ?", (username,))
        for s in subs:
            conn.execute(
                "INSERT INTO subscriptions (username, start_date, end_date, days) VALUES (?, ?, ?, ?)",
                (username, s["start"], s["end"], s["days"]),
            )
    pend = user_dict.get("pending_subs")
    if pend is not None:
        conn.execute("DELETE FROM pending_subs WHERE username = ?", (username,))
        for msg in pend:
            conn.execute(
                "INSERT INTO pending_subs (username, message) VALUES (?, ?)",
                (username, msg),
            )
    conn.commit()
    conn.close()
    logger.info("تم حفظ المستخدم %s في قاعدة البيانات", username)


def delete_user(username):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM pending_subs WHERE username = ?", (username,))
    conn.execute("DELETE FROM subscriptions WHERE username = ?", (username,))
    conn.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    conn.close()
    logger.info("تم حذف المستخدم %s من قاعدة البيانات", username)


def compute_subscription_days(username):
    today = date.today().isoformat()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT start_date, end_date, days FROM subscriptions WHERE username = ? AND end_date >= ?",
        (username, today),
    ).fetchall()
    conn.close()
    total = 0
    for start_str, end_str, days in rows:
        end = date.fromisoformat(end_str)
        remaining = (end - date.today()).days
        if remaining > 0:
            total += remaining
    return total


def add_subscription(username, start_date, end_date, days):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO subscriptions (username, start_date, end_date, days) VALUES (?, ?, ?, ?)",
        (username, start_date, end_date, days),
    )
    conn.commit()
    conn.close()
    logger.info("تم إضافة اشتراك للمستخدم %s: %d يوم", username, days)


def add_pending_message(username, message):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO pending_subs (username, message) VALUES (?, ?)",
        (username, message),
    )
    conn.commit()
    conn.close()
    logger.info("تم إضافة إشعار للمستخدم %s", username)


def clear_pending(username):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM pending_subs WHERE username = ?", (username,))
    conn.commit()
    conn.close()


# API mode wrappers
def api_login(username, password, force_login=False):
    from . import api_client
    data, err = api_client.login(username, password, force_login=force_login)
    if err:
        return None, err
    return data, None


def api_login_force_check(username, password):
    from . import api_client
    return api_client.login_check_force(username, password)


def api_register(username, password, shop_name, phone):
    from . import api_client
    data, err = api_client.register(username, password, shop_name, phone)
    if err:
        return None, err
    return data, None


def api_get_users():
    from . import api_client
    return api_client.get_users()


def api_set_subscription(username, days):
    from . import api_client
    return api_client.set_subscription(username, days)


def api_delete_user(username):
    from . import api_client
    return api_client.delete_user(username)


def api_change_password(new_password):
    from . import api_client
    return api_client.change_password(new_password)


def api_reset_password(username, new_password):
    from . import api_client
    return api_client.reset_password(username, new_password)


def api_update_profile(shop_name, phone):
    from . import api_client
    return api_client.update_profile(shop_name, phone)


def api_upload_pixmap(pixmap_bytes):
    from . import api_client
    return api_client.upload_pixmap(pixmap_bytes)


def api_check_auth():
    from . import api_client
    return api_client.check_auth()


def api_get_subscriptions(username):
    from . import api_client
    return api_client.get_subscriptions(username)


def api_clear_pending():
    from . import api_client
    return api_client.clear_pending()


def api_get_banners():
    from . import api_client
    return api_client.get_banners()


def api_set_banner(side, pixmap_bytes, link=""):
    from . import api_client
    return api_client.set_banner(side, pixmap_bytes, link)


def api_delete_banner(side):
    from . import api_client
    return api_client.delete_banner(side)


def api_logout():
    from . import api_client
    return api_client.logout()


def api_get_user_sessions(username):
    from . import api_client
    return api_client.get_user_sessions(username)


def api_set_max_devices(username, max_devices):
    from . import api_client
    return api_client.set_max_devices(username, max_devices)


def get_subscription_required():
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?", ("subscription_required",)
    ).fetchone()
    conn.close()
    if not row or row[0] is None:
        return True
    return str(row[0]).lower() in ("1", "true", "yes", "on")


def set_subscription_required(enabled):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ("subscription_required", "1" if enabled else "0"),
    )
    conn.commit()
    conn.close()
    logger.info("تم تعيين إلزامية الاشتراك (محلي): %s", enabled)


def api_get_subscription_required():
    from . import api_client
    data, err = api_client.get_settings()
    if err or data is None:
        return True, err
    return bool(data.get("subscription_required", True)), None


def api_set_subscription_required(enabled):
    from . import api_client
    return api_client.set_subscription_required(enabled)


def create_notification(ntype, text, link_url="", link_label="", question=""):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "INSERT INTO notifications (type, text, link_url, link_label, question, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (ntype, text, link_url, link_label, question,
         datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    nid = cur.lastrowid
    conn.close()
    logger.info("تم إنشاء إشعار محلي id=%s type=%s", nid, ntype)
    return nid


def get_notifications_for_user(username):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT n.*, EXISTS(SELECT 1 FROM notification_reads r "
        "WHERE r.notification_id = n.id AND r.username = ?) AS is_read "
        "FROM notifications n ORDER BY n.id DESC",
        (username,),
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["is_read"] = bool(d.get("is_read"))
        result.append(d)
    return result


def get_unread_notifications_count(username):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT COUNT(*) FROM notifications n "
        "WHERE NOT EXISTS (SELECT 1 FROM notification_reads r "
        "WHERE r.notification_id = n.id AND r.username = ?)",
        (username,),
    ).fetchone()
    conn.close()
    return row[0] if row else 0


def mark_notification_read(notification_id, username):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO notification_reads (notification_id, username, read_at) "
        "VALUES (?, ?, ?)",
        (notification_id, username, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()


def mark_all_notifications_read(username):
    conn = sqlite3.connect(DB_PATH)
    now = datetime.now().isoformat(timespec="seconds")
    for r in conn.execute("SELECT id FROM notifications").fetchall():
        conn.execute(
            "INSERT OR REPLACE INTO notification_reads (notification_id, username, read_at) "
            "VALUES (?, ?, ?)",
            (r[0], username, now),
        )
    conn.commit()
    conn.close()


def add_notification_reply(notification_id, username, reply_text):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "INSERT INTO notification_replies (notification_id, username, reply_text, replied_at) "
        "VALUES (?, ?, ?, ?)",
        (notification_id, username, reply_text,
         datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    logger.info("رد محلي على إشعار %s من %s", notification_id, username)
    return rid


def get_notification_replies():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT r.id, r.notification_id, r.username, r.reply_text, r.replied_at, "
        "u.data AS user_data, n.text AS notification_text, n.question AS notification_question "
        "FROM notification_replies r "
        "LEFT JOIN users u ON u.username = r.username "
        "LEFT JOIN notifications n ON n.id = r.notification_id "
        "ORDER BY r.id DESC",
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        user_data = d.pop("user_data", None)
        try:
            ud = json.loads(user_data) if user_data else {}
        except Exception:
            ud = {}
        d["shop_name"] = ud.get("shop_name", d["username"])
        result.append(d)
    return result


def delete_notification_reply(reply_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM notification_replies WHERE id = ?", (reply_id,))
    conn.commit()
    conn.close()
    logger.info("تم حذف رد الإشعار المحلي %s", reply_id)


def delete_notification(notification_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM notification_reads WHERE notification_id = ?", (notification_id,))
    conn.execute("DELETE FROM notification_replies WHERE notification_id = ?", (notification_id,))
    conn.execute("DELETE FROM notifications WHERE id = ?", (notification_id,))
    conn.commit()
    conn.close()
    logger.info("تم حذف الإشعار المحلي %s نهائياً", notification_id)


def api_delete_notification(notification_id):
    from . import api_client
    return api_client.delete_notification(notification_id)


def api_get_notifications():
    from . import api_client
    return api_client.get_notifications()


def api_create_notification(ntype, text, link_url="", link_label="", question=""):
    from . import api_client
    return api_client.create_notification(ntype, text, link_url, link_label, question)


def api_mark_notifications_read(notification_id=None, mark_all=False):
    from . import api_client
    return api_client.mark_notification_read(notification_id, mark_all)


def api_reply_notification(notification_id, reply_text):
    from . import api_client
    return api_client.reply_notification(notification_id, reply_text)


def api_get_notification_replies():
    from . import api_client
    return api_client.get_notification_replies()


def api_delete_notification_reply(reply_id):
    from . import api_client
    return api_client.delete_notification_reply(reply_id)


def api_get_user_details(username):
    from . import api_client
    return api_client.get_user_details(username)


def api_get_admin_user_stats():
    from . import api_client
    return api_client.get_admin_user_stats()


def api_get_admin_all_users():
    from . import api_client
    return api_client.get_admin_all_users()
