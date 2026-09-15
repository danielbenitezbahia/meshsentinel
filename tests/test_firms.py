import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from firms_client import FirmsDetection, build_fingerprint, parse_csv
from firms_geo import FirmsGeoFilter
from firms_notifier import process_pending_actions
from firms_service import (
    FirmsService,
    LEVEL_CANDIDATE,
    LEVEL_INITIAL,
    LEVEL_MULTISATELLITE,
    LEVEL_REPEATED,
)

ROOT = Path(__file__).resolve().parents[1]


def detection(satellite, acquired_at, lat, lon, confidence="n", frp=1.0):
    return FirmsDetection(
        fingerprint=build_fingerprint(satellite, acquired_at, lat, lon),
        satellite=satellite,
        acquired_at=acquired_at,
        latitude=lat,
        longitude=lon,
        confidence=confidence,
        frp=frp,
        daynight="D",
    )


class StaticGeo:
    def __init__(self, partido="Puan"):
        self.partido = partido

    def locate_point(self, latitude, longitude):
        return self.partido


class MutableClient:
    enabled = True

    def __init__(self, detections=None):
        self.detections = list(detections or [])

    def fetch_detections(self, now=None, rolling_hours=24, day_range=2):
        return sorted([d for d in self.detections if now is None or d.acquired_at <= now], key=lambda d: d.acquired_at)


class FakeIface:
    def __init__(self, ok=True):
        self.ok = ok
        self.text = []
        self.waypoints = []

    def send_channel_message(self, message, channel_index=0, chunk_delay=0.2):
        self.text.append((message, channel_index))
        return self.ok

    def send_channel_waypoint(self, **kwargs):
        self.waypoints.append(kwargs)
        return self.ok


class FirmsClientTests(unittest.TestCase):
    def test_parse_time_without_leading_zero_and_fingerprint_ignores_frp(self):
        csv_text = """latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight\n-36.98383,-60.24705,296.16,0.5,0.5,2026-09-15,525,N21,VIIRS,n,2.0NRT,280,1.1,N\n"""
        parsed = parse_csv(csv_text)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].acquired_at.hour, 5)
        self.assertEqual(parsed[0].acquired_at.minute, 25)
        fp1 = build_fingerprint("N21", parsed[0].acquired_at, parsed[0].latitude, parsed[0].longitude)
        fp2 = build_fingerprint("N21", parsed[0].acquired_at, parsed[0].latitude, parsed[0].longitude)
        self.assertEqual(fp1, fp2)


class FirmsGeoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.geo = FirmsGeoFilter(str(ROOT / "partidos.geojson"))

    def test_loads_all_target_partidos(self):
        self.assertEqual(self.geo.loaded_count, 13)

    def test_locates_known_puan_and_rejects_outside_sob(self):
        self.assertEqual(self.geo.locate_point(-38.00987, -63.21591), "Puan")
        self.assertIsNone(self.geo.locate_point(-38.96157, -64.16523))  # Río Negro


class FirmsServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "firms.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def service(self, client, channels=(1,)):
        return FirmsService(
            db_path=self.db,
            client=client,
            geo_filter=StaticGeo(),
            channel_indexes=channels,
        )

    def test_single_nominal_stays_candidate_and_high_alerts(self):
        t = datetime(2026, 9, 14, 18, 0, tzinfo=timezone.utc)
        client = MutableClient([detection("N20", t, -38.0, -63.0, "n", 50.0)])
        service = self.service(client)
        service.poll(now=t + timedelta(minutes=5))
        self.assertEqual(service.list_events()[0]["current_level"], LEVEL_CANDIDATE)
        self.assertEqual(service.count_rows("firms_outbox"), 0)

        # New isolated high-confidence event, far enough away not to match the first.
        client.detections.append(detection("N20", t + timedelta(minutes=2), -38.1, -63.1, "h", 1.0))
        service.poll(now=t + timedelta(minutes=10))
        levels = sorted(e["current_level"] for e in service.list_events())
        self.assertEqual(levels, [LEVEL_CANDIDATE, LEVEL_INITIAL])

    def test_puan_real_case_matches_at_2_18km_and_keeps_waypoint_after_restart(self):
        t = datetime(2026, 9, 14, 18, 6, tzinfo=timezone.utc)
        first = [
            detection("N21", t, -38.01120, -63.21709, frp=6.42),
            detection("N21", t, -38.01053, -63.21273, frp=6.42),
            detection("N21", t, -38.00789, -63.21791, frp=6.42),
        ]
        client = MutableClient(first)
        service = self.service(client, channels=(1, 2))
        result = service.poll(now=t + timedelta(minutes=24))  # bootstrap
        self.assertEqual(result["bootstrap_alerts"], 1)
        event = service.list_events()[0]
        event_id = event["event_id"]
        waypoint_id = event["waypoint_id"]
        self.assertEqual(event["current_level"], LEVEL_INITIAL)
        self.assertEqual(event["total_pixels"], 3)

        # Simulate process restart: a new service instance opens the same SQLite DB.
        second = [
            detection("N20", t + timedelta(minutes=56), -38.02077, -63.19693, frp=8.13),
            detection("N20", t + timedelta(minutes=56), -38.01955, -63.19246, frp=5.95),
        ]
        client2 = MutableClient(first + second)
        restarted = self.service(client2, channels=(1, 2))
        restarted.poll(now=t + timedelta(minutes=64))
        events = restarted.list_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_id"], event_id)
        self.assertEqual(events[0]["waypoint_id"], waypoint_id)
        self.assertEqual(events[0]["current_level"], LEVEL_MULTISATELLITE)
        self.assertEqual(events[0]["total_pixels"], 5)

        # 2 channels: initial text+waypoint + update text+waypoint = 8 actions.
        self.assertEqual(restarted.count_rows("firms_outbox"), 8)

    def test_incremental_pixels_update_same_observation_across_polls(self):
        t = datetime(2026, 9, 14, 18, 6, tzinfo=timezone.utc)
        d1 = detection("N21", t, -38.01120, -63.21709, "n", 2.0)
        client = MutableClient([d1])
        service = self.service(client)
        service.poll(now=t + timedelta(minutes=1))
        self.assertEqual(service.count_rows("firms_observations"), 1)
        self.assertEqual(service.list_events()[0]["current_level"], LEVEL_CANDIDATE)

        d2 = detection("N21", t, -38.01053, -63.21273, "n", 3.0)
        client.detections.append(d2)
        service.poll(now=t + timedelta(minutes=2))
        self.assertEqual(service.count_rows("firms_observations"), 1)
        event = service.list_events()[0]
        self.assertEqual(event["total_pixels"], 2)
        self.assertEqual(event["current_level"], LEVEL_INITIAL)

    def test_same_satellite_second_pass_is_repeated(self):
        t = datetime(2026, 9, 14, 17, 20, tzinfo=timezone.utc)
        client = MutableClient([detection("N20", t, -39.14212, -62.58121)])
        service = self.service(client)
        service.poll(now=t + timedelta(minutes=5))
        client.detections.extend([
            detection("N20", t + timedelta(minutes=100), -39.14203, -62.58664, "h", 7.47),
            detection("N20", t + timedelta(minutes=100), -39.14077, -62.58212, "n", 7.47),
        ])
        service.poll(now=t + timedelta(minutes=105))
        event = service.list_events()[0]
        self.assertEqual(event["current_level"], LEVEL_REPEATED)
        self.assertEqual(event["pass_count"], 2)


    def test_bootstrap_suppresses_old_alert_but_sends_recent_one(self):
        now = datetime(2026, 9, 14, 18, 30, tzinfo=timezone.utc)
        old = now - timedelta(hours=10)
        recent = now - timedelta(minutes=20)
        client = MutableClient([
            detection("N20", old, -38.0, -63.0, "h", 4.0),
            detection("N21", recent, -38.2, -63.2, "n", 3.0),
            detection("N21", recent, -38.199, -63.199, "n", 3.0),
        ])
        service = self.service(client)
        result = service.poll(now=now)
        self.assertEqual(result["bootstrap_suppressed"], 1)
        self.assertEqual(result["bootstrap_alerts"], 1)
        self.assertEqual(service.count_rows("firms_outbox"), 2)

    def test_closed_event_is_not_resurrected(self):
        t = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
        first = detection("N20", t, -38.0, -63.0, "h", 2.0)
        client = MutableClient([first])
        service = self.service(client)
        service.poll(now=t + timedelta(minutes=1))
        old_id = service.list_events()[0]["event_id"]
        service.close_expired_events(now=t + timedelta(hours=14, minutes=1))
        client.detections.append(detection("N20", t + timedelta(hours=15), -38.0, -63.0, "h", 2.0))
        service.poll(now=t + timedelta(hours=15, minutes=1))
        ids = [e["event_id"] for e in service.list_events()]
        self.assertEqual(len(ids), 2)
        self.assertIn(old_id, ids)

    def test_outbox_is_idempotent_and_notifier_marks_sent(self):
        t = datetime(2026, 9, 14, 18, 6, tzinfo=timezone.utc)
        client = MutableClient([
            detection("N21", t, -38.01120, -63.21709),
            detection("N21", t, -38.01053, -63.21273),
        ])
        service = self.service(client, channels=(1, 2))
        service.poll(now=t + timedelta(minutes=5))
        self.assertEqual(service.count_rows("firms_outbox"), 4)
        # Same FIRMS response again must not duplicate actions.
        service.poll(now=t + timedelta(minutes=6))
        self.assertEqual(service.count_rows("firms_outbox"), 4)

        iface = FakeIface(ok=True)
        sent = process_pending_actions(iface, service)
        self.assertEqual(sent, 4)
        self.assertEqual(len(iface.text), 2)
        self.assertEqual(len(iface.waypoints), 2)
        self.assertIn("https://maps.google.com/?q=", iface.text[0][0])

        with sqlite3.connect(self.db) as conn:
            pending = conn.execute("SELECT COUNT(*) FROM firms_outbox WHERE status='PENDING'").fetchone()[0]
        self.assertEqual(pending, 0)


if __name__ == "__main__":
    unittest.main()
