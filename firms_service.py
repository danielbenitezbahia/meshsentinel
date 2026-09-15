import json
import logging
import math
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from firms_client import FirmsClient, FirmsDetection
from firms_geo import FirmsGeoFilter
from firms_notifier import (
    ACTION_TEXT,
    ACTION_WAYPOINT_CREATE,
    ACTION_WAYPOINT_UPDATE,
    build_text_payload,
    build_waypoint_payload,
)

logger = logging.getLogger(__name__)

DB_PATH = "firms.sqlite"

LEVEL_CANDIDATE = 0
LEVEL_INITIAL = 1
LEVEL_REPEATED = 2
LEVEL_MULTISATELLITE = 3

OBSERVATION_DISTANCE_KM = 1.0
OBSERVATION_WINDOW_MINUTES = 10
EVENT_DISTANCE_KM = 2.5
EVENT_MAX_GAP_HOURS = 14
BOOTSTRAP_ALERT_WINDOW_HOURS = 3
WAYPOINT_TTL_HOURS = 24

DETECTION_RETENTION_HOURS = 72
OBSERVATION_RETENTION_DAYS = 7
EVENT_RETENTION_DAYS = 30


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _parse_channel_indexes(value: Optional[str]) -> tuple[int, ...]:
    raw = value if value is not None else os.getenv("FIRMS_CHANNEL_INDEXES", "1,2")
    indexes: list[int] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            index = int(item)
        except ValueError:
            logger.warning("FIRMS: ignoring invalid channel index %r", item)
            continue
        if index not in indexes:
            indexes.append(index)
    return tuple(indexes or [1])


class FirmsService:
    def __init__(
        self,
        db_path: str = DB_PATH,
        geojson_path: str = "partidos.geojson",
        client: Optional[FirmsClient] = None,
        geo_filter: Optional[FirmsGeoFilter] = None,
        channel_indexes: Optional[tuple[int, ...]] = None,
    ):
        self.db_path = db_path
        self.client = client or FirmsClient()
        self.geo = geo_filter or FirmsGeoFilter(geojson_path)
        self.channel_indexes = channel_indexes or _parse_channel_indexes(None)
        self.init_db()

    @property
    def enabled(self) -> bool:
        return self.client.enabled

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS firms_detections (
                    fingerprint TEXT PRIMARY KEY,
                    satellite TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    confidence TEXT,
                    frp REAL,
                    daynight TEXT,
                    partido TEXT NOT NULL,
                    observation_id TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_firms_detections_acquired_at
                    ON firms_detections(acquired_at);
                CREATE INDEX IF NOT EXISTS idx_firms_detections_observation
                    ON firms_detections(observation_id);

                CREATE TABLE IF NOT EXISTS firms_observations (
                    observation_id TEXT PRIMARY KEY,
                    satellite TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    pixel_count INTEGER NOT NULL,
                    max_frp REAL NOT NULL,
                    has_high_confidence INTEGER NOT NULL DEFAULT 0,
                    partidos_json TEXT NOT NULL,
                    event_id TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_firms_observations_event
                    ON firms_observations(event_id);
                CREATE INDEX IF NOT EXISTS idx_firms_observations_acquired_at
                    ON firms_observations(acquired_at);

                CREATE TABLE IF NOT EXISTS firms_events (
                    event_id TEXT PRIMARY KEY,
                    waypoint_id INTEGER NOT NULL UNIQUE,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    latest_latitude REAL NOT NULL,
                    latest_longitude REAL NOT NULL,
                    partidos_json TEXT NOT NULL,
                    satellites_json TEXT NOT NULL,
                    pass_count INTEGER NOT NULL DEFAULT 1,
                    observation_count INTEGER NOT NULL DEFAULT 1,
                    total_pixels INTEGER NOT NULL DEFAULT 1,
                    max_pixels_in_observation INTEGER NOT NULL DEFAULT 1,
                    has_high_confidence INTEGER NOT NULL DEFAULT 0,
                    max_frp REAL NOT NULL DEFAULT 0,
                    current_level INTEGER NOT NULL DEFAULT 0,
                    communicated_level INTEGER NOT NULL DEFAULT 0,
                    ever_communicated INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    closed_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_firms_events_status_last_seen
                    ON firms_events(status, last_seen);

                CREATE TABLE IF NOT EXISTS firms_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    action_type TEXT NOT NULL,
                    channel_index INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    sent_at TEXT,
                    last_error TEXT,
                    UNIQUE(event_id, level, action_type, channel_index)
                );

                CREATE INDEX IF NOT EXISTS idx_firms_outbox_pending
                    ON firms_outbox(status, id);

                CREATE TABLE IF NOT EXISTS firms_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            conn.commit()

    def _meta_get(self, conn: sqlite3.Connection, key: str) -> Optional[str]:
        row = conn.execute("SELECT value FROM firms_meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def _meta_set(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            """INSERT INTO firms_meta(key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, value),
        )

    def _generate_waypoint_id(self, conn: sqlite3.Connection) -> int:
        while True:
            # Meshtastic generates waypoint ids below 1e9. Keep the same range.
            candidate = secrets.randbelow(999_999_999) + 1
            exists = conn.execute("SELECT 1 FROM firms_events WHERE waypoint_id = ?", (candidate,)).fetchone()
            if not exists:
                return candidate

    def _event_dict(self, row: sqlite3.Row) -> dict:
        return {
            "event_id": row["event_id"],
            "waypoint_id": row["waypoint_id"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "latest_latitude": row["latest_latitude"],
            "latest_longitude": row["latest_longitude"],
            "partidos": json.loads(row["partidos_json"]),
            "satellites": json.loads(row["satellites_json"]),
            "pass_count": row["pass_count"],
            "observation_count": row["observation_count"],
            "total_pixels": row["total_pixels"],
            "max_pixels_in_observation": row["max_pixels_in_observation"],
            "has_high_confidence": bool(row["has_high_confidence"]),
            "max_frp": row["max_frp"],
            "current_level": row["current_level"],
            "communicated_level": row["communicated_level"],
            "ever_communicated": bool(row["ever_communicated"]),
            "status": row["status"],
        }

    def _find_observation_match(self, conn: sqlite3.Connection, detection: FirmsDetection) -> Optional[str]:
        window_start = _iso(detection.acquired_at - timedelta(minutes=OBSERVATION_WINDOW_MINUTES))
        window_end = _iso(detection.acquired_at + timedelta(minutes=OBSERVATION_WINDOW_MINUTES))
        rows = conn.execute(
            """SELECT observation_id, acquired_at, latitude, longitude
               FROM firms_observations
               WHERE satellite = ? AND acquired_at BETWEEN ? AND ?""",
            (detection.satellite, window_start, window_end),
        ).fetchall()

        candidates = []
        for row in rows:
            distance = _haversine(detection.latitude, detection.longitude, row["latitude"], row["longitude"])
            if distance > OBSERVATION_DISTANCE_KM:
                continue
            minutes = abs((_dt(row["acquired_at"]) - detection.acquired_at).total_seconds()) / 60
            score = 0.8 * (distance / OBSERVATION_DISTANCE_KM) + 0.2 * (minutes / OBSERVATION_WINDOW_MINUTES)
            candidates.append((score, row["observation_id"]))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _recompute_observation(self, conn: sqlite3.Connection, observation_id: str) -> sqlite3.Row:
        rows = conn.execute(
            """SELECT acquired_at, latitude, longitude, confidence, frp, partido, satellite
               FROM firms_detections WHERE observation_id = ? ORDER BY acquired_at""",
            (observation_id,),
        ).fetchall()
        if not rows:
            raise RuntimeError(f"FIRMS observation {observation_id} has no detections")

        acquired_at = min(_dt(row["acquired_at"]) for row in rows)
        latitude = sum(row["latitude"] for row in rows) / len(rows)
        longitude = sum(row["longitude"] for row in rows) / len(rows)
        max_frp = max(float(row["frp"] or 0.0) for row in rows)
        has_high = any((row["confidence"] or "").lower() == "h" for row in rows)
        partidos = sorted({row["partido"] for row in rows})

        conn.execute(
            """UPDATE firms_observations
               SET acquired_at = ?, latitude = ?, longitude = ?, pixel_count = ?,
                   max_frp = ?, has_high_confidence = ?, partidos_json = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE observation_id = ?""",
            (
                _iso(acquired_at),
                latitude,
                longitude,
                len(rows),
                max_frp,
                1 if has_high else 0,
                json.dumps(partidos, ensure_ascii=False),
                observation_id,
            ),
        )
        return conn.execute("SELECT * FROM firms_observations WHERE observation_id = ?", (observation_id,)).fetchone()

    def _find_event_match(self, conn: sqlite3.Connection, observation: sqlite3.Row) -> Optional[str]:
        obs_time = _dt(observation["acquired_at"])
        rows = conn.execute("SELECT * FROM firms_events WHERE status = 'ACTIVE'").fetchall()
        candidates = []
        for row in rows:
            gap_hours = (obs_time - _dt(row["last_seen"])).total_seconds() / 3600
            if gap_hours < 0 or gap_hours >= EVENT_MAX_GAP_HOURS:
                continue
            distance = _haversine(
                observation["latitude"],
                observation["longitude"],
                row["latest_latitude"],
                row["latest_longitude"],
            )
            if distance > EVENT_DISTANCE_KM:
                continue
            score = 0.75 * (distance / EVENT_DISTANCE_KM) + 0.25 * (gap_hours / EVENT_MAX_GAP_HOURS)
            candidates.append((score, row["event_id"]))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _calculate_level(self, satellites: set[str], pass_count: int, max_pixels: int, has_high: bool) -> int:
        if len(satellites) >= 2:
            return LEVEL_MULTISATELLITE
        if pass_count >= 2:
            return LEVEL_REPEATED
        if max_pixels >= 2 or has_high:
            return LEVEL_INITIAL
        return LEVEL_CANDIDATE

    def _recompute_event(self, conn: sqlite3.Connection, event_id: str) -> None:
        observations = conn.execute(
            "SELECT * FROM firms_observations WHERE event_id = ? ORDER BY acquired_at, observation_id",
            (event_id,),
        ).fetchall()
        if not observations:
            return

        first_seen = min(_dt(row["acquired_at"]) for row in observations)
        last_seen = max(_dt(row["acquired_at"]) for row in observations)
        latest = max(observations, key=lambda row: _dt(row["acquired_at"]))
        partidos = sorted({p for row in observations for p in json.loads(row["partidos_json"])})
        satellites = {row["satellite"] for row in observations}
        passes = {(row["satellite"], row["acquired_at"]) for row in observations}
        total_pixels = sum(int(row["pixel_count"]) for row in observations)
        max_pixels = max(int(row["pixel_count"]) for row in observations)
        has_high = any(bool(row["has_high_confidence"]) for row in observations)
        max_frp = max(float(row["max_frp"] or 0.0) for row in observations)
        level = self._calculate_level(satellites, len(passes), max_pixels, has_high)

        conn.execute(
            """UPDATE firms_events SET
                   first_seen = ?, last_seen = ?, latest_latitude = ?, latest_longitude = ?,
                   partidos_json = ?, satellites_json = ?, pass_count = ?, observation_count = ?,
                   total_pixels = ?, max_pixels_in_observation = ?, has_high_confidence = ?,
                   max_frp = ?, current_level = ?, updated_at = CURRENT_TIMESTAMP
               WHERE event_id = ?""",
            (
                _iso(first_seen),
                _iso(last_seen),
                latest["latitude"],
                latest["longitude"],
                json.dumps(partidos, ensure_ascii=False),
                json.dumps(sorted(satellites)),
                len(passes),
                len(observations),
                total_pixels,
                max_pixels,
                1 if has_high else 0,
                max_frp,
                level,
                event_id,
            ),
        )

    def _create_event_for_observation(self, conn: sqlite3.Connection, observation: sqlite3.Row) -> str:
        event_id = str(uuid.uuid4())
        waypoint_id = self._generate_waypoint_id(conn)
        acquired_at = observation["acquired_at"]
        conn.execute(
            """INSERT INTO firms_events(
                   event_id, waypoint_id, first_seen, last_seen, latest_latitude, latest_longitude,
                   partidos_json, satellites_json, pass_count, observation_count, total_pixels,
                   max_pixels_in_observation, has_high_confidence, max_frp, current_level,
                   communicated_level, ever_communicated, status
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?, ?, 0, 0, 0, 'ACTIVE')""",
            (
                event_id,
                waypoint_id,
                acquired_at,
                acquired_at,
                observation["latitude"],
                observation["longitude"],
                observation["partidos_json"],
                json.dumps([observation["satellite"]]),
                observation["pixel_count"],
                observation["pixel_count"],
                observation["has_high_confidence"],
                observation["max_frp"],
            ),
        )
        conn.execute("UPDATE firms_observations SET event_id = ? WHERE observation_id = ?", (event_id, observation["observation_id"]))
        self._recompute_event(conn, event_id)
        return event_id

    def _close_expired(self, conn: sqlite3.Connection, now: datetime) -> int:
        cutoff = _iso(_as_utc(now) - timedelta(hours=EVENT_MAX_GAP_HOURS))
        cur = conn.execute(
            """UPDATE firms_events
               SET status = 'CLOSED', closed_at = ?, updated_at = CURRENT_TIMESTAMP
               WHERE status = 'ACTIVE' AND last_seen <= ?""",
            (_iso(now), cutoff),
        )
        return cur.rowcount

    def _insert_new_detection(self, conn: sqlite3.Connection, detection: FirmsDetection, partido: str) -> Optional[str]:
        exists = conn.execute("SELECT 1 FROM firms_detections WHERE fingerprint = ?", (detection.fingerprint,)).fetchone()
        if exists:
            return None

        conn.execute(
            """INSERT INTO firms_detections(
                   fingerprint, satellite, acquired_at, latitude, longitude, confidence, frp, daynight, partido
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                detection.fingerprint,
                detection.satellite,
                _iso(detection.acquired_at),
                detection.latitude,
                detection.longitude,
                detection.confidence,
                detection.frp,
                detection.daynight,
                partido,
            ),
        )

        observation_id = self._find_observation_match(conn, detection)
        if observation_id is None:
            observation_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO firms_observations(
                       observation_id, satellite, acquired_at, latitude, longitude,
                       pixel_count, max_frp, has_high_confidence, partidos_json
                   ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                (
                    observation_id,
                    detection.satellite,
                    _iso(detection.acquired_at),
                    detection.latitude,
                    detection.longitude,
                    detection.frp,
                    1 if detection.confidence == "h" else 0,
                    json.dumps([partido], ensure_ascii=False),
                ),
            )

        conn.execute(
            "UPDATE firms_detections SET observation_id = ? WHERE fingerprint = ?",
            (observation_id, detection.fingerprint),
        )
        observation = self._recompute_observation(conn, observation_id)

        event_id = observation["event_id"]
        if not event_id:
            event_id = self._find_event_match(conn, observation)
            if event_id:
                conn.execute("UPDATE firms_observations SET event_id = ? WHERE observation_id = ?", (event_id, observation_id))
                self._recompute_event(conn, event_id)
            else:
                event_id = self._create_event_for_observation(conn, observation)
        else:
            self._recompute_event(conn, event_id)

        return event_id

    def _queue_transition(self, conn: sqlite3.Connection, event_id: str, previous_level: int) -> bool:
        row = conn.execute("SELECT * FROM firms_events WHERE event_id = ?", (event_id,)).fetchone()
        if not row or row["status"] != "ACTIVE":
            return False
        event = self._event_dict(row)
        if event["current_level"] <= previous_level or event["current_level"] == LEVEL_CANDIDATE:
            return False

        # If bootstrap silently baselined an event, its first future radio message must still
        # look like an initial alert and create (not update) the waypoint.
        format_previous = previous_level if event["ever_communicated"] else LEVEL_CANDIDATE
        waypoint_action = ACTION_WAYPOINT_UPDATE if event["ever_communicated"] else ACTION_WAYPOINT_CREATE
        text_payload = json.dumps(build_text_payload(event, format_previous), ensure_ascii=False)
        waypoint_payload = json.dumps(build_waypoint_payload(event, WAYPOINT_TTL_HOURS), ensure_ascii=False)

        for channel_index in self.channel_indexes:
            conn.execute(
                """INSERT OR IGNORE INTO firms_outbox(
                       event_id, level, action_type, channel_index, payload_json
                   ) VALUES (?, ?, ?, ?, ?)""",
                (event_id, event["current_level"], ACTION_TEXT, channel_index, text_payload),
            )
            conn.execute(
                """INSERT OR IGNORE INTO firms_outbox(
                       event_id, level, action_type, channel_index, payload_json
                   ) VALUES (?, ?, ?, ?, ?)""",
                (event_id, event["current_level"], waypoint_action, channel_index, waypoint_payload),
            )

        conn.execute(
            """UPDATE firms_events
               SET communicated_level = ?, ever_communicated = 1, updated_at = CURRENT_TIMESTAMP
               WHERE event_id = ?""",
            (event["current_level"], event_id),
        )
        return True

    def _ingest_detections(self, conn: sqlite3.Connection, detections: list[FirmsDetection], now: datetime, emit: bool) -> dict:
        impacted: set[str] = set()
        previous_levels = {
            row["event_id"]: row["communicated_level"]
            for row in conn.execute("SELECT event_id, communicated_level FROM firms_events").fetchall()
        }
        new_count = 0
        sob_count = 0

        for detection in sorted(detections, key=lambda d: d.acquired_at):
            partido = self.geo.locate_point(detection.latitude, detection.longitude)
            if not partido:
                continue
            sob_count += 1
            self._close_expired(conn, detection.acquired_at)
            event_id = self._insert_new_detection(conn, detection, partido)
            if event_id:
                new_count += 1
                impacted.add(event_id)
                previous_levels.setdefault(event_id, LEVEL_CANDIDATE)

        self._close_expired(conn, now)
        queued = 0
        if emit:
            for event_id in impacted:
                if self._queue_transition(conn, event_id, previous_levels.get(event_id, LEVEL_CANDIDATE)):
                    queued += 1

        return {
            "fetched": len(detections),
            "inside_sob": sob_count,
            "new_detections": new_count,
            "impacted_events": len(impacted),
            "queued_events": queued,
        }

    def _bootstrap(self, conn: sqlite3.Connection, now: datetime) -> dict:
        detections = self.client.fetch_detections(now=now, rolling_hours=None)
        result = self._ingest_detections(conn, detections, now, emit=False)
        alerts = 0
        suppressed = 0
        rows = conn.execute("SELECT * FROM firms_events").fetchall()
        for row in rows:
            event = self._event_dict(row)
            if event["current_level"] == LEVEL_CANDIDATE:
                continue
            age_hours = (now - _dt(event["last_seen"])).total_seconds() / 3600
            if event["status"] == "ACTIVE" and age_hours <= BOOTSTRAP_ALERT_WINDOW_HOURS:
                if self._queue_transition(conn, event["event_id"], LEVEL_CANDIDATE):
                    alerts += 1
            else:
                conn.execute(
                    "UPDATE firms_events SET communicated_level = ?, updated_at = CURRENT_TIMESTAMP WHERE event_id = ?",
                    (event["current_level"], event["event_id"]),
                )
                suppressed += 1

        self._meta_set(conn, "bootstrap_completed", "1")
        self._meta_set(conn, "bootstrap_completed_at", _iso(now))
        result.update({"bootstrap": True, "bootstrap_alerts": alerts, "bootstrap_suppressed": suppressed})
        return result

    def poll(self, now: Optional[datetime] = None) -> dict:
        now = _as_utc(now or _utc_now())
        if not self.enabled:
            return {"enabled": False, "reason": "FIRMS_MAP_KEY not configured"}

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if self._meta_get(conn, "bootstrap_completed") != "1":
                result = self._bootstrap(conn, now)
            else:
                detections = self.client.fetch_detections(now=now, rolling_hours=24)
                result = self._ingest_detections(conn, detections, now, emit=True)
                result["bootstrap"] = False
            conn.commit()
        result["enabled"] = True
        return result

    def close_expired_events(self, now: Optional[datetime] = None) -> int:
        now = _as_utc(now or _utc_now())
        with self._connect() as conn:
            count = self._close_expired(conn, now)
            conn.commit()
            return count

    def get_pending_outbox(self, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM firms_outbox WHERE status = 'PENDING' ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def mark_outbox_sent(self, action_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE firms_outbox SET status = 'SENT', sent_at = CURRENT_TIMESTAMP,
                   attempts = attempts + 1, last_error = NULL WHERE id = ?""",
                (action_id,),
            )
            conn.commit()

    def mark_outbox_failed(self, action_id: int, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE firms_outbox SET attempts = attempts + 1, last_error = ?
                   WHERE id = ?""",
                ((error or "")[:500], action_id),
            )
            conn.commit()

    def prune_old_data(self, now: Optional[datetime] = None) -> dict:
        now = _as_utc(now or _utc_now())
        detection_cutoff = _iso(now - timedelta(hours=DETECTION_RETENTION_HOURS))
        observation_cutoff = _iso(now - timedelta(days=OBSERVATION_RETENTION_DAYS))
        event_cutoff = _iso(now - timedelta(days=EVENT_RETENTION_DAYS))
        with self._connect() as conn:
            det = conn.execute("DELETE FROM firms_detections WHERE acquired_at < ?", (detection_cutoff,)).rowcount
            obs = conn.execute("DELETE FROM firms_observations WHERE acquired_at < ?", (observation_cutoff,)).rowcount
            out = conn.execute("DELETE FROM firms_outbox WHERE status = 'SENT' AND sent_at < ?", (event_cutoff,)).rowcount
            evt = conn.execute("DELETE FROM firms_events WHERE status = 'CLOSED' AND closed_at < ?", (event_cutoff,)).rowcount
            conn.commit()
        return {"detections": det, "observations": obs, "outbox": out, "events": evt}

    # Small read helpers intentionally exposed for diagnostics/tests.
    def list_events(self) -> list[dict]:
        with self._connect() as conn:
            return [self._event_dict(row) for row in conn.execute("SELECT * FROM firms_events ORDER BY first_seen").fetchall()]

    def count_rows(self, table: str) -> int:
        allowed = {"firms_detections", "firms_observations", "firms_events", "firms_outbox"}
        if table not in allowed:
            raise ValueError("invalid FIRMS table")
        with self._connect() as conn:
            return conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
