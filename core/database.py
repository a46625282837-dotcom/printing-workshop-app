import sqlite3
import json
import os
import sys
import logging
from datetime import date

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
def api_login(username, password):
    from . import api_client
    data, err = api_client.login(username, password)
    if err:
        return None, err
    return data, None


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
