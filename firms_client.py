import csv
import hashlib
import io
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import requests

logger = logging.getLogger(__name__)

FIRMS_BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
FIRMS_SOURCES = ("VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT")
FIRMS_BBOX = (-64.5, -41.5, -60.5, -36.5)  # west, south, east, north
FIRMS_DAY_RANGE = 2
FIRMS_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class FirmsDetection:
    fingerprint: str
    satellite: str
    acquired_at: datetime
    latitude: float
    longitude: float
    confidence: str
    frp: float
    daynight: str


def _parse_acquired_at(acq_date: str, acq_time: str) -> datetime:
    hhmm = str(acq_time).strip().zfill(4)
    return datetime.strptime(f"{acq_date}{hhmm}", "%Y-%m-%d%H%M").replace(tzinfo=timezone.utc)


def build_fingerprint(satellite: str, acquired_at: datetime, latitude: float, longitude: float) -> str:
    raw = f"{satellite}|{acquired_at.astimezone(timezone.utc).isoformat()}|{latitude:.5f}|{longitude:.5f}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_csv(text: str) -> list[FirmsDetection]:
    result: list[FirmsDetection] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        if not row or not row.get("latitude") or not row.get("longitude"):
            continue
        try:
            acquired_at = _parse_acquired_at(row["acq_date"], row["acq_time"])
            latitude = float(row["latitude"])
            longitude = float(row["longitude"])
            satellite = (row.get("satellite") or "").strip()
            frp = float(row.get("frp") or 0.0)
            confidence = (row.get("confidence") or "").strip().lower()
            daynight = (row.get("daynight") or "").strip().upper()
        except (KeyError, TypeError, ValueError):
            logger.exception("FIRMS: invalid CSV row: %r", row)
            continue

        result.append(
            FirmsDetection(
                fingerprint=build_fingerprint(satellite, acquired_at, latitude, longitude),
                satellite=satellite,
                acquired_at=acquired_at,
                latitude=latitude,
                longitude=longitude,
                confidence=confidence,
                frp=frp,
                daynight=daynight,
            )
        )
    return result


class FirmsClient:
    def __init__(
        self,
        map_key: Optional[str] = None,
        sources: Iterable[str] = FIRMS_SOURCES,
        bbox: tuple[float, float, float, float] = FIRMS_BBOX,
        timeout_seconds: int = FIRMS_TIMEOUT_SECONDS,
        session=None,
    ):
        self.map_key = map_key if map_key is not None else os.getenv("FIRMS_MAP_KEY", "").strip()
        self.sources = tuple(sources)
        self.bbox = bbox
        self.timeout_seconds = timeout_seconds
        self.session = session or requests

    @property
    def enabled(self) -> bool:
        return bool(self.map_key)

    def _source_url(self, source: str, day_range: int = FIRMS_DAY_RANGE) -> str:
        west, south, east, north = self.bbox
        area = f"{west},{south},{east},{north}"
        return f"{FIRMS_BASE_URL}/{self.map_key}/{source}/{area}/{day_range}"

    def fetch_source(self, source: str, day_range: int = FIRMS_DAY_RANGE) -> list[FirmsDetection]:
        if not self.enabled:
            return []
        response = self.session.get(self._source_url(source, day_range), timeout=self.timeout_seconds)
        response.raise_for_status()
        return parse_csv(response.text)

    def fetch_detections(
        self,
        now: Optional[datetime] = None,
        rolling_hours: Optional[int] = 24,
        day_range: int = FIRMS_DAY_RANGE,
    ) -> list[FirmsDetection]:
        """Fetch all configured sources.

        rolling_hours=None returns the full FIRMS day_range response (used only for first bootstrap).
        Normal polling uses rolling_hours=24 because FIRMS /2 means calendar days, not rolling 24h.
        A failure in one satellite does not discard data from the other.
        """
        if not self.enabled:
            logger.warning("FIRMS disabled: FIRMS_MAP_KEY is not configured")
            return []

        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        cutoff = now - timedelta(hours=rolling_hours) if rolling_hours is not None else None

        detections: dict[str, FirmsDetection] = {}
        successful_sources = 0
        for source in self.sources:
            try:
                source_detections = self.fetch_source(source, day_range=day_range)
                successful_sources += 1
                for detection in source_detections:
                    if detection.acquired_at > now:
                        continue
                    if cutoff is not None and detection.acquired_at < cutoff:
                        continue
                    detections[detection.fingerprint] = detection
            except Exception:
                logger.exception("FIRMS: failed fetching source %s", source)

        if self.sources and successful_sources == 0:
            # Especially important on first bootstrap: do not mark the baseline as
            # complete if NASA was simply unreachable.
            raise RuntimeError("FIRMS: all configured sources failed")

        return sorted(detections.values(), key=lambda d: d.acquired_at)
