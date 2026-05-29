import sqlite3
import os
import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DATA_DIR, "backend.db")


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            shop_name TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            reg_date TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0
        )
    """)
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
        CREATE TABLE IF NOT EXISTS profile_pixmaps (
            username TEXT PRIMARY KEY,
            pixmap BLOB,
            FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS banner_pixmaps (
            side TEXT,
            pixmap BLOB,
            link TEXT DEFAULT '',
            PRIMARY KEY (side)
        )
    """)
    _ensure_admin(conn)
    conn.commit()
    conn.close()
    logger.info("Backend DB initialized: %s", DB_PATH)


def _ensure_admin(conn):
    import bcrypt
    row = conn.execute("SELECT username FROM users WHERE username = 'ahmed'").fetchone()
    if not row:
        hashed = bcrypt.hashpw(b"Aa511F511fa", bcrypt.gensalt()).decode()
        conn.execute(
            "INSERT INTO users (username, password, shop_name, reg_date, is_admin) VALUES (?, ?, ?, ?, ?)",
            ("ahmed", hashed, "المالك", date.today().isoformat(), 1),
        )
        logger.info("Admin account created")


def get_user(username):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def get_all_users():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT username, shop_name, phone, reg_date, is_admin FROM users ORDER BY reg_date"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_user(username, password, shop_name, phone):
    import bcrypt
    conn = sqlite3.connect(DB_PATH)
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        conn.execute(
            "INSERT INTO users (username, password, shop_name, phone, reg_date, is_admin) VALUES (?, ?, ?, ?, ?, 0)",
            (username, hashed, shop_name, phone, date.today().isoformat()),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def update_password(username, new_password):
    import bcrypt
    hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE users SET password = ? WHERE username = ?", (hashed, username))
    conn.commit()
    conn.close()


def update_profile(username, shop_name, phone):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE users SET shop_name = ?, phone = ? WHERE username = ?",
                 (shop_name, phone, username))
    conn.commit()
    conn.close()


def delete_user(username):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM pending_subs WHERE username = ?", (username,))
    conn.execute("DELETE FROM subscriptions WHERE username = ?", (username,))
    conn.execute("DELETE FROM profile_pixmaps WHERE username = ?", (username,))
    conn.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    conn.close()


def verify_password(username, password):
    import bcrypt
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT password FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    if not row:
        return False
    return bcrypt.checkpw(password.encode(), row[0].encode())


def get_subscriptions(username):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT start_date, end_date, days FROM subscriptions WHERE username = ? ORDER BY start_date",
        (username,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_subscription(username, start_date, end_date, days):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO subscriptions (username, start_date, end_date, days) VALUES (?, ?, ?, ?)",
        (username, start_date, end_date, days),
    )
    conn.commit()
    conn.close()


def compute_remaining_days(username):
    today = date.today().isoformat()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT end_date FROM subscriptions WHERE username = ? AND end_date >= ?",
        (username, today),
    ).fetchall()
    conn.close()
    total = 0
    for (end_str,) in rows:
        end = date.fromisoformat(end_str)
        remaining = (end - date.today()).days
        if remaining > 0:
            total += remaining
    return total


def get_pending_messages(username):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT message FROM pending_subs WHERE username = ?", (username,)
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def add_pending_message(username, message):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO pending_subs (username, message) VALUES (?, ?)",
        (username, message),
    )
    conn.commit()
    conn.close()


def clear_pending(username):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM pending_subs WHERE username = ?", (username,))
    conn.commit()
    conn.close()


def save_profile_pixmap(username, pixmap_bytes):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO profile_pixmaps (username, pixmap) VALUES (?, ?)",
        (username, sqlite3.Binary(pixmap_bytes) if pixmap_bytes else None),
    )
    conn.commit()
    conn.close()


def get_profile_pixmap(username):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT pixmap FROM profile_pixmaps WHERE username = ?", (username,)
    ).fetchone()
    conn.close()
    return bytes(row[0]) if row and row[0] else None


def save_banner_pixmap(side, pixmap_bytes, link=""):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO banner_pixmaps (side, pixmap, link) VALUES (?, ?, ?)",
        (side, sqlite3.Binary(pixmap_bytes) if pixmap_bytes else None, link),
    )
    conn.commit()
    conn.close()


def get_banner_pixmaps():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM banner_pixmaps").fetchall()
    conn.close()
    result = {}
    for r in rows:
        result[r["side"]] = {
            "pixmap": bytes(r["pixmap"]) if r["pixmap"] else None,
            "link": r["link"],
        }
    return result


def delete_banner_pixmap(side):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM banner_pixmaps WHERE side = ?", (side,))
    conn.commit()
    conn.close()
