import sqlite3
from datetime import datetime

DB_NAME = "agent_memory.db"


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_base (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT UNIQUE,
            summary TEXT,
            timestamp TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS moltbook_queries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT,
            post_id TEXT,
            status TEXT DEFAULT 'pending',
            timestamp TEXT
        )
    """)

    conn.commit()
    conn.close()
    print("✅ Database tables ready.")


def _migrate_schema():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE moltbook_queries ADD COLUMN verification_status TEXT DEFAULT 'pending'")
        conn.commit()
        print("✅ Added verification_status column.")
    except sqlite3.OperationalError:
        pass  # already exists
    finally:
        conn.close()


def save_knowledge(subject, summary):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT OR REPLACE INTO knowledge_base (subject, summary, timestamp) VALUES (?, ?, ?)",
            (subject, summary, datetime.now().isoformat())
        )
        conn.commit()
    finally:
        conn.close()


def log_moltbook_post(question, post_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO moltbook_queries (question, post_id, timestamp) VALUES (?, ?, ?)",
        (question, post_id, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def update_moltbook_status(post_id, status):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE moltbook_queries SET verification_status = ? WHERE post_id = ?",
            (status, post_id)
        )
        conn.commit()
    finally:
        conn.close()


# Run on import
init_db()
_migrate_schema()