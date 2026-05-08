import sqlite3
import time
from dataclasses import dataclass
from typing import Optional, List, Tuple


DB_PATH = "store_forward.sqlite"
ONLINE_WINDOW_SECONDS = 10 * 60  # 10 min
DELIVERY_INTERVAL_SECONDS = 60   # cada 60s (podés subir a 300)


def now_ts() -> int:
    return int(time.time())


def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS queue (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      from_id TEXT NOT NULL,
      to_id TEXT NOT NULL,
      body TEXT NOT NULL,
      created_at INTEGER NOT NULL,
      delivered_at INTEGER,
      attempts INTEGER NOT NULL DEFAULT 0
    )
    """)
    con.commit()
    con.close()


def enqueue(from_id: str, to_id: str, body: str) -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "INSERT INTO queue(from_id,to_id,body,created_at) VALUES (?,?,?,?)",
        (from_id, to_id, body, now_ts())
    )
    con.commit()
    mid = cur.lastrowid
    con.close()
    return mid


def pending(limit: int = 20) -> List[Tuple[int, str, str, str, int]]:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
      SELECT id, from_id, to_id, body, attempts
      FROM queue
      WHERE delivered_at IS NULL
      ORDER BY id ASC
      LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    con.close()
    return rows


def mark_delivered(msg_id: int):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("UPDATE queue SET delivered_at=? WHERE id=?", (now_ts(), msg_id))
    con.commit()
    con.close()


def inc_attempts(msg_id: int):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("UPDATE queue SET attempts=attempts+1 WHERE id=?", (msg_id,))
    con.commit()
    con.close()


def normalize_node_id(node_id: str) -> str:
    # aseguramos formato "!abcd..."
    node_id = node_id.strip()
    if not node_id.startswith("!"):
        node_id = "!" + node_id
    return node_id.lower()


def node_id_to_int(node_id: str) -> int:
    return int(node_id.lstrip("!"), 16)


def is_online(serial_iface, to_id: str) -> bool:
    """
    serial_iface: instancia de meshtastic.serial_interface.SerialInterface
    """
    nodes = getattr(serial_iface, "nodes", None)
    if not nodes:
        return False

    # meshtastic suele usar keys tipo '!abcd'
    n = nodes.get(to_id)
    if not n:
        return False

    last = n.get("lastHeard") or n.get("lastHeardTimestamp") or n.get("last_heard")
    if not isinstance(last, (int, float)):
        return False

    return (now_ts() - int(last)) <= ONLINE_WINDOW_SECONDS
