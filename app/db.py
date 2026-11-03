"""SQLite database setup — zero install, single file."""
import sqlite3, os
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "facility_report.db"
UPLOAD_DIR = BASE_DIR / "app" / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS categories (
        code TEXT PRIMARY KEY,
        portal_name TEXT NOT NULL,
        type TEXT NOT NULL,
        team_email TEXT,
        group_code TEXT
    );
    CREATE TABLE IF NOT EXISTS zones (
        code TEXT PRIMARY KEY,
        label TEXT NOT NULL,
        building TEXT,
        floor TEXT
    );
    CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        zone_code TEXT,
        asset_code TEXT,
        category_code TEXT,
        group_code TEXT,
        symptom TEXT,
        impact TEXT,
        priority TEXT DEFAULT 'P3',
        status TEXT DEFAULT 'NEW',
        description TEXT,
        photo_path TEXT,
        reporter_email TEXT,
        ai_confidence REAL,
        ai_predicted TEXT,
        created_at TIMESTAMP,
        updated_at TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS status_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER,
        from_status TEXT,
        to_status TEXT,
        note TEXT,
        created_at TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS training_examples (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        text TEXT NOT NULL,
        label TEXT NOT NULL,
        source TEXT DEFAULT 'officer_correction',
        created_at TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS analytics_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_name TEXT NOT NULL,
        page TEXT,
        ticket_code TEXT,
        metadata TEXT,
        created_at TIMESTAMP NOT NULL
    );
    CREATE TABLE IF NOT EXISTS ticket_compilations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        zone_code TEXT NOT NULL,
        group_code TEXT,
        status TEXT NOT NULL DEFAULT 'OPEN',
        created_at TIMESTAMP NOT NULL
    );
    CREATE TABLE IF NOT EXISTS compilation_members (
        compilation_id INTEGER NOT NULL,
        ticket_id INTEGER NOT NULL,
        similarity REAL NOT NULL,
        PRIMARY KEY (compilation_id, ticket_id)
    );
    """)
    conn.commit()
    conn.close()

def seed_lookups():
    """Load categories.csv and zones.csv if tables are empty."""
    import csv
    conn = get_conn()
    cur = conn.cursor()
    if cur.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
        with open(BASE_DIR / "categories.csv", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                cur.execute(
                    "INSERT INTO categories (code,portal_name,type,team_email,group_code) VALUES (?,?,?,?,?)",
                    (row["code"], row["portal_name"], row["type"], row["team_email_placeholder"], row.get("group_code",""))
                )
    if cur.execute("SELECT COUNT(*) FROM zones").fetchone()[0] == 0:
        zones = [
            ("YKM-4-CONC-MAIN", "Main concourse corridor", "YKM", "4"),
            ("YKM-4-CONC-L1", "LT1 · Lecture Theatre 1 (4/F)", "YKM", "4"),
            ("YKM-4-CONC-L2", "LT2 · Lecture Theatre 2 (4/F)", "YKM", "4"),
            ("YKM-4-CONC-L3", "LT3 · Lecture Theatre 3 (4/F)", "YKM", "4"),
            ("YKM-4-CONC-L4", "LT4 · Lecture Theatre 4 (4/F)", "YKM", "4"),
            ("YKM-4-CONC-L5", "LT5 · Lecture Theatre 5 (4/F)", "YKM", "4"),
            ("YKM-4-CONC-L6", "LT6 · Lecture Theatre 6 (4/F)", "YKM", "4"),
            ("YKM-4-CONC-L7", "LT7 · Lecture Theatre 7 (4/F)", "YKM", "4"),
            ("YKM-4-CONC-L8", "LT8 · Lecture Theatre 8 (4/F)", "YKM", "4"),
            ("YKM-4-CONC-L9", "LT9 · Lecture Theatre 9 (4/F)", "YKM", "4"),
            ("YKM-4-CONC-LT10", "LT10 · Lecture Theatre 10 (4/F)", "YKM", "4"),
            ("YKM-4-CONC-LT11", "LT11 · Lecture Theatre 11 (4/F)", "YKM", "4"),
            ("YKM-4-CONC-LT12", "LT12 · Lecture Theatre 12 (4/F)", "YKM", "4"),
            ("YKM-4-CONC-LT13", "LT13 · Lecture Theatre 13 (4/F)", "YKM", "4"),
            ("YKM-4-CONC-LT14", "LT14 · Lecture Theatre 14 (4/F)", "YKM", "4"),
            ("YKM-4-CONC-LT15", "LT15 · Lecture Theatre 15 (4/F)", "YKM", "4"),
            ("YKM-4-CONC-LT16", "LT16 · Lecture Theatre 16 (4/F)", "YKM", "4"),
            ("YKM-4-CONC-LT17", "LT17 · Lecture Theatre 17 (4/F)", "YKM", "4"),
            ("YKM-4-CONC-LT18", "LT18 · Lecture Theatre 18 (4/F)", "YKM", "4"),
            ("YKM-4-CONC-COFFEE", "Coffee cart area", "YKM", "4"),
            ("YKM-4-CONC-TOILET", "Toilets on 4/F", "YKM", "4"),
            ("YKM-4-CONC-L13", "Link toward BOC Complex (L13)", "YKM", "4"),
            ("YKM-4-CONC-L1617", "Link toward To Yuen (L16/L17)", "YKM", "4"),
        ]
        cur.executemany("INSERT INTO zones VALUES (?,?,?,?)", zones)
    conn.commit()
    conn.close()

def update_zone_labels():
    """Refresh labels while preserving the stable zone codes used by existing tickets."""
    conn = get_conn()
    for n in range(1, 10):
        code = f"YKM-4-CONC-L{n}"
        conn.execute("UPDATE zones SET label=? WHERE code=?", (f"LT{n} · Lecture Theatre {n} (4/F)", code))
    for n in range(10, 19):
        code = f"YKM-4-CONC-LT{n}"
        conn.execute("INSERT OR IGNORE INTO zones (code,label,building,floor) VALUES (?,?,?,?)",
                     (code, f"LT{n} · Lecture Theatre {n} (4/F)", "YKM", "4"))
    conn.commit()
    conn.close()

def next_ticket_code():
    year = datetime.now().year
    conn = get_conn()
    cur = conn.cursor()
    row = cur.execute("SELECT COUNT(*) FROM tickets WHERE code LIKE ?", (f"FM-{year}-%",)).fetchone()
    n = row[0] + 1
    conn.close()
    return f"FM-{year}-{n:06d}"
