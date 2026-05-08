import sqlite3
import time
from typing import Optional

DB_PATH = "/home/daniel/bbs/meshsentinel/bbs_messages.sqlite"


def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    cur.execute("PRAGMA journal_mode=WAL;")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        node_id TEXT,
        author TEXT,
        body TEXT NOT NULL,
        created_ts INTEGER NOT NULL
    )
    """)

    con.commit()
    con.close()


def post_message(node_id: str, author: Optional[str], body: str):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    cur.execute("""
        INSERT INTO messages(node_id, author, body, created_ts)
        VALUES (?, ?, ?, ?)
    """, (node_id, author, body, int(time.time())))

    msg_id = cur.lastrowid
    con.commit()
    con.close()
    return msg_id


def delete_message(msg_id: int, node_id: str):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    cur.execute(
        "DELETE FROM messages WHERE id=? AND node_id=?",
        (msg_id, node_id),
    )

    count = cur.rowcount
    con.commit()
    con.close()
    return count > 0


def list_messages(limit=20):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    cur.execute("""
        SELECT id, author, created_ts
        FROM messages
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))

    rows = cur.fetchall()
    con.close()
    return rows


def get_message(msg_id: int):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    cur.execute("""
        SELECT id, author, body, created_ts
        FROM messages
        WHERE id=?
    """, (msg_id,))

    row = cur.fetchone()
    con.close()
    return row
