import sqlite3
from datetime import datetime

DB_NAME = "agent_memory.db"


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Stores daily topics and the agent's synthesised understanding
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS knowledge_base (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT UNIQUE,
            summary TEXT,
            timestamp TEXT
        )
    ''')

    # Stores pending questions posted to Moltbook to check for answers later
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS moltbook_queries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT,
            post_id TEXT,
            status TEXT DEFAULT 'pending',
            timestamp TEXT
        )
    ''')

    conn.commit()
    conn.close()
    print("✅ Database tables ready.")


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