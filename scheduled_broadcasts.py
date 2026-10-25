"""Cola de envíos programados al canal MediumFast, armados por los 3 nodos
de confianza (comando 'programar'). A diferencia de announcement.py (un
solo aviso vigente a la vez), acá pueden coexistir varios programados
simultáneamente -- es una cola real, no un slot que se pisa.
"""
import sqlite3
import time
from typing import List, Optional, Tuple

DB_PATH = "scheduled_broadcasts.sqlite"
DELIVERY_INTERVAL_SECONDS = 60  # cada cuanto tick() revisa si hay algo vencido


def now_ts() -> int:
    return int(time.time())


def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS queue (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      message TEXT NOT NULL,
      scheduled_at_ts INTEGER NOT NULL,
      scheduled_at_display TEXT NOT NULL,
      created_by TEXT NOT NULL,
      created_at INTEGER NOT NULL,
      sent_at INTEGER
    )
    """)
    con.commit()
    con.close()


def enqueue(message: str, scheduled_at_ts: int, scheduled_at_display: str, created_by: str) -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        """INSERT INTO queue(message, scheduled_at_ts, scheduled_at_display, created_by, created_at)
           VALUES (?,?,?,?,?)""",
        (message, scheduled_at_ts, scheduled_at_display, created_by, now_ts()),
    )
    con.commit()
    qid = cur.lastrowid
    con.close()
    return qid


def due(limit: int = 50, now: Optional[int] = None) -> List[Tuple[int, str, str]]:
    """Envíos pendientes (sent_at IS NULL) cuya hora ya llegó."""
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        """SELECT id, message, scheduled_at_display FROM queue
           WHERE sent_at IS NULL AND scheduled_at_ts <= ?
           ORDER BY scheduled_at_ts ASC LIMIT ?""",
        (now if now is not None else now_ts(), limit),
    )
    rows = cur.fetchall()
    con.close()
    return rows


def pending(limit: int = 50) -> List[Tuple[int, str, str, str]]:
    """Todos los envíos todavía no entregados, ordenados por fecha."""
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        """SELECT id, message, scheduled_at_display, created_by FROM queue
           WHERE sent_at IS NULL
           ORDER BY scheduled_at_ts ASC LIMIT ?""",
        (limit,),
    )
    rows = cur.fetchall()
    con.close()
    return rows


def mark_sent(qid: int):
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE queue SET sent_at=? WHERE id=?", (now_ts(), qid))
    con.commit()
    con.close()


def cancel(qid: int) -> bool:
    """Borra un envío todavía no entregado. Devuelve False si no existía
    o ya se había enviado."""
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("DELETE FROM queue WHERE id=? AND sent_at IS NULL", (qid,))
    deleted = cur.rowcount > 0
    con.commit()
    con.close()
    return deleted
