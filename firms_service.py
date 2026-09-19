import hashlib
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
from sof_client import SofClient, SOF_STRONG_CONFIDENCE_CODES
from firms_geo import FirmsGeoFilter
from smn_fire_weather import SMNFireWeatherClient
from firms_notifier import (
    ACTION_TEXT,
    ACTION_TEXT_BURST,
    ACTION_WAYPOINT_CREATE,
    ACTION_WAYPOINT_UPDATE,
    ACTION_WAYPOINT_DELETE,
    build_text_payload,
    build_burst_text_payload,
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
GOES_OBSERVATION_DISTANCE_KM = 4.0
GOES_OBSERVATION_WINDOW_MINUTES = 3
EVENT_DISTANCE_KM = 2.5
GOES_EVENT_DISTANCE_KM = 5.0
EVENT_MAX_GAP_HOURS = 14
BOOTSTRAP_ALERT_WINDOW_HOURS = 3
WAYPOINT_TTL_HOURS = 24
BURST_SUMMARY_MIN_EVENTS = max(2, int(os.getenv("FIRMS_BURST_SUMMARY_MIN_EVENTS", "3")))

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


def _canonical_satellite(satellite: str) -> str:
    raw = (satellite or "").strip()
    upper = raw.upper().replace("_", " ")
    if upper == "N20" or "NOAA-20" in upper or "NOAA 20" in upper:
        return "NOAA-20"
    if upper == "N21" or "NOAA-21" in upper or "NOAA 21" in upper:
        return "NOAA-21"
    if upper in {"SNPP", "NPP"} or "SUOMI" in upper:
        return "Suomi NPP"
    if upper.startswith("GOES-"):
        return upper
    return raw


def _is_goes_satellite(satellite: str) -> bool:
    return _canonical_satellite(satellite).upper().startswith("GOES-")


def _is_high_confidence(value: Optional[str]) -> bool:
    confidence = (value or "").strip().lower()
    if confidence == "h":
        return True
    try:
        return int(confidence) in SOF_STRONG_CONFIDENCE_CODES
    except ValueError:
        return False


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
        sof_client: Optional[SofClient] = None,
        geo_filter: Optional[FirmsGeoFilter] = None,
        channel_indexes: Optional[tuple[int, ...]] = None,
        fire_weather_client: Optional[SMNFireWeatherClient] = None,
    ):
        self.db_path = db_path
        self.client = client or FirmsClient()
        self.sof_client = sof_client or SofClient()
        self.geo = geo_filter or FirmsGeoFilter(geojson_path)
        self.channel_indexes = channel_indexes or _parse_channel_indexes(None)
        self.fire_weather = fire_weather_client or SMNFireWeatherClient()
        self.init_db()

    @property
    def enabled(self) -> bool:
        return self.client.enabled or self.sof_client.enabled

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

            # Normalize observation satellite aliases already present in older DBs,
            # then recompute active events so N20/NOAA-20/VIIRS NOAA-20 do not
            # masquerade as different satellites.
            changed_events: list[tuple[str, int, int]] = []
            detection_rows = conn.execute(
                "SELECT fingerprint, satellite FROM firms_detections"
            ).fetchall()
            for det in detection_rows:
                canonical = _canonical_satellite(det["satellite"])
                if canonical and canonical != det["satellite"]:
                    conn.execute(
                        "UPDATE firms_detections SET satellite = ? WHERE fingerprint = ?",
                        (canonical, det["fingerprint"]),
                    )

            observation_rows = conn.execute(
                "SELECT observation_id, satellite FROM firms_observations"
            ).fetchall()
            for obs in observation_rows:
                canonical = _canonical_satellite(obs["satellite"])
                if canonical and canonical != obs["satellite"]:
                    conn.execute(
                        "UPDATE firms_observations SET satellite = ?, updated_at = CURRENT_TIMESTAMP WHERE observation_id = ?",
                        (canonical, obs["observation_id"]),
                    )

            active_rows = conn.execute(
                "SELECT event_id, current_level, communicated_level FROM firms_events WHERE status='ACTIVE'"
            ).fetchall()
            for event_row in active_rows:
                old_level = int(event_row["current_level"])
                old_communicated = int(event_row["communicated_level"])
                self._recompute_event(conn, event_row["event_id"])
                refreshed = conn.execute(
                    "SELECT current_level FROM firms_events WHERE event_id = ?",
                    (event_row["event_id"],),
                ).fetchone()
                new_level = int(refreshed["current_level"])
                if new_level < old_level and old_communicated > new_level:
                    conn.execute(
                        "UPDATE firms_events SET communicated_level = ? WHERE event_id = ?",
                        (new_level, event_row["event_id"]),
                    )
                    changed_events.append((event_row["event_id"], old_level, new_level))

            if changed_events:
                logger.warning(
                    "FIRMS normalized satellite aliases and corrected %s active event level(s): %s",
                    len(changed_events),
                    changed_events,
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
        canonical_satellite = _canonical_satellite(detection.satellite)
        is_goes = _is_goes_satellite(canonical_satellite)
        window_minutes = GOES_OBSERVATION_WINDOW_MINUTES if is_goes else OBSERVATION_WINDOW_MINUTES
        distance_limit = GOES_OBSERVATION_DISTANCE_KM if is_goes else OBSERVATION_DISTANCE_KM

        window_start = _iso(detection.acquired_at - timedelta(minutes=window_minutes))
        window_end = _iso(detection.acquired_at + timedelta(minutes=window_minutes))
        rows = conn.execute(
            """SELECT observation_id, acquired_at, latitude, longitude
               FROM firms_observations
               WHERE satellite = ? AND acquired_at BETWEEN ? AND ?""",
            (canonical_satellite, window_start, window_end),
        ).fetchall()

        candidates = []
        for row in rows:
            distance = _haversine(detection.latitude, detection.longitude, row["latitude"], row["longitude"])
            if distance > distance_limit:
                continue
            minutes = abs((_dt(row["acquired_at"]) - detection.acquired_at).total_seconds()) / 60
            score = 0.8 * (distance / distance_limit) + 0.2 * (minutes / window_minutes)
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
        has_high = any(_is_high_confidence(row["confidence"]) for row in rows)
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

            event_satellites = json.loads(row["satellites_json"])
            involves_goes = _is_goes_satellite(observation["satellite"]) or any(
                _is_goes_satellite(satellite) for satellite in event_satellites
            )
            distance_limit = GOES_EVENT_DISTANCE_KM if involves_goes else EVENT_DISTANCE_KM

            distance = _haversine(
                observation["latitude"],
                observation["longitude"],
                row["latest_latitude"],
                row["latest_longitude"],
            )
            if distance > distance_limit:
                continue
            score = 0.75 * (distance / distance_limit) + 0.25 * (gap_hours / EVENT_MAX_GAP_HOURS)
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

        # A single GOES scan is intentionally kept as CANDIDATE even if it contains
        # multiple pixels or one of the provisional strong SoF confidence codes.
        # We communicate it immediately as "possible", but require temporal
        # persistence (second scan) or another satellite before calling it confirmed.
        if any(_is_goes_satellite(satellite) for satellite in satellites):
            return LEVEL_CANDIDATE

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
        satellites = {_canonical_satellite(row["satellite"]) for row in observations}
        passes = {(_canonical_satellite(row["satellite"]), row["acquired_at"]) for row in observations}
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
                json.dumps([_canonical_satellite(observation["satellite"])]),
                observation["pixel_count"],
                observation["pixel_count"],
                observation["has_high_confidence"],
                observation["max_frp"],
            ),
        )
        conn.execute("UPDATE firms_observations SET event_id = ? WHERE observation_id = ?", (event_id, observation["observation_id"]))
        self._recompute_event(conn, event_id)
        return event_id

    def _queue_waypoint_delete(self, conn: sqlite3.Connection, event_row: sqlite3.Row) -> int:
        """Queue one idempotent waypoint delete per configured channel.

        Only events that actually communicated a waypoint need a delete packet.
        The existing outbox UNIQUE constraint makes repeated calls harmless.
        """
        if not bool(event_row["ever_communicated"]):
            return 0

        payload = json.dumps({"waypoint_id": int(event_row["waypoint_id"])})
        queued = 0
        for channel_index in self.channel_indexes:
            cur = conn.execute(
                """INSERT OR IGNORE INTO firms_outbox(
                       event_id, level, action_type, channel_index, payload_json
                   ) VALUES (?, ?, ?, ?, ?)""",
                (
                    event_row["event_id"],
                    int(event_row["current_level"]),
                    ACTION_WAYPOINT_DELETE,
                    channel_index,
                    payload,
                ),
            )
            queued += cur.rowcount
        return queued

    def _close_expired(self, conn: sqlite3.Connection, now: datetime) -> int:
        cutoff = _iso(_as_utc(now) - timedelta(hours=EVENT_MAX_GAP_HOURS))
        rows = conn.execute(
            """SELECT * FROM firms_events
               WHERE status = 'ACTIVE' AND last_seen <= ?""",
            (cutoff,),
        ).fetchall()

        closed_at = _iso(now)
        for row in rows:
            conn.execute(
                """UPDATE firms_events
                   SET status = 'CLOSED', closed_at = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE event_id = ? AND status = 'ACTIVE'""",
                (closed_at, row["event_id"]),
            )
            self._queue_waypoint_delete(conn, row)

        return len(rows)

    def _insert_new_detection(self, conn: sqlite3.Connection, detection: FirmsDetection, partido: str) -> Optional[str]:
        exists = conn.execute("SELECT 1 FROM firms_detections WHERE fingerprint = ?", (detection.fingerprint,)).fetchone()
        if exists:
            return None

        canonical_satellite = _canonical_satellite(detection.satellite)
        # Different upstreams sometimes spell the same instrument differently
        # (for example N20 vs VIIRS NOAA-20). If time and coordinates are the
        # same, treat it as the same physical pixel even if its raw fingerprint differs.
        alias_duplicate = conn.execute(
            """SELECT 1 FROM firms_detections
               WHERE satellite = ? AND acquired_at = ?
                 AND ABS(latitude - ?) < 0.000001
                 AND ABS(longitude - ?) < 0.000001
               LIMIT 1""",
            (canonical_satellite, _iso(detection.acquired_at), detection.latitude, detection.longitude),
        ).fetchone()
        if alias_duplicate:
            return None

        conn.execute(
            """INSERT INTO firms_detections(
                   fingerprint, satellite, acquired_at, latitude, longitude, confidence, frp, daynight, partido
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                detection.fingerprint,
                canonical_satellite,
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
                    _canonical_satellite(detection.satellite),
                    _iso(detection.acquired_at),
                    detection.latitude,
                    detection.longitude,
                    detection.frp,
                    1 if _is_high_confidence(detection.confidence) else 0,
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

    def _transition_needed(self, event: dict, previous_level: int) -> bool:
        goes_candidate = (
            event["current_level"] == LEVEL_CANDIDATE
            and bool(event["satellites"])
            and all(_is_goes_satellite(satellite) for satellite in event["satellites"])
        )
        first_possible_alert = goes_candidate and not event["ever_communicated"]
        if first_possible_alert:
            return True
        return event["current_level"] > previous_level and event["current_level"] != LEVEL_CANDIDATE

    def _is_pure_firms_event(self, event: dict) -> bool:
        return bool(event["satellites"]) and not any(
            _is_goes_satellite(satellite) for satellite in event["satellites"]
        )

    def _queue_burst_summary(self, conn: sqlite3.Connection, events: list[dict]) -> None:
        if not events:
            return
        payload = json.dumps(build_burst_text_payload(events), ensure_ascii=False)
        digest = hashlib.sha256(
            "|".join(sorted(event["event_id"] for event in events)).encode("utf-8")
        ).hexdigest()[:24]
        burst_event_id = f"burst:{digest}"
        level = max(int(event["current_level"]) for event in events)
        for channel_index in self.channel_indexes:
            conn.execute(
                """INSERT OR IGNORE INTO firms_outbox(
                       event_id, level, action_type, channel_index, payload_json
                   ) VALUES (?, ?, ?, ?, ?)""",
                (burst_event_id, level, ACTION_TEXT_BURST, channel_index, payload),
            )

    def _queue_transition(self, conn: sqlite3.Connection, event_id: str, previous_level: int, weather_snapshot=None, queue_text: bool = True) -> bool:
        row = conn.execute("SELECT * FROM firms_events WHERE event_id = ?", (event_id,)).fetchone()
        if not row or row["status"] != "ACTIVE":
            return False
        event = self._event_dict(row)

        # Early warning policy:
        # - first GOES-only candidate: communicate once as POSSIBLE;
        # - later promotion to REPEATED/MULTISATELLITE: communicate as confirmation/update;
        # - ordinary FIRMS candidates remain silent as before.
        if not self._transition_needed(event, previous_level):
            return False

        # If bootstrap silently baselined an event, its first future radio message must still
        # look like an initial alert and create (not update) the waypoint.
        format_previous = previous_level if event["ever_communicated"] else LEVEL_CANDIDATE
        waypoint_action = ACTION_WAYPOINT_UPDATE if event["ever_communicated"] else ACTION_WAYPOINT_CREATE

        fire_weather = None
        if weather_snapshot is not None:
            try:
                assessment = weather_snapshot.assess(event["latest_latitude"], event["latest_longitude"])
                if assessment is not None:
                    fire_weather = assessment.as_dict()
                    logger.info(
                        "FIRMS meteo enrichment event=%s level=%s wind=%skm/h rh=%s%% temp=%sC "
                        "station=%s distance=%skm age=%sh propagation_to=%s",
                        event_id,
                        fire_weather["level"],
                        fire_weather["wind_speed_kmh"],
                        fire_weather["relative_humidity"],
                        fire_weather["temperature_c"],
                        fire_weather["station_id"],
                        fire_weather["distance_km"],
                        fire_weather["age_hours"],
                        fire_weather.get("propagation_to"),
                    )
            except Exception:
                logger.exception("FIRMS meteo enrichment failed for event=%s; sending base alert", event_id)

        text_payload = json.dumps(build_text_payload(event, format_previous, fire_weather), ensure_ascii=False)
        waypoint_payload = json.dumps(build_waypoint_payload(event, WAYPOINT_TTL_HOURS), ensure_ascii=False)

        for channel_index in self.channel_indexes:
            if queue_text:
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

    def _queue_impacted_transitions(
        self,
        conn: sqlite3.Connection,
        event_ids: list[str],
        previous_levels: dict[str, int],
        weather_snapshot=None,
    ) -> int:
        eligible: list[tuple[str, dict, int]] = []
        for event_id in sorted(event_ids):
            row = conn.execute("SELECT * FROM firms_events WHERE event_id = ?", (event_id,)).fetchone()
            if not row or row["status"] != "ACTIVE":
                continue
            event = self._event_dict(row)
            previous = previous_levels.get(event_id, LEVEL_CANDIDATE)
            if self._transition_needed(event, previous):
                eligible.append((event_id, event, previous))

        pure_firms = [item for item in eligible if self._is_pure_firms_event(item[1])]
        burst_ids = {item[0] for item in pure_firms} if len(pure_firms) >= BURST_SUMMARY_MIN_EVENTS else set()

        queued = 0
        burst_events: list[dict] = []
        for event_id, event, previous in eligible:
            suppress_individual_text = event_id in burst_ids
            if self._queue_transition(
                conn,
                event_id,
                previous,
                weather_snapshot,
                queue_text=not suppress_individual_text,
            ):
                queued += 1
                if suppress_individual_text:
                    burst_events.append(event)

        if burst_events:
            self._queue_burst_summary(conn, burst_events)
            logger.info(
                "FIRMS burst summary queued: events=%s channels=%s",
                len(burst_events),
                self.channel_indexes,
            )

        return queued

    def _ingest_detections(self, conn: sqlite3.Connection, detections: list[FirmsDetection], now: datetime, emit: bool, weather_snapshot=None) -> dict:
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
            queued = self._queue_impacted_transitions(
                conn, list(impacted), previous_levels, weather_snapshot
            )

        return {
            "fetched": len(detections),
            "inside_sob": sob_count,
            "new_detections": new_count,
            "impacted_events": len(impacted),
            "queued_events": queued,
        }

    def _fetch_combined_detections(self, now: datetime, bootstrap: bool) -> tuple[list[FirmsDetection], dict]:
        detections: dict[str, FirmsDetection] = {}
        stats = {"firms": 0, "sof": 0}
        successful_sources = 0

        if self.client.enabled:
            try:
                firms = self.client.fetch_detections(
                    now=now,
                    rolling_hours=None if bootstrap else 24,
                )
                successful_sources += 1
                stats["firms"] = len(firms)
                for detection in firms:
                    detections[detection.fingerprint] = detection
            except Exception:
                # Preserve the existing bootstrap safety rule: if FIRMS is configured,
                # do not establish the first baseline while NASA is unreachable.
                if bootstrap:
                    raise
                logger.exception("FIRMS: fetch failed; continuing with other fire sources")

        if self.sof_client.enabled:
            try:
                sof = self.sof_client.fetch_detections(now=now)
                successful_sources += 1
                stats["sof"] = len(sof)
                for detection in sof:
                    detections[detection.fingerprint] = detection
            except Exception:
                logger.exception("SoF: fetch failed; continuing with other fire sources")

        enabled_sources = int(self.client.enabled) + int(self.sof_client.enabled)
        if enabled_sources and successful_sources == 0:
            raise RuntimeError("all configured fire data sources failed")

        return sorted(detections.values(), key=lambda d: d.acquired_at), stats

    def _bootstrap(self, conn: sqlite3.Connection, now: datetime, weather_snapshot=None) -> dict:
        detections, source_stats = self._fetch_combined_detections(now, bootstrap=True)
        result = self._ingest_detections(conn, detections, now, emit=False, weather_snapshot=weather_snapshot)
        result["sources"] = source_stats
        alerts = 0
        suppressed = 0
        recent_ids: list[str] = []
        previous_levels: dict[str, int] = {}
        rows = conn.execute("SELECT * FROM firms_events").fetchall()
        for row in rows:
            event = self._event_dict(row)
            age_hours = (now - _dt(event["last_seen"])).total_seconds() / 3600
            if event["status"] == "ACTIVE" and age_hours <= BOOTSTRAP_ALERT_WINDOW_HOURS:
                recent_ids.append(event["event_id"])
                previous_levels[event["event_id"]] = LEVEL_CANDIDATE
            else:
                conn.execute(
                    "UPDATE firms_events SET communicated_level = ?, updated_at = CURRENT_TIMESTAMP WHERE event_id = ?",
                    (event["current_level"], event["event_id"]),
                )
                suppressed += 1

        alerts = self._queue_impacted_transitions(
            conn, recent_ids, previous_levels, weather_snapshot
        )

        self._meta_set(conn, "bootstrap_completed", "1")
        self._meta_set(conn, "bootstrap_completed_at", _iso(now))
        result.update({"bootstrap": True, "bootstrap_alerts": alerts, "bootstrap_suppressed": suppressed})
        return result

    def poll(self, now: Optional[datetime] = None) -> dict:
        now = _as_utc(now or _utc_now())
        if not self.enabled:
            return {"enabled": False, "reason": "no fire data source configured (FIRMS_MAP_KEY / SOF_API_KEY)"}

        # Fetch SMN outside the SQLite write transaction.  If the official SMN
        # endpoint is unavailable, fetch_snapshot() fails soft and FIRMS keeps
        # operating exactly as before.
        weather_snapshot = self.fire_weather.fetch_snapshot(now=now)

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if self._meta_get(conn, "bootstrap_completed") != "1":
                result = self._bootstrap(conn, now, weather_snapshot=weather_snapshot)
            else:
                detections, source_stats = self._fetch_combined_detections(now, bootstrap=False)
                result = self._ingest_detections(
                    conn, detections, now, emit=True, weather_snapshot=weather_snapshot
                )
                result["sources"] = source_stats
                result["bootstrap"] = False
            conn.commit()
        result["enabled"] = True
        result["smn_weather"] = weather_snapshot is not None
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
