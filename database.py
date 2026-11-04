import os
import re
import random
import sqlite3
import time
import uuid
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "vidipay.db")

MICRO = 1_000_000                          # balans mikro-dollarda saqlanadi (xatosiz hisob uchun)
BASE_RATE = MICRO / 480                    # 8 daqiqa (480 s) = 1$
FRIEND_BONUS_RATE = 1.05 * MICRO / 3600    # har bir faol do'st uchun soatiga +1.05$
MAX_BEAT_GAP = 10                          # bitta "heartbeat" uchun ko'pi bilan 10 s hisoblanadi
MAX_SESSION_SECONDS = 2 * 3600             # bitta sessiya limiti
ACTIVE_WINDOW = 24 * 3600                  # "faol do'st" = oxirgi 24 soatda video ko'rgan


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


_YT_PATTERNS = [
    r"youtube\.com/watch\?(?:[^#]*&)?v=([A-Za-z0-9_-]{11})",
    r"youtu\.be/([A-Za-z0-9_-]{11})",
    r"youtube\.com/(?:embed|shorts|live)/([A-Za-z0-9_-]{11})",
]


def extract_youtube_id(url):
    url = (url or "").strip()
    for pattern in _YT_PATTERNS:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def _ensure_column(c, table, column, ddl):
    cols = [r["name"] for r in c.execute(f"PRAGMA table_info({table})")]
    if column not in cols:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                fullname TEXT,
                balance_micro INTEGER DEFAULT 0,
                tier INTEGER DEFAULT 1,
                referred_by INTEGER,
                created_at REAL,
                last_watch_at REAL DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_url TEXT,
                youtube_id TEXT,
                title TEXT,
                is_active INTEGER DEFAULT 1
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id INTEGER,
                video_id INTEGER,
                started_at REAL,
                last_beat REAL,
                watched_seconds REAL DEFAULT 0,
                earned_micro INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active'
            )
        """)
        # Eski bazadan ko'chirish (agar vidipay.db avval yaratilgan bo'lsa)
        _ensure_column(c, "users", "balance_micro", "INTEGER DEFAULT 0")
        _ensure_column(c, "users", "referred_by", "INTEGER")
        _ensure_column(c, "users", "created_at", "REAL")
        _ensure_column(c, "users", "last_watch_at", "REAL DEFAULT 0")
        _ensure_column(c, "videos", "youtube_id", "TEXT")
        for row in c.execute("SELECT id, video_url FROM videos WHERE youtube_id IS NULL").fetchall():
            c.execute("UPDATE videos SET youtube_id=? WHERE id=?",
                      (extract_youtube_id(row["video_url"]), row["id"]))


# ---------------- Videolar ----------------

def add_video(url, title):
    yt_id = extract_youtube_id(url)
    if not yt_id:
        return False
    with _conn() as c:
        c.execute(
            "INSERT INTO videos (video_url, youtube_id, title, is_active) VALUES (?, ?, ?, 1)",
            (url.strip(), yt_id, title.strip()),
        )
    return True


def list_videos(limit=20):
    with _conn() as c:
        rows = c.execute(
            "SELECT id, youtube_id, title FROM videos WHERE is_active=1 ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_video(video_id):
    with _conn() as c:
        c.execute("DELETE FROM videos WHERE id=?", (video_id,))


def get_random_video():
    with _conn() as c:
        rows = c.execute(
            "SELECT id, youtube_id, title FROM videos WHERE is_active=1 AND youtube_id IS NOT NULL"
        ).fetchall()
    return dict(random.choice(rows)) if rows else None


# ---------------- Foydalanuvchilar ----------------

def create_user(user_id, username, fullname, referred_by=None):
    with _conn() as c:
        if referred_by == user_id or (
            referred_by and not c.execute(
                "SELECT 1 FROM users WHERE user_id=?", (referred_by,)).fetchone()
        ):
            referred_by = None
        c.execute(
            "INSERT OR IGNORE INTO users (user_id, username, fullname, referred_by, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, username, fullname, referred_by, time.time()),
        )
        c.execute("UPDATE users SET username=?, fullname=? WHERE user_id=?",
                  (username, fullname, user_id))


def get_user(user_id):
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def friends_stats(user_id):
    """(jami do'stlar, faol do'stlar)"""
    since = time.time() - ACTIVE_WINDOW
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) FROM users WHERE referred_by=?", (user_id,)).fetchone()[0]
        active = c.execute(
            "SELECT COUNT(*) FROM users WHERE referred_by=? AND tier=1 AND last_watch_at>?",
            (user_id, since),
        ).fetchone()[0]
    return total, active


def update_balance(user_id, amount_dollars):
    with _conn() as c:
        c.execute("UPDATE users SET balance_micro = balance_micro + ? WHERE user_id=?",
                  (round(amount_dollars * MICRO), user_id))


# ---------------- Ko'rish sessiyalari ----------------

def start_session(user_id):
    video = get_random_video()
    if not video:
        return None
    now = time.time()
    session_id = uuid.uuid4().hex
    with _conn() as c:
        # Bir vaqtda faqat bitta faol sessiya (parallel "ferma"ga yo'l qo'ymaslik uchun)
        c.execute("UPDATE sessions SET status='closed' WHERE user_id=? AND status='active'", (user_id,))
        c.execute(
            "INSERT INTO sessions (id, user_id, video_id, started_at, last_beat) VALUES (?, ?, ?, ?, ?)",
            (session_id, user_id, video["id"], now, now),
        )
    return {"session_id": session_id, "video": video}


def _reward_micro(c, user_id, seconds):
    base = seconds * BASE_RATE
    bonus = 0.0
    user = c.execute("SELECT tier FROM users WHERE user_id=?", (user_id,)).fetchone()
    if user and user["tier"] == 1:
        since = time.time() - ACTIVE_WINDOW
        friends = c.execute(
            "SELECT COUNT(*) FROM users WHERE referred_by=? AND tier=1 AND last_watch_at>?",
            (user_id, since),
        ).fetchone()[0]
        bonus = friends * seconds * FRIEND_BONUS_RATE
    return round(base + bonus)


def beat_session(user_id, session_id, playing):
    """Mijoz har ~5 soniyada chaqiradi. Vaqtni server o'lchaydi, mijozga ishonilmaydi."""
    now = time.time()
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        s = c.execute("SELECT * FROM sessions WHERE id=? AND user_id=?", (session_id, user_id)).fetchone()
        if not s or s["status"] != "active":
            return None
        elapsed = max(0.0, now - s["last_beat"])
        credit = 0.0
        if playing:
            credit = min(elapsed, MAX_BEAT_GAP, MAX_SESSION_SECONDS - s["watched_seconds"])
            credit = max(credit, 0.0)
        earned = _reward_micro(c, user_id, credit) if credit > 0 else 0
        c.execute(
            "UPDATE sessions SET last_beat=?, watched_seconds=watched_seconds+?, "
            "earned_micro=earned_micro+? WHERE id=?",
            (now, credit, earned, session_id),
        )
        if earned:
            c.execute(
                "UPDATE users SET balance_micro=balance_micro+?, last_watch_at=? WHERE user_id=?",
                (earned, now, user_id),
            )
        total = c.execute("SELECT watched_seconds, earned_micro FROM sessions WHERE id=?", (session_id,)).fetchone()
        balance = c.execute("SELECT balance_micro FROM users WHERE user_id=?", (user_id,)).fetchone()[0]
    return {
        "watched": int(total["watched_seconds"]),
        "earned": total["earned_micro"] / MICRO,
        "balance": balance / MICRO,
    }


def finish_session(user_id, session_id):
    with _conn() as c:
        s = c.execute("SELECT * FROM sessions WHERE id=? AND user_id=?", (session_id, user_id)).fetchone()
        if not s:
            return None
        if s["status"] == "active":
            c.execute("UPDATE sessions SET status='done' WHERE id=?", (session_id,))
        balance = c.execute("SELECT balance_micro FROM users WHERE user_id=?", (user_id,)).fetchone()[0]
    return {
        "watched": int(s["watched_seconds"]),
        "earned": s["earned_micro"] / MICRO,
        "balance": balance / MICRO,
    }


# ---------------- Statistika ----------------

def get_stats():
    since = time.time() - ACTIVE_WINDOW
    with _conn() as c:
        users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active = c.execute("SELECT COUNT(*) FROM users WHERE last_watch_at>?", (since,)).fetchone()[0]
        videos = c.execute("SELECT COUNT(*) FROM videos WHERE is_active=1").fetchone()[0]
        balance = c.execute("SELECT COALESCE(SUM(balance_micro),0) FROM users").fetchone()[0]
        seconds = c.execute("SELECT COALESCE(SUM(watched_seconds),0) FROM sessions").fetchone()[0]
    return {
        "users": users,
        "active_24h": active,
        "videos": videos,
        "total_balance": balance / MICRO,
        "watch_hours": seconds / 3600,
    }
