"""SQLite storage. One connection per call; WAL mode so reads never block."""
import os
import sqlite3

DATA_DIR = os.environ.get(
    "DATA_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"),
)
DB_PATH = os.path.join(DATA_DIR, "portal.db")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    title         TEXT NOT NULL DEFAULT '',
    phone         TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS folders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    category   TEXT NOT NULL CHECK (category IN ('working', 'company')),
    restricted INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS folder_access (
    folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (folder_id, user_id)
);

CREATE TABLE IF NOT EXISTS docs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    category   TEXT NOT NULL CHECK (category IN ('working', 'company')),
    content    TEXT NOT NULL DEFAULT '',
    folder_id  INTEGER REFERENCES folders(id),
    updated_by INTEGER REFERENCES users(id),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS doc_versions (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id   INTEGER NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    title    TEXT NOT NULL,
    content  TEXT NOT NULL,
    saved_by INTEGER REFERENCES users(id),
    saved_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_versions_doc ON doc_versions (doc_id, saved_at);

CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    orig_name   TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    category    TEXT NOT NULL CHECK (category IN ('working', 'company')),
    size        INTEGER NOT NULL,
    folder_id   INTEGER REFERENCES folders(id),
    uploaded_by INTEGER REFERENCES users(id),
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS announcements (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL DEFAULT '',
    author_id  INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo', 'doing', 'done')),
    assignee_id INTEGER REFERENCES users(id),
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS links (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    title    TEXT NOT NULL,
    url      TEXT NOT NULL,
    note     TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'General'
);

CREATE TABLE IF NOT EXISTS weather_cities (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name   TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT '',
    lat    REAL NOT NULL,
    lon    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS ticker_symbols (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL UNIQUE,
    name   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS onboarding_items (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    label    TEXT NOT NULL,
    detail   TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS onboarding_done (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    item_id INTEGER NOT NULL REFERENCES onboarding_items(id) ON DELETE CASCADE,
    done_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, item_id)
);

CREATE TABLE IF NOT EXISTS expense_lists (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    kind   TEXT NOT NULL CHECK (kind IN ('entity', 'vehicle', 'category', 'payment')),
    name   TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    UNIQUE (kind, name)
);

CREATE TABLE IF NOT EXISTS expenses (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    date         TEXT NOT NULL,
    entity_id    INTEGER NOT NULL REFERENCES expense_lists(id),
    merchant     TEXT NOT NULL,
    category_id  INTEGER REFERENCES expense_lists(id),
    payment_id   INTEGER REFERENCES expense_lists(id),
    amount_cents INTEGER NOT NULL,
    tags         TEXT NOT NULL DEFAULT '',
    notes        TEXT NOT NULL DEFAULT '',
    receipt_name TEXT NOT NULL DEFAULT '',
    receipt_orig TEXT NOT NULL DEFAULT '',
    created_by   INTEGER REFERENCES users(id),
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses (date);

CREATE TABLE IF NOT EXISTS mileage_trips (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT NOT NULL,
    entity_id  INTEGER NOT NULL REFERENCES expense_lists(id),
    vehicle_id INTEGER REFERENCES expense_lists(id),
    from_place TEXT NOT NULL DEFAULT '',
    to_place   TEXT NOT NULL DEFAULT '',
    miles      REAL NOT NULL,
    round_trip INTEGER NOT NULL DEFAULT 0,
    notes      TEXT NOT NULL DEFAULT '',
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_trips_date ON mileage_trips (date);

CREATE TABLE IF NOT EXISTS contacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    company    TEXT NOT NULL DEFAULT '',
    title      TEXT NOT NULL DEFAULT '',
    email      TEXT NOT NULL DEFAULT '',
    phone      TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS leads (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    stage      TEXT NOT NULL DEFAULT 'new'
               CHECK (stage IN ('new', 'contacted', 'qualified', 'proposal', 'won', 'lost')),
    entity_id  INTEGER REFERENCES expense_lists(id),
    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    source     TEXT NOT NULL DEFAULT '',
    follow_up  TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '',
    closed_at  TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS challenges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL CHECK (kind IN ('quiz', 'puzzle', 'scenario')),
    question    TEXT NOT NULL,
    options     TEXT NOT NULL DEFAULT '',
    answer      TEXT NOT NULL,
    explanation TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS challenge_attempts (
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day          TEXT NOT NULL,
    challenge_id INTEGER NOT NULL REFERENCES challenges(id) ON DELETE CASCADE,
    answer       TEXT NOT NULL,
    correct      INTEGER NOT NULL,
    answered_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, day)
);

CREATE TABLE IF NOT EXISTS projects (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'planned'
               CHECK (status IN ('planned', 'active', 'hold', 'done')),
    entity_id  INTEGER REFERENCES expense_lists(id),
    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    lead_id    INTEGER REFERENCES leads(id) ON DELETE SET NULL,
    due_date   TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '',
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

TICKER_DEFAULTS = [
    ("^DJI", "Dow Jones"),
    ("AAPL", "Apple"),
    ("MSFT", "Microsoft"),
    ("NVDA", "NVIDIA"),
    ("GOOGL", "Alphabet"),
    ("AMZN", "Amazon"),
    ("META", "Meta Platforms"),
    ("TSLA", "Tesla"),
    ("AVGO", "Broadcom"),
    ("ORCL", "Oracle"),
    ("AMD", "AMD"),
    ("CRM", "Salesforce"),
    ("INTC", "Intel"),
]

EXPENSE_LIST_DEFAULTS = {
    "entity": ["Stone-Knight", "Rustic Saw", "National Guard"],
    "vehicle": ["2025 F-150", "1996 F-150"],
    "category": ["Fuel", "Maintenance & Repairs", "Insurance", "Supplies", "Tools & Equipment",
                 "Software & Subscriptions", "Meals", "Travel & Lodging", "Office",
                 "Phone & Internet", "Training & Certifications", "Fees & Licenses", "Other"],
    "payment": ["Business Card", "Personal Card", "Cash", "Check", "Bank Transfer"],
}


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        # Migration for databases created before folders existed.
        for table in ("docs", "files"):
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            if "folder_id" not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN folder_id INTEGER REFERENCES folders(id)")
        if not conn.execute("SELECT 1 FROM ticker_symbols LIMIT 1").fetchone():
            conn.executemany("INSERT INTO ticker_symbols (symbol, name) VALUES (?, ?)",
                             TICKER_DEFAULTS)
        for kind, names in EXPENSE_LIST_DEFAULTS.items():
            if conn.execute("SELECT 1 FROM expense_lists WHERE kind = ? LIMIT 1", (kind,)).fetchone():
                continue
            conn.executemany("INSERT INTO expense_lists (kind, name) VALUES (?, ?)",
                             [(kind, n) for n in names])
        if not conn.execute("SELECT 1 FROM challenges LIMIT 1").fetchone():
            import json

            from .challenge_bank import CHALLENGES
            conn.executemany(
                "INSERT INTO challenges (kind, question, options, answer, explanation) VALUES (?, ?, ?, ?, ?)",
                [(kind, q, json.dumps(opts) if opts else "", ans, expl)
                 for kind, q, opts, ans, expl in CHALLENGES])
        conn.commit()
    finally:
        conn.close()


def query(sql, args=()):
    conn = connect()
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def query_one(sql, args=()):
    conn = connect()
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


def execute(sql, args=()):
    conn = connect()
    try:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()
