import os
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from firms_notifier import build_text_payload
from smn_fire_weather import (
    SMNFireWeatherClient,
    SMNFireWeatherSnapshot,
    degrees_to_cardinal,
    propagation_score,
    relative_humidity_from_dewpoint,
)


class SMNFireWeatherMathTests(unittest.TestCase):
    def test_relative_humidity_from_dewpoint(self):
        rh = relative_humidity_from_dewpoint(30.0, 10.0)
        self.assertGreater(rh, 28)
        self.assertLess(rh, 30)

    def test_wind_direction_is_downwind(self):
        reports = [{
            "station_id": "0-20000-0-87750",
            "report_id": "r1",
            "observed_at": datetime(2026, 9, 15, 18, 0, tzinfo=timezone.utc),
            "latitude": -38.72,
            "longitude": -62.17,
            "values": {
                "air_temperature": 30.0,
                "dewpoint_temperature": 10.0,
                "wind_speed": 10.0,      # m/s = 36 km/h
                "wind_direction": 270.0, # from west -> downwind east
            },
        }]
        snap = SMNFireWeatherSnapshot(
            reports,
            datetime(2026, 9, 15, 18, 30, tzinfo=timezone.utc),
            max_distance_km=180,
            max_age_hours=3,
        )
        assessment = snap.assess(-38.7, -62.2)
        self.assertIsNotNone(assessment)
        payload = assessment.as_dict()
        self.assertEqual(payload["wind_speed_kmh"], 36)
        self.assertEqual(payload["propagation_to"], "E")
        self.assertEqual(degrees_to_cardinal(90), "E")

    def test_stale_or_far_reports_are_rejected(self):
        reports = [{
            "station_id": "x",
            "report_id": "r1",
            "observed_at": datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc),
            "latitude": -38.7,
            "longitude": -62.2,
            "values": {"air_temperature": 25, "dewpoint_temperature": 15, "wind_speed": 5},
        }]
        snap = SMNFireWeatherSnapshot(
            reports,
            datetime(2026, 9, 15, 18, 30, tzinfo=timezone.utc),
            max_distance_km=180,
            max_age_hours=3,
        )
        self.assertIsNone(snap.assess(-38.7, -62.2))

    def test_propagation_score(self):
        score, level = propagation_score(wind_kmh=50, rh=18, temperature_c=36)
        self.assertGreaterEqual(score, 8)
        self.assertEqual(level, "MUY ALTA")
        score, level = propagation_score(wind_kmh=5, rh=70, temperature_c=15)
        self.assertEqual(score, 0)
        self.assertEqual(level, "BAJA")


class SMNFireWeatherClientTests(unittest.TestCase):
    def test_groups_synop_features_into_report(self):
        features = []
        for name, value in [
            ("air_temperature", 31.0),
            ("dewpoint_temperature", 9.0),
            ("wind_speed", 12.0),
            ("wind_direction", 300.0),
        ]:
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-62.2, -38.7]},
                "properties": {
                    "name": name,
                    "value": value,
                    "reportId": "0-20000-0-87750-202609151800",
                    "reportTime": "2026-09-15T18:00:00Z",
                    "wigos_station_identifier": "0-20000-0-87750",
                },
            })
        reports = SMNFireWeatherClient._group_reports(features)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["values"]["wind_speed"], 12.0)

    def test_fetch_snapshot_fails_soft(self):
        old = os.environ.get("FIRMS_SMN_WEATHER_ENABLED")
        os.environ["FIRMS_SMN_WEATHER_ENABLED"] = "1"
        try:
            session = MagicMock()
            session.get.side_effect = RuntimeError("SMN offline")
            client = SMNFireWeatherClient(session=session)
            self.assertIsNone(client.fetch_snapshot(datetime(2026, 9, 15, 18, 0, tzinfo=timezone.utc)))
        finally:
            if old is None:
                os.environ.pop("FIRMS_SMN_WEATHER_ENABLED", None)
            else:
                os.environ["FIRMS_SMN_WEATHER_ENABLED"] = old


class FirmsWeatherMessageTests(unittest.TestCase):
    def test_message_contains_weather_and_encoded_google_maps_separator(self):
        event = {
            "satellites": ["N21"],
            "current_level": 1,
            "max_pixels_in_observation": 2,
            "pass_count": 1,
            "partidos": ["Saavedra"],
            "last_seen": "2026-09-15T17:45:00+00:00",
            "latest_latitude": -37.37418,
            "latest_longitude": -62.41396,
        }
        weather = {
            "level": "ALTA",
            "wind_speed_kmh": 38,
            "relative_humidity": 28,
            "temperature_c": 31.0,
            "propagation_to": "SE",
        }
        message = build_text_payload(event, 0, weather)["message"]
        self.assertIn("⚠️ Propagación ALTA · SMN 38km/h→SE · HR28% · 31°C", message)
        self.assertIn("?q=-37.37418%2C-62.41396", message)
        self.assertNotIn("?q=-37.37418,-62.41396", message)
        self.assertLessEqual(len(message.encode("utf-8")), 220)


if __name__ == "__main__":
    unittest.main()
