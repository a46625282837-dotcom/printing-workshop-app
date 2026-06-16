import os
import sqlite3
import logging
from datetime import date, timedelta

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
    try:
        if _is_pg:
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS token_id TEXT DEFAULT ''")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS max_devices INTEGER DEFAULT 1")
        else:
            cur.execute("ALTER TABLE users ADD COLUMN token_id TEXT DEFAULT ''")
            try:
                cur.execute("ALTER TABLE users ADD COLUMN max_devices INTEGER DEFAULT 1")
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
    conn = _conn()
    cur = conn.cursor()
    cur.execute(_q("SELECT COUNT(*) FROM user_sessions WHERE username = %s"), (username,))
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
