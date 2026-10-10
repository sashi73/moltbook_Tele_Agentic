import sqlite3
import os

DB_NAME = "agent_memory.db"
SUBJECT_WIDTH = 45
ANSWER_WIDTH = 70


def truncate(text, width):
    if text is None:
        return ""
    text = str(text).replace("\n", " ").strip()
    return text if len(text) <= width else text[:width - 3] + "..."


def list_knowledge(conn, limit=20):
    print("=" * 120)
    print(f"KNOWLEDGE BASE (last {limit})")
    print("=" * 120)
    print(f"{'ID':<4} | {'SUBJECT':<{SUBJECT_WIDTH}} | {'ANSWER (truncated)':<{ANSWER_WIDTH}}")
    print("-" * 120)

    rows = conn.execute(
        "SELECT id, subject, summary FROM knowledge_base ORDER BY id DESC LIMIT ?",
        (limit,)
    ).fetchall()

    if not rows:
        print("  (empty)")
        return

    for rid, subject, summary in rows:
        subj = truncate(subject, SUBJECT_WIDTH)
        ans = truncate(summary, ANSWER_WIDTH)
        print(f"{rid:<4} | {subj:<{SUBJECT_WIDTH}} | {ans:<{ANSWER_WIDTH}}")


def list_moltbook(conn, limit=20):
    print()
    print("=" * 120)
    print(f"MOLTBOOK QUERIES (last {limit})")
    print("=" * 120)
    print(f"{'ID':<4} | {'TITLE':<{SUBJECT_WIDTH}} | {'POST ID':<36} | {'STATUS':<10}")
    print("-" * 120)

    try:
        rows = conn.execute(
            "SELECT id, question, post_id, verification_status "
            "FROM moltbook_queries ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    except sqlite3.OperationalError:
        # verification_status column may not exist yet
        rows = conn.execute(
            "SELECT id, question, post_id, status "
            "FROM moltbook_queries ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()

    if not rows:
        print("  (empty)")
        return

    for rid, title, post_id, status in rows:
        t = truncate(title, SUBJECT_WIDTH)
        pid = truncate(post_id, 36)
        st = truncate(status or "pending", 10)
        print(f"{rid:<4} | {t:<{SUBJECT_WIDTH}} | {pid:<36} | {st:<10}")


def main():
    if not os.path.exists(DB_NAME):
        print(f"❌ Database not found: {os.path.abspath(DB_NAME)}")
        print("   Run from the folder that contains agent_memory.db.")
        return

    conn = sqlite3.connect(DB_NAME)
    try:
        list_knowledge(conn, limit=20)
        list_moltbook(conn, limit=20)
    finally:
        conn.close()


if __name__ == "__main__":
    main()