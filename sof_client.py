import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from firms_client import FirmsDetection

logger = logging.getLogger(__name__)

# Avoid noisy HTTP debug logs. In particular, never log x-api-key ourselves.
logging.getLogger("urllib3").setLevel(logging.WARNING)

SOF_GRAPHQL_URL = (
    "https://oe2b7fi45zazpkvcjfxzpbqhfq.appsync-api.us-east-1.amazonaws.com/graphql"
)
SOF_WINDOW_MINUTES = 65
SOF_LIMIT = 7500
SOF_TIMEOUT_SECONDS = 20

# GOES ABI Fire/Hot Spot mask classes that we treat as strong enough to map to
# the existing FIRMS high-confidence concept. Other classes remain candidates
# and can still be promoted by spatial multiplicity, repeated scans or another
# satellite.
SOF_STRONG_CONFIDENCE_CODES = {10, 11, 30, 31}

SOF_QUERY = """
query GetPublicWildfireByDateRangeNewIds(
  $startDate: AWSDateTime!,
  $endDate: AWSDateTime!,
  $limit: Int,
  $nextToken: String
) {
  getPublicWildfireByDateRangeNewIds(
    startDate: $startDate
    endDate: $endDate
    limit: $limit
    nextToken: $nextToken
  ) {
    items {
      cat
      conf
      date
      id
      sat
      x
      y
      __typename
    }
    nextToken
    __typename
  }
}
""".strip()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _aws_datetime(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_detection_time(detection_id: str) -> datetime:
    # Example: 2026-09-16T12:10:00+00:00_214505365
    timestamp, separator, _internal_id = detection_id.rpartition("_")
    if not separator or not timestamp:
        raise ValueError(f"invalid SoF detection id: {detection_id!r}")
    return _as_utc(datetime.fromisoformat(timestamp.replace("Z", "+00:00")))


def _normalize_satellite(value: str) -> str:
    raw = (value or "").strip().lower()
    if raw == "noaa-goes19":
        return "GOES-19"
    if raw == "noaa-goes18":
        return "GOES-18"
    if raw == "noaa-goes16":
        return "GOES-16"
    return (value or "SoF").strip() or "SoF"


def _fingerprint(detection_id: str) -> str:
    return hashlib.sha256(f"sof|{detection_id}".encode("utf-8")).hexdigest()


def _to_firms_detection(item: dict) -> FirmsDetection:
    detection_id = str(item["id"])
    acquired_at = _parse_detection_time(detection_id)
    confidence_code = int(item.get("conf"))

    # Keep the raw GOES mask code in the shared confidence field. FirmsService
    # understands both VIIRS 'h' and these numeric SoF/GOES codes.
    confidence = str(confidence_code)

    return FirmsDetection(
        fingerprint=_fingerprint(detection_id),
        satellite=_normalize_satellite(str(item.get("sat") or "")),
        acquired_at=acquired_at,
        latitude=float(item["y"]),
        longitude=float(item["x"]),
        confidence=confidence,
        frp=0.0,       # not exposed by this public SoF query
        daynight="",   # not exposed by this public SoF query
    )


class SofClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        window_minutes: int = SOF_WINDOW_MINUTES,
        limit: int = SOF_LIMIT,
        timeout_seconds: int = SOF_TIMEOUT_SECONDS,
        session=None,
    ):
        self.api_key = api_key if api_key is not None else os.getenv("SOF_API_KEY", "").strip()
        self.endpoint = endpoint or os.getenv("SOF_API_URL", SOF_GRAPHQL_URL).strip()
        self.window_minutes = max(1, int(window_minutes))
        self.limit = max(1, int(limit))
        self.timeout_seconds = timeout_seconds
        self.session = session or requests

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.endpoint)

    def fetch_detections(
        self,
        now: Optional[datetime] = None,
        window_minutes: Optional[int] = None,
    ) -> list[FirmsDetection]:
        if not self.enabled:
            logger.debug("SoF disabled: SOF_API_KEY is not configured")
            return []

        now = _as_utc(now or datetime.now(timezone.utc))
        minutes = self.window_minutes if window_minutes is None else max(1, int(window_minutes))
        start = now - timedelta(minutes=minutes)

        detections: dict[str, FirmsDetection] = {}
        next_token: Optional[str] = None

        while True:
            variables = {
                "startDate": _aws_datetime(start),
                "endDate": _aws_datetime(now),
                "limit": self.limit,
            }
            if next_token:
                variables["nextToken"] = next_token

            payload = {
                "operationName": "GetPublicWildfireByDateRangeNewIds",
                "variables": variables,
                "query": SOF_QUERY,
            }

            response = self.session.post(
                self.endpoint,
                headers={
                    "content-type": "application/json",
                    "x-api-key": self.api_key,
                },
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()

            errors = body.get("errors") or []
            if errors:
                messages = "; ".join(str(error.get("message") or error) for error in errors)
                raise RuntimeError(f"SoF GraphQL error: {messages}")

            connection = (body.get("data") or {}).get("getPublicWildfireByDateRangeNewIds")
            if connection is None:
                raise RuntimeError("SoF GraphQL response missing getPublicWildfireByDateRangeNewIds")

            for item in connection.get("items") or []:
                try:
                    detection = _to_firms_detection(item)
                except (KeyError, TypeError, ValueError):
                    logger.exception("SoF: invalid wildfire item: %r", item)
                    continue

                if detection.acquired_at > now:
                    continue
                if detection.acquired_at < start:
                    continue
                detections[detection.fingerprint] = detection

            next_token = connection.get("nextToken")
            if not next_token:
                break

        result = sorted(detections.values(), key=lambda d: d.acquired_at)
        logger.info(
            "SoF fetch complete: detections=%d window=%dmin",
            len(result),
            minutes,
        )
        return result
