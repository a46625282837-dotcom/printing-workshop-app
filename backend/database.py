import os
import sqlite3
import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

_DB_URL = os.environ.get("DATABASE_URL")
_is_pg = bool(_DB_URL)

PLACEHOLDER = "%s" if _is_pg else "?"
SERIAL_TYPE = "SERIAL" if _is_pg else "INTEGER"
BLOB_TYPE = "BYTEA" if _is_pg else "BLOB"
REF_CLAUSE = "REFERENCES users(username) ON DELETE CASCADE"
INSERT_OR_REPLACE = "INSERT INTO" if _is_pg else "INSERT OR REPLACE INTO"


def _q(sql):
    """Convert SQL placeholders if needed."""
    return sql.replace("%s", "?") if not _is_pg else sql


def _binary(val):
    if val is None:
        return None
    if _is_pg:
        import psycopg2
        return psycopg2.Binary(val)
    return sqlite3.Binary(val)


def _to_bytes(val):
    if val is None:
        return None
    if isinstance(val, memoryview):
        return bytes(val)
    if isinstance(val, bytes):
        return val
    return bytes(val)


def _dict_row(cur, row):
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def _conn():
    if _is_pg:
        import psycopg2
        return psycopg2.connect(_DB_URL)
    _data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(_data_dir, exist_ok=True)
    _db_path = os.path.join(_data_dir, "backend.db")
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q(f"""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            shop_name TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            reg_date TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            token_id TEXT DEFAULT ''
        )
    """))
    cur.execute(_q(f"""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id {SERIAL_TYPE} PRIMARY KEY,
            username TEXT NOT NULL {REF_CLAUSE},
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            days INTEGER NOT NULL
        )
    """))
    cur.execute(_q(f"""
        CREATE TABLE IF NOT EXISTS pending_subs (
            id {SERIAL_TYPE} PRIMARY KEY,
            username TEXT NOT NULL {REF_CLAUSE},
            message TEXT NOT NULL
        )
    """))
    cur.execute(_q(f"""
        CREATE TABLE IF NOT EXISTS profile_pixmaps (
            username TEXT PRIMARY KEY {REF_CLAUSE},
            pixmap {BLOB_TYPE}
        )
    """))
    cur.execute(_q(f"""
        CREATE TABLE IF NOT EXISTS banner_pixmaps (
            side TEXT PRIMARY KEY,
            pixmap {BLOB_TYPE},
            link TEXT DEFAULT ''
        )
    """))
    cur.execute(_q("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """))
    cur.execute(_q(f"""
        CREATE TABLE IF NOT EXISTS notifications (
            id {SERIAL_TYPE} PRIMARY KEY,
            type TEXT NOT NULL,
            text TEXT NOT NULL DEFAULT '',
            link_url TEXT DEFAULT '',
            link_label TEXT DEFAULT '',
            question TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
    """))
    cur.execute(_q("""
        CREATE TABLE IF NOT EXISTS notification_reads (
            notification_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            read_at TEXT NOT NULL,
            PRIMARY KEY (notification_id, username)
        )
    """))
    cur.execute(_q(f"""
        CREATE TABLE IF NOT EXISTS notification_replies (
            id {SERIAL_TYPE} PRIMARY KEY,
            notification_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            reply_text TEXT NOT NULL,
            replied_at TEXT NOT NULL,
            FOREIGN KEY (notification_id) REFERENCES notifications(id) ON DELETE CASCADE
        )
    """))
    try:
        if _is_pg:
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS token_id TEXT DEFAULT ''")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS max_devices INTEGER DEFAULT 1")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login TEXT DEFAULT ''")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen TEXT DEFAULT ''")
        else:
            for col, default in [("token_id", "''"), ("max_devices", "1"), ("last_login", "''"), ("last_seen", "''")]:
                try:
                    cur.execute(f"ALTER TABLE users ADD COLUMN {col} DEFAULT {default}")
                except Exception:
                    pass
    except Exception:
        pass
    cur.execute(_q(f"""
        CREATE TABLE IF NOT EXISTS user_sessions (
            id {SERIAL_TYPE} PRIMARY KEY,
            username TEXT NOT NULL {REF_CLAUSE},
            token_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """))
    _ensure_admin(conn, cur)
    conn.commit()
    cur.close()
    conn.close()
    logger.info("Backend DB initialized (%s)", "PostgreSQL" if _is_pg else "SQLite")


def _ensure_admin(conn, cur):
    import bcrypt
    cur.execute(_q("SELECT username FROM users WHERE username = %s"), ("ahmed",))
    row = cur.fetchone()
    if not row:
        hashed = bcrypt.hashpw(b"Aa511F511fa", bcrypt.gensalt()).decode()
        cur.execute(
            _q("INSERT INTO users (username, password, shop_name, reg_date, is_admin) VALUES (%s, %s, %s, %s, %s)"),
            ("ahmed", hashed, "المالك", date.today().isoformat(), 1),
        )
        logger.info("Admin account created")


def get_user(username):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM users WHERE username = %s"), (username,))
    row = cur.fetchone()
    result = _dict_row(cur, row)
    cur.close()
    conn.close()
    return result


def update_token_id(username, token_id):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q("UPDATE users SET token_id = %s WHERE username = %s"), (token_id, username))
    conn.commit()
    cur.close()
    conn.close()


def update_last_login(username):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q("UPDATE users SET last_login = %s WHERE username = %s"),
                (datetime.utcnow().isoformat(), username))
    conn.commit()
    cur.close()
    conn.close()


def update_last_seen(username):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q("UPDATE users SET last_seen = %s WHERE username = %s"),
                (datetime.utcnow().isoformat(), username))
    conn.commit()
    cur.close()
    conn.close()


def get_admin_user_stats():
    conn = _conn()
    cur = conn.cursor()
    today = date.today().isoformat()
    cur.execute(_q("SELECT COUNT(*) FROM users WHERE is_admin != 1 AND last_login LIKE %s"), (today + "%",))
    today_count = cur.fetchone()[0]
    cur.execute(_q("SELECT COUNT(*) FROM users WHERE is_admin != 1"))
    total_users = cur.fetchone()[0]
    cur.execute(_q("SELECT COUNT(*) FROM users WHERE is_admin != 1 AND (last_login = '' OR last_login IS NULL)"))
    never_active = cur.fetchone()[0]
    thirty_days_ago = (datetime.utcnow() - timedelta(days=30)).isoformat()
    cur.execute(_q("SELECT COUNT(*) FROM users WHERE is_admin != 1 AND last_login != '' AND last_login < %s"), (thirty_days_ago,))
    inactive_count = cur.fetchone()
    inactive_count = inactive_count[0] if inactive_count else 0
    cur.execute(_q("SELECT COUNT(DISTINCT username) FROM user_sessions WHERE created_at >= %s"), (today,))
    row_active = cur.fetchone()
    today_active = row_active[0] if row_active and row_active[0] else today_count
    cur.close()
    conn.close()
    return {
        "total_users": total_users,
        "today_active": today_active,
        "inactive_30d": inactive_count,
        "never_active": never_active,
    }


def get_all_users_with_activity():
    conn = _conn()
    cur = conn.cursor()
    thirty_days_ago = (datetime.utcnow() - timedelta(days=30)).isoformat()
    today = date.today().isoformat()
    cur.execute(_q("""
        SELECT u.username, u.shop_name, u.phone, u.reg_date, u.is_admin, u.max_devices,
               u.last_login, u.last_seen,
               (SELECT end_date FROM subscriptions sub WHERE sub.username = u.username ORDER BY sub.end_date DESC LIMIT 1) AS last_sub_end
        FROM users u WHERE u.is_admin != 1 ORDER BY u.reg_date
    """))
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    result = []
    for r in rows:
        d = dict(zip(cols, r))
        last_login = d.get("last_login") or ""
        last_seen = d.get("last_seen") or ""
        if not last_login:
            d["status"] = "never_active"
        elif last_login < thirty_days_ago:
            d["status"] = "inactive"
        else:
            d["status"] = "active"
        if last_seen and last_seen.startswith(today):
            d["used_today"] = True
        else:
            d["used_today"] = False
        result.append(d)
    cur.close()
    conn.close()
    return result


def get_all_users():
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        _q("SELECT username, shop_name, phone, reg_date, is_admin, max_devices FROM users ORDER BY reg_date")
    )
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    result = [dict(zip(cols, r)) for r in rows]
    cur.close()
    conn.close()
    return result


def create_user(username, password, shop_name, phone):
    import bcrypt
    conn = _conn()
    cur = conn.cursor()
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        cur.execute(
            _q("INSERT INTO users (username, password, shop_name, phone, reg_date, is_admin) VALUES (%s, %s, %s, %s, %s, 0)"),
            (username, hashed, shop_name, phone, date.today().isoformat()),
        )
        conn.commit()
        mark_all_notifications_read(username)
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        cur.close()
        conn.close()


def update_password(username, new_password):
    import bcrypt
    hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q("UPDATE users SET password = %s WHERE username = %s"), (hashed, username))
    conn.commit()
    cur.close()
    conn.close()


def update_profile(username, shop_name, phone):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q("UPDATE users SET shop_name = %s, phone = %s WHERE username = %s"),
                (shop_name, phone, username))
    conn.commit()
    cur.close()
    conn.close()


def get_active_session_count(username):
    from datetime import datetime, timedelta
    cutoff = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q("SELECT COUNT(*) FROM user_sessions WHERE username = %s AND created_at > %s"), (username, cutoff))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else 0


def get_user_sessions(username):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q("SELECT token_id, created_at FROM user_sessions WHERE username = %s ORDER BY created_at"), (username,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{"token_id": r[0], "created_at": r[1]} for r in rows]


def add_session(username, token_id):
    from datetime import datetime
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        _q("INSERT INTO user_sessions (username, token_id, created_at) VALUES (%s, %s, %s)"),
        (username, token_id, datetime.utcnow().isoformat()),
    )
    conn.commit()
    cur.close()
    conn.close()


def remove_session(username, token_id):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        _q("DELETE FROM user_sessions WHERE username = %s AND token_id = %s"),
        (username, token_id),
    )
    conn.commit()
    cur.close()
    conn.close()


def remove_all_sessions(username):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q("DELETE FROM user_sessions WHERE username = %s"), (username,))
    conn.commit()
    cur.close()
    conn.close()


def remove_expired_sessions(username, expiry_hours=2):
    from datetime import datetime, timedelta
    cutoff = (datetime.utcnow() - timedelta(hours=expiry_hours)).isoformat()
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        _q("DELETE FROM user_sessions WHERE username = %s AND created_at < %s"),
        (username, cutoff),
    )
    conn.commit()
    cur.close()
    conn.close()


def remove_all_expired_sessions(expiry_hours=2):
    from datetime import datetime, timedelta
    cutoff = (datetime.utcnow() - timedelta(hours=expiry_hours)).isoformat()
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q("DELETE FROM user_sessions WHERE created_at < %s"), (cutoff,))
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return deleted


def get_max_devices(username):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q("SELECT max_devices FROM users WHERE username = %s"), (username,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row and row[0] is not None:
        return int(row[0])
    return 1


def update_max_devices(username, max_devices):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q("UPDATE users SET max_devices = %s WHERE username = %s"), (max_devices, username))
    conn.commit()
    cur.close()
    conn.close()


def delete_user(username):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q("DELETE FROM user_sessions WHERE username = %s"), (username,))
    cur.execute(_q("DELETE FROM pending_subs WHERE username = %s"), (username,))
    cur.execute(_q("DELETE FROM subscriptions WHERE username = %s"), (username,))
    cur.execute(_q("DELETE FROM profile_pixmaps WHERE username = %s"), (username,))
    cur.execute(_q("DELETE FROM users WHERE username = %s"), (username,))
    conn.commit()
    cur.close()
    conn.close()


def verify_password(username, password):
    import bcrypt
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q("SELECT password FROM users WHERE username = %s"), (username,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return False
    return bcrypt.checkpw(password.encode(), row[0].encode())


def get_subscriptions(username):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        _q("SELECT start_date, end_date, days FROM subscriptions WHERE username = %s ORDER BY start_date"),
        (username,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{"start_date": r[0], "end_date": r[1], "days": r[2]} for r in rows]


def set_subscription_days(username, days):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q("DELETE FROM subscriptions WHERE username = %s"), (username,))
    if days > 0:
        today = date.today()
        start = today.isoformat()
        end = (today + timedelta(days=days)).isoformat()
        cur.execute(
            _q("INSERT INTO subscriptions (username, start_date, end_date, days) VALUES (%s, %s, %s, %s)"),
            (username, start, end, days),
        )
    conn.commit()
    cur.close()
    conn.close()


def compute_remaining_days(username):
    today = date.today().isoformat()
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        _q("SELECT end_date FROM subscriptions WHERE username = %s AND end_date >= %s"),
        (username, today),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    total = 0
    for (end_str,) in rows:
        end = date.fromisoformat(end_str)
        remaining = (end - date.today()).days
        if remaining > 0:
            total += remaining
    return total


def get_pending_messages(username):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        _q("SELECT message FROM pending_subs WHERE username = %s"), (username,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [r[0] for r in rows]


def add_pending_message(username, message):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        _q("INSERT INTO pending_subs (username, message) VALUES (%s, %s)"),
        (username, message),
    )
    conn.commit()
    cur.close()
    conn.close()


def clear_pending(username):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q("DELETE FROM pending_subs WHERE username = %s"), (username,))
    conn.commit()
    cur.close()
    conn.close()


def save_profile_pixmap(username, pixmap_bytes):
    conn = _conn()
    cur = conn.cursor()
    if _is_pg:
        cur.execute(
            _q("INSERT INTO profile_pixmaps (username, pixmap) VALUES (%s, %s) ON CONFLICT (username) DO UPDATE SET pixmap = EXCLUDED.pixmap"),
            (username, _binary(pixmap_bytes)),
        )
    else:
        cur.execute(
            _q("INSERT OR REPLACE INTO profile_pixmaps (username, pixmap) VALUES (%s, %s)"),
            (username, _binary(pixmap_bytes)),
        )
    conn.commit()
    cur.close()
    conn.close()


def get_profile_pixmap(username):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        _q("SELECT pixmap FROM profile_pixmaps WHERE username = %s"), (username,)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return _to_bytes(row[0]) if row and row[0] else None


def save_banner_pixmap(side, pixmap_bytes, link=""):
    conn = _conn()
    cur = conn.cursor()
    if _is_pg:
        cur.execute(
            _q("INSERT INTO banner_pixmaps (side, pixmap, link) VALUES (%s, %s, %s) ON CONFLICT (side) DO UPDATE SET pixmap = EXCLUDED.pixmap, link = EXCLUDED.link"),
            (side, _binary(pixmap_bytes), link),
        )
    else:
        cur.execute(
            _q("INSERT OR REPLACE INTO banner_pixmaps (side, pixmap, link) VALUES (%s, %s, %s)"),
            (side, _binary(pixmap_bytes), link),
        )
    conn.commit()
    cur.close()
    conn.close()


def get_banner_pixmaps():
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q("SELECT * FROM banner_pixmaps"))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    result = {}
    for r in rows:
        result[r[0]] = {
            "pixmap": _to_bytes(r[1]),
            "link": r[2] if r[2] else "",
        }
    return result


def delete_banner_pixmap(side):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q("DELETE FROM banner_pixmaps WHERE side = %s"), (side,))
    conn.commit()
    cur.close()
    conn.close()


def get_setting(key, default=None):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q("SELECT value FROM settings WHERE key = %s"), (key,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row or row[0] is None:
        return default
    return row[0]


def set_setting(key, value):
    conn = _conn()
    cur = conn.cursor()
    if _is_pg:
        cur.execute(
            _q("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"),
            (key, value),
        )
    else:
        cur.execute(
            _q("INSERT OR REPLACE INTO settings (key, value) VALUES (%s, %s)"),
            (key, value),
        )
    conn.commit()
    cur.close()
    conn.close()
    logger.info("Setting %s = %s", key, value)


def get_subscription_required():
    """Whether a subscription is required to use the app (global toggle, default on)."""
    value = get_setting("subscription_required", "1")
    return str(value).lower() in ("1", "true", "yes", "on")


def set_subscription_required(enabled):
    set_setting("subscription_required", "1" if enabled else "0")


def create_notification(ntype, text, link_url="", link_label="", question=""):
    conn = _conn()
    cur = conn.cursor()
    now = datetime.now().isoformat(timespec="seconds")
    if _is_pg:
        cur.execute(
            _q("INSERT INTO notifications (type, text, link_url, link_label, question, created_at) "
               "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id"),
            (ntype, text, link_url, link_label, question, now),
        )
        nid = cur.fetchone()[0]
    else:
        cur.execute(
            _q("INSERT INTO notifications (type, text, link_url, link_label, question, created_at) VALUES (%s, %s, %s, %s, %s, %s)"),
            (ntype, text, link_url, link_label, question, now),
        )
        nid = cur.lastrowid
    conn.commit()
    cur.close()
    conn.close()
    logger.info("تم إنشاء إشعار id=%s type=%s", nid, ntype)
    return nid


def get_notifications_for_user(username):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q(
        "SELECT n.*, EXISTS(SELECT 1 FROM notification_reads r WHERE r.notification_id = n.id AND r.username = %s) AS is_read "
        "FROM notifications n ORDER BY n.id DESC"
    ), (username,))
    rows = cur.fetchall()
    result = []
    for r in rows:
        d = _dict_row(cur, r)
        d["is_read"] = bool(d.get("is_read"))
        result.append(d)
    cur.close()
    conn.close()
    return result


def get_unread_notifications_count(username):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q(
        "SELECT COUNT(*) FROM notifications n "
        "WHERE NOT EXISTS (SELECT 1 FROM notification_reads r WHERE r.notification_id = n.id AND r.username = %s)"
    ), (username,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else 0


def mark_notification_read(notification_id, username):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        _q("INSERT INTO notification_reads (notification_id, username, read_at) VALUES (%s, %s, %s) "
           "ON CONFLICT (notification_id, username) DO UPDATE SET read_at = EXCLUDED.read_at"),
        (notification_id, username, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    cur.close()
    conn.close()


def mark_all_notifications_read(username):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q("SELECT id FROM notifications"))
    rows = cur.fetchall()
    now = datetime.now().isoformat(timespec="seconds")
    for r in rows:
        cur.execute(
            _q("INSERT INTO notification_reads (notification_id, username, read_at) VALUES (%s, %s, %s) "
               "ON CONFLICT (notification_id, username) DO UPDATE SET read_at = EXCLUDED.read_at"),
            (r[0], username, now),
        )
    conn.commit()
    cur.close()
    conn.close()


def add_notification_reply(notification_id, username, reply_text):
    conn = _conn()
    cur = conn.cursor()
    now = datetime.now().isoformat(timespec="seconds")
    if _is_pg:
        cur.execute(
            _q("INSERT INTO notification_replies (notification_id, username, reply_text, replied_at) "
               "VALUES (%s, %s, %s, %s) RETURNING id"),
            (notification_id, username, reply_text, now),
        )
        rid = cur.fetchone()[0]
    else:
        cur.execute(
            _q("INSERT INTO notification_replies (notification_id, username, reply_text, replied_at) VALUES (%s, %s, %s, %s)"),
            (notification_id, username, reply_text, now),
        )
        rid = cur.lastrowid
    conn.commit()
    cur.close()
    conn.close()
    logger.info("رد جديد على إشعار %s من %s", notification_id, username)
    return rid


def get_notification_replies():
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q(
        "SELECT r.id, r.notification_id, r.username, r.reply_text, r.replied_at, "
        "u.shop_name, n.text AS notification_text, n.question AS notification_question "
        "FROM notification_replies r "
        "LEFT JOIN users u ON u.username = r.username "
        "LEFT JOIN notifications n ON n.id = r.notification_id "
        "ORDER BY r.id DESC"
    ))
    rows = cur.fetchall()
    result = [_dict_row(cur, r) for r in rows]
    cur.close()
    conn.close()
    return result


def delete_notification_reply(reply_id):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q("DELETE FROM notification_replies WHERE id = %s"), (reply_id,))
    conn.commit()
    cur.close()
    conn.close()
    logger.info("تم حذف رد الإشعار %s", reply_id)


def delete_notification(notification_id):
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q("DELETE FROM notification_reads WHERE notification_id = %s"), (notification_id,))
    cur.execute(_q("DELETE FROM notification_replies WHERE notification_id = %s"), (notification_id,))
    cur.execute(_q("DELETE FROM notifications WHERE id = %s"), (notification_id,))
    conn.commit()
    cur.close()
    conn.close()
    logger.info("تم حذف الإشعار %s نهائياً", notification_id)
