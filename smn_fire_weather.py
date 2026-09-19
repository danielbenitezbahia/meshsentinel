"""Weather enrichment for FIRMS events using official SMN WIS2/OGC API observations.

This module intentionally does NOT claim to calculate the official FWI.  It derives
an indicative, weather-only propagation potential from recent SMN observations
near a FIRMS event.  If SMN is unavailable or data are stale, callers simply omit
this enrichment; FIRMS alerting must continue to work independently.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import requests

logger = logging.getLogger(__name__)

SMN_OBSERVATIONS_URL = (
    "https://w2b.smn.gov.ar/oapi/collections/"
    "urn:wmo:md:ar-smn:slt0ci/items"
)

# Same regional envelope used by FIRMS, plus a small margin so border events can
# use a nearby SMN station just outside the operational polygon.
DEFAULT_BBOX = (-64.8, -41.8, -60.2, -36.2)  # west, south, east, north
DEFAULT_LOOKBACK_HOURS = 3
DEFAULT_MAX_DISTANCE_KM = 120.0
DEFAULT_MAX_AGE_HOURS = 3.0
DEFAULT_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class SMNWeatherObservation:
    station_id: str
    observed_at: datetime
    latitude: float
    longitude: float
    distance_km: float
    age_hours: float
    temperature_c: float
    dewpoint_c: float
    relative_humidity: float
    wind_speed_kmh: float
    wind_from_deg: Optional[float]
    propagation_to_deg: Optional[float]


@dataclass(frozen=True)
class FireWeatherAssessment:
    level: str
    score: int
    observation: SMNWeatherObservation

    def as_dict(self) -> dict:
        obs = self.observation
        return {
            "level": self.level,
            "score": self.score,
            "station_id": obs.station_id,
            "observed_at": obs.observed_at.isoformat(),
            "distance_km": round(obs.distance_km, 1),
            "age_hours": round(obs.age_hours, 2),
            "temperature_c": round(obs.temperature_c, 1),
            "relative_humidity": round(obs.relative_humidity),
            "wind_speed_kmh": round(obs.wind_speed_kmh),
            "wind_from_deg": None if obs.wind_from_deg is None else round(obs.wind_from_deg),
            "propagation_to_deg": None if obs.propagation_to_deg is None else round(obs.propagation_to_deg),
            "propagation_to": degrees_to_cardinal(obs.propagation_to_deg),
        }


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return None


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def relative_humidity_from_dewpoint(temp_c: float, dewpoint_c: float) -> float:
    """Magnus approximation, adequate for ordinary surface weather ranges."""
    a = 17.625
    b = 243.04
    numerator = math.exp((a * dewpoint_c) / (b + dewpoint_c))
    denominator = math.exp((a * temp_c) / (b + temp_c))
    return max(0.0, min(100.0, 100.0 * numerator / denominator))


def degrees_to_cardinal(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    labels = ("N", "NE", "E", "SE", "S", "SO", "O", "NO")
    return labels[int((value % 360 + 22.5) // 45) % 8]


def propagation_score(*, wind_kmh: float, rh: float, temperature_c: float) -> tuple[int, str]:
    """Return a transparent weather-only propagation score.

    This is deliberately NOT FWI.  It only summarizes three instantaneous
    meteorological drivers useful to an early-warning message:
      * wind speed (stronger wind -> faster potential spread),
      * relative humidity (lower RH -> drier fine fuels),
      * air temperature (higher temperature -> more adverse drying conditions).

    The buckets are conservative operational heuristics, kept explicit here so
    they can later be replaced by official/regional FWI classes without changing
    the rest of the FIRMS pipeline.
    """
    score = 0

    if wind_kmh >= 45:
        score += 4
    elif wind_kmh >= 30:
        score += 3
    elif wind_kmh >= 20:
        score += 2
    elif wind_kmh >= 10:
        score += 1

    if rh < 20:
        score += 4
    elif rh < 30:
        score += 3
    elif rh < 40:
        score += 2
    elif rh < 50:
        score += 1

    if temperature_c >= 35:
        score += 3
    elif temperature_c >= 30:
        score += 2
    elif temperature_c >= 25:
        score += 1

    if score >= 8:
        level = "MUY ALTA"
    elif score >= 5:
        level = "ALTA"
    elif score >= 3:
        level = "MODERADA"
    else:
        level = "BAJA"
    return score, level


class SMNFireWeatherSnapshot:
    def __init__(self, reports: list[dict], now: datetime, max_distance_km: float, max_age_hours: float):
        self.reports = reports
        self.now = _utc(now)
        self.max_distance_km = max_distance_km
        self.max_age_hours = max_age_hours

    def assess(self, latitude: float, longitude: float) -> Optional[FireWeatherAssessment]:
        candidates: list[SMNWeatherObservation] = []
        for report in self.reports:
            values = report["values"]
            if "air_temperature" not in values or "dewpoint_temperature" not in values or "wind_speed" not in values:
                continue

            observed_at = report["observed_at"]
            age_hours = max(0.0, (self.now - observed_at).total_seconds() / 3600)
            if age_hours > self.max_age_hours:
                continue

            distance = _haversine(latitude, longitude, report["latitude"], report["longitude"])
            if distance > self.max_distance_km:
                continue

            temp = float(values["air_temperature"])
            dewpoint = float(values["dewpoint_temperature"])
            wind_ms = max(0.0, float(values["wind_speed"]))
            wind_from = values.get("wind_direction")
            if wind_from is not None:
                wind_from = float(wind_from) % 360
            # Meteorological wind direction is where wind comes FROM.  A simple
            # downwind propagation hint therefore points 180 degrees opposite.
            propagation_to = None if wind_from is None or wind_ms < 0.5 else (wind_from + 180.0) % 360

            candidates.append(
                SMNWeatherObservation(
                    station_id=report["station_id"],
                    observed_at=observed_at,
                    latitude=report["latitude"],
                    longitude=report["longitude"],
                    distance_km=distance,
                    age_hours=age_hours,
                    temperature_c=temp,
                    dewpoint_c=dewpoint,
                    relative_humidity=relative_humidity_from_dewpoint(temp, dewpoint),
                    wind_speed_kmh=wind_ms * 3.6,
                    wind_from_deg=wind_from,
                    propagation_to_deg=propagation_to,
                )
            )

        if not candidates:
            return None

        # Favor nearby stations, but penalize stale reports enough that a much
        # fresher station can beat a slightly closer one.
        best = min(candidates, key=lambda obs: obs.distance_km + obs.age_hours * 20.0)
        score, level = propagation_score(
            wind_kmh=best.wind_speed_kmh,
            rh=best.relative_humidity,
            temperature_c=best.temperature_c,
        )
        return FireWeatherAssessment(level=level, score=score, observation=best)


class SMNFireWeatherClient:
    def __init__(
        self,
        *,
        session: Optional[requests.Session] = None,
        bbox: tuple[float, float, float, float] = DEFAULT_BBOX,
        lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
        max_distance_km: float = DEFAULT_MAX_DISTANCE_KM,
        max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ):
        self.session = session or requests.Session()
        self.bbox = bbox
        self.lookback_hours = lookback_hours
        self.max_distance_km = max_distance_km
        self.max_age_hours = max_age_hours
        self.timeout_seconds = timeout_seconds
        self.enabled = os.getenv("FIRMS_SMN_WEATHER_ENABLED", "0").strip().lower() not in {"0", "false", "no", "off"}

    def _request_pages(self, params: dict) -> Iterable[dict]:
        url = SMN_OBSERVATIONS_URL
        pages = 0
        while url and pages < 2:
            response = self.session.get(url, params=params if pages == 0 else None, timeout=self.timeout_seconds)
            response.raise_for_status()
            data = response.json()
            yield data
            pages += 1
            url = None
            for link in data.get("links", []):
                if link.get("rel") == "next" and link.get("href"):
                    url = link["href"]
                    break

    def fetch_snapshot(self, now: Optional[datetime] = None) -> Optional[SMNFireWeatherSnapshot]:
        if not self.enabled:
            return None
        now = _utc(now or datetime.now(timezone.utc))
        start = now - timedelta(hours=self.lookback_hours)
        west, south, east, north = self.bbox
        params = {
            "f": "json",
            "bbox": f"{west},{south},{east},{north}",
            "datetime": f"{start.isoformat().replace('+00:00', 'Z')}/{now.isoformat().replace('+00:00', 'Z')}",
            "limit": 1000,
        }

        try:
            features: list[dict] = []
            for page in self._request_pages(params):
                features.extend(page.get("features", []))
            reports = self._group_reports(features)
            logger.info("SMN fire weather snapshot: features=%d reports=%d", len(features), len(reports))
            return SMNFireWeatherSnapshot(reports, now, self.max_distance_km, self.max_age_hours)
        except Exception as exc:
            # Fail-soft by design: an SMN outage must never stop FIRMS.
            logger.warning("SMN fire weather unavailable; FIRMS will continue without meteo enrichment: %s", exc)
            return None

    @staticmethod
    def _group_reports(features: list[dict]) -> list[dict]:
        grouped: dict[tuple[str, str], dict] = {}
        for feature in features:
            props = feature.get("properties") or {}
            name = props.get("name")
            if name not in {"air_temperature", "dewpoint_temperature", "wind_speed", "wind_direction"}:
                continue
            station_id = props.get("wigos_station_identifier")
            report_id = props.get("reportId") or props.get("report_id")
            report_time = _parse_dt(props.get("reportTime") or props.get("report_time") or props.get("phenomenonTime"))
            geometry = feature.get("geometry") or {}
            coords = geometry.get("coordinates") or []
            if not station_id or not report_id or report_time is None or len(coords) < 2:
                continue
            try:
                lon = float(coords[0])
                lat = float(coords[1])
                value = float(props.get("value"))
            except (TypeError, ValueError):
                continue

            key = (str(station_id), str(report_id))
            report = grouped.setdefault(
                key,
                {
                    "station_id": str(station_id),
                    "report_id": str(report_id),
                    "observed_at": report_time,
                    "latitude": lat,
                    "longitude": lon,
                    "values": {},
                },
            )
            report["values"][name] = value
            if report_time > report["observed_at"]:
                report["observed_at"] = report_time

        # Keep only the newest report for each station; older reports are useful
        # only if the latest one is incomplete.  Prefer the latest complete one.
        by_station: dict[str, list[dict]] = {}
        for report in grouped.values():
            by_station.setdefault(report["station_id"], []).append(report)

        result: list[dict] = []
        required = {"air_temperature", "dewpoint_temperature", "wind_speed"}
        for station_reports in by_station.values():
            station_reports.sort(key=lambda item: item["observed_at"], reverse=True)
            complete = next((r for r in station_reports if required.issubset(r["values"])), None)
            if complete:
                result.append(complete)
        return result
