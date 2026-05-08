import sqlite3
import time
from typing import Optional

DB_PATH = "/home/daniel/bbs/meshsentinel/bbs_users.sqlite"


def _now() -> int:
    return int(time.time())


def init_db() -> None:
    con = sqlite3.connect(DB_PATH, timeout=3)
    cur = con.cursor()

    # Mejor para escrituras frecuentes
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA synchronous=NORMAL;")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS bbs_visits (
        node_id TEXT PRIMARY KEY,

        short_name TEXT,
        long_name TEXT,

        first_seen_ts INTEGER NOT NULL,
        last_seen_ts INTEGER NOT NULL,

        visits_count INTEGER NOT NULL DEFAULT 0,
        last_message TEXT
    )
    """)

    con.commit()
    con.close()


def record_visit(
    node_id: str,
    short_name: Optional[str] = None,
    long_name: Optional[str] = None,
    last_message: Optional[str] = None,
) -> None:
    """
    Registra una visita al BBS (cada DM procesado).
    - Incrementa visits_count
    - Actualiza last_seen_ts
    - Guarda short/long name si vienen (sin pisar con None)
    - Guarda last_message (truncado recomendado desde el caller)
    """
    ts = _now()

    con = sqlite3.connect(DB_PATH, timeout=3)
    cur = con.cursor()

    cur.execute("""
    INSERT INTO bbs_visits(
        node_id, short_name, long_name,
        first_seen_ts, last_seen_ts,
        visits_count, last_message
    )
    VALUES (?, ?, ?, ?, ?, 1, ?)
    ON CONFLICT(node_id) DO UPDATE SET
        last_seen_ts = excluded.last_seen_ts,
        visits_count = bbs_visits.visits_count + 1,
        last_message = excluded.last_message,
        short_name = COALESCE(excluded.short_name, bbs_visits.short_name),
        long_name  = COALESCE(excluded.long_name,  bbs_visits.long_name)
    """, (
        node_id,
        short_name,
        long_name,
        ts,
        ts,
        last_message
    ))

    con.commit()
    con.close()

def last_visits(limit: int = 20):
    con = sqlite3.connect(DB_PATH, timeout=3)
    cur = con.cursor()
    cur.execute("""
        SELECT node_id,
               COALESCE(short_name,'-') AS short,
               COALESCE(long_name,'-') AS name,
               visits_count,
               first_seen_ts,
               last_seen_ts,
               COALESCE(last_message,'') AS last_message
        FROM bbs_visits
        ORDER BY last_seen_ts DESC
        LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    con.close()
    return rows


def top_visits(limit: int = 20):
    con = sqlite3.connect(DB_PATH, timeout=3)
    cur = con.cursor()
    cur.execute("""
        SELECT node_id,
               COALESCE(short_name,'-') AS short,
               COALESCE(long_name,'-') AS name,
               visits_count,
               first_seen_ts,
               last_seen_ts
        FROM bbs_visits
        ORDER BY visits_count DESC, last_seen_ts DESC
        LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    con.close()
    return rows


def seen(node_id: str):
    con = sqlite3.connect(DB_PATH, timeout=3)
    cur = con.cursor()
    cur.execute("""
        SELECT node_id,
               COALESCE(short_name,'-') AS short,
               COALESCE(long_name,'-') AS name,
               visits_count,
               first_seen_ts,
               last_seen_ts,
               COALESCE(last_message,'') AS last_message
        FROM bbs_visits
        WHERE node_id = ?
        LIMIT 1
    """, (node_id,))
    row = cur.fetchone()
    con.close()
    return row
