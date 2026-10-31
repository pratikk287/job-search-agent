"""SQLite storage."""
import json
import sqlite3
import threading
from datetime import datetime, timezone

from .config import DB_PATH

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,            -- greenhouse, lever, jsearch, manual ...
    source_type TEXT NOT NULL,       -- employer | aggregator | manual
    publisher TEXT,                  -- where the aggregator found it (LinkedIn, Naukri ...)
    external_id TEXT,
    company TEXT NOT NULL,
    company_domain TEXT,
    title TEXT NOT NULL,
    location TEXT,
    city TEXT,
    remote INTEGER DEFAULT 0,
    url TEXT,
    apply_url TEXT,
    description TEXT,
    posted_at TEXT,
    valid_through TEXT,
    salary_min_lpa REAL,
    salary_max_lpa REAL,
    salary_text TEXT,
    function TEXT,
    score REAL,
    score_breakdown TEXT,
    resume_key TEXT,
    resume_reason TEXT,
    verification_status TEXT DEFAULT 'unchecked',
    verification_notes TEXT,
    verified_at TEXT,
    status TEXT DEFAULT 'new',
    applied_at TEXT,
    dedupe_key TEXT,
    first_seen TEXT,
    last_seen TEXT,
    notes TEXT,
    raw TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_dedupe ON jobs(dedupe_key);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    company TEXT,
    name TEXT,
    title TEXT,
    role_type TEXT,                  -- recruiter | hiring_manager | department_head | generic
    email TEXT,
    email_status TEXT,               -- verified | public | pattern_guess | unknown
    email_confidence INTEGER,
    linkedin_url TEXT,
    source TEXT,
    source_url TEXT,
    created_at TEXT,
    UNIQUE(job_id, email, name)
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    kind TEXT,                       -- resume_docx | resume_pdf | cover_letter_docx | cover_letter_txt
    path TEXT,
    generator TEXT,                  -- template | claude
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    contact_id INTEGER,
    kind TEXT NOT NULL,              -- email | linkedin_note | linkedin_message | application | followup_email
    channel TEXT,                    -- smtp | manual | browser_prefill
    subject TEXT,
    body TEXT,
    recipient TEXT,
    attachments TEXT,
    status TEXT DEFAULT 'draft',     -- draft | approved | executing | done | failed | rejected
    approved_at TEXT,
    executed_at TEXT,
    result TEXT,
    due_at TEXT,
    parent_action_id INTEGER,
    replied_at TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    kind TEXT,
    detail TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS li_outreach (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    contact_id INTEGER,
    company TEXT,
    job_title TEXT,
    job_url TEXT,
    name TEXT,
    title TEXT,
    who TEXT,
    linkedin_url TEXT,
    note TEXT,
    message TEXT,
    email_guess TEXT,
    note_sent_at TEXT,
    message_sent_at TEXT,
    replied_at TEXT,
    created_at TEXT,
    UNIQUE(job_id, linkedin_url)
);

CREATE TABLE IF NOT EXISTS email_patterns (
    domain TEXT PRIMARY KEY,
    pattern TEXT,
    example TEXT,
    confirmed INTEGER DEFAULT 0,
    bounces INTEGER DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    finished_at TEXT,
    summary TEXT
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


_conn = None


def conn():
    global _conn
    with _lock:
        if _conn is None:
            _conn = connect()
            _conn.executescript(SCHEMA)
            try:
                _conn.execute("ALTER TABLE email_patterns ADD COLUMN tried TEXT")
                _conn.commit()
            except Exception:
                pass            # column already exists
        return _conn


def query(sql, params=()):
    with _lock:
        return [dict(r) for r in conn().execute(sql, params).fetchall()]


def one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    with _lock:
        c = conn()
        cur = c.execute(sql, params)
        c.commit()
        return cur.lastrowid


def insert(table, row):
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    return execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))


def update(table, key, key_value, fields):
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    execute(f"UPDATE {table} SET {sets} WHERE {key} = ?", (*fields.values(), key_value))


def log_event(job_id, kind, detail=None):
    insert("events", {"job_id": job_id, "kind": kind,
                      "detail": json.dumps(detail) if isinstance(detail, (dict, list)) else detail,
                      "created_at": now()})
