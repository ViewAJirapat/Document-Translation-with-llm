"""SQLite storage for jobs, text blocks and settings.

Every call opens its own short-lived connection, which keeps it safe to use from the web
handlers and the background worker thread at the same time.
"""
import sqlite3
import time
import uuid
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id         TEXT PRIMARY KEY,
    filename   TEXT NOT NULL,
    status     TEXT NOT NULL,     -- queued, extracting, translating, done, failed, cancelled
    force_ocr  INTEGER NOT NULL DEFAULT 0,
    error      TEXT,
    pages      INTEGER,
    model      TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS blocks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    page        INTEGER,
    kind        TEXT NOT NULL,    -- heading, paragraph, list_item, table, caption, footnote, code, formula
    level       INTEGER,
    marker      TEXT,
    source      TEXT NOT NULL,
    translation TEXT,
    status      TEXT NOT NULL,    -- pending, done, skip (verbatim, not translated)
    updated_at  REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS blocks_job ON blocks(job_id, seq);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.execute("PRAGMA journal_mode = WAL")
        c.executescript(SCHEMA)
        # Extraction can't resume half-way, so a job interrupted there starts over.
        c.execute("UPDATE jobs SET status = 'queued' WHERE status = 'extracting'")


def upload_path(job_id):
    return config.UPLOAD_DIR / f"{job_id}.pdf"


# --- jobs ---------------------------------------------------------------------------------

def create_job(filename, force_ocr):
    job_id = uuid.uuid4().hex
    now = time.time()
    with connect() as c:
        c.execute(
            "INSERT INTO jobs (id, filename, status, force_ocr, created_at, updated_at) VALUES (?, ?, 'queued', ?, ?, ?)",
            (job_id, filename, int(force_ocr), now, now),
        )
    return job_id


def get_job(job_id):
    with connect() as c:
        return c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def list_jobs():
    with connect() as c:
        return c.execute(
            """SELECT j.*,
                      (SELECT COUNT(*) FROM blocks b WHERE b.job_id = j.id AND b.status != 'skip') AS total,
                      (SELECT COUNT(*) FROM blocks b WHERE b.job_id = j.id AND b.status = 'done') AS done
               FROM jobs j ORDER BY j.created_at DESC"""
        ).fetchall()


def next_job():
    """Oldest job that still has work: new uploads and interrupted translations."""
    with connect() as c:
        return c.execute(
            "SELECT * FROM jobs WHERE status IN ('queued', 'translating') ORDER BY created_at LIMIT 1"
        ).fetchone()


def update_job(job_id, **fields):
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))


def delete_job(job_id):
    with connect() as c:
        c.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    upload_path(job_id).unlink(missing_ok=True)


def progress(job_id):
    with connect() as c:
        row = c.execute(
            """SELECT SUM(status != 'skip') AS total, SUM(status = 'done') AS done
               FROM blocks WHERE job_id = ?""",
            (job_id,),
        ).fetchone()
    return {"total": row["total"] or 0, "done": row["done"] or 0}


# --- blocks -------------------------------------------------------------------------------

def replace_blocks(job_id, blocks):
    now = time.time()
    with connect() as c:
        c.execute("DELETE FROM blocks WHERE job_id = ?", (job_id,))
        c.executemany(
            """INSERT INTO blocks (job_id, seq, page, kind, level, marker, source, status, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (job_id, i, b.get("page"), b["kind"], b.get("level"), b.get("marker"), b["text"],
                 "skip" if b.get("verbatim") else "pending", now)
                for i, b in enumerate(blocks)
            ],
        )


def get_blocks(job_id):
    with connect() as c:
        return c.execute("SELECT * FROM blocks WHERE job_id = ? ORDER BY seq", (job_id,)).fetchall()


def get_block(block_id):
    with connect() as c:
        return c.execute("SELECT * FROM blocks WHERE id = ?", (block_id,)).fetchone()


def next_pending_block(job_id):
    with connect() as c:
        return c.execute(
            "SELECT * FROM blocks WHERE job_id = ? AND status = 'pending' ORDER BY seq LIMIT 1", (job_id,)
        ).fetchone()


def blocks_since(job_id, since):
    with connect() as c:
        return c.execute(
            "SELECT id, status, translation FROM blocks WHERE job_id = ? AND updated_at >= ? ORDER BY seq",
            (job_id, since),
        ).fetchall()


def update_block(block_id, **fields):
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE blocks SET {cols} WHERE id = ?", (*fields.values(), block_id))


def reset_translations(job_id):
    with connect() as c:
        c.execute(
            "UPDATE blocks SET status = 'pending', updated_at = ? WHERE job_id = ? AND status = 'done'",
            (time.time(), job_id),
        )


# --- settings -----------------------------------------------------------------------------

def get_setting(key, default=""):
    with connect() as c:
        row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    with connect() as c:
        c.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
