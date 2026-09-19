import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from firms_client import FirmsDetection, build_fingerprint, parse_csv
from firms_geo import FirmsGeoFilter
from firms_notifier import ACTION_TEXT, ACTION_TEXT_BURST, process_pending_actions
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




class DisabledSofClient:
    enabled = False

    def fetch_detections(self, now=None):
        return []


class FakeIface:
    def __init__(self, ok=True):
        self.ok = ok
        self.text = []
        self.waypoints = []
        self.deleted_waypoints = []

    def send_channel_message(self, message, channel_index=0, chunk_delay=0.2):
        self.text.append((message, channel_index))
        return self.ok

    def send_channel_waypoint(self, **kwargs):
        self.waypoints.append(kwargs)
        return self.ok

    def send_channel_waypoint_delete(self, **kwargs):
        self.deleted_waypoints.append(kwargs)
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
            sof_client=DisabledSofClient(),
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


    def test_satellite_aliases_are_canonicalized_and_exact_duplicate_is_ignored(self):
        t = datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)
        client = MutableClient([
            detection("N20", t, -38.0, -63.0, "n", 1.0),
            detection("VIIRS NOAA-20", t, -38.0, -63.0, "n", 1.0),
        ])
        service = self.service(client)
        service.poll(now=t + timedelta(minutes=5))

        self.assertEqual(service.count_rows("firms_detections"), 1)
        event = service.list_events()[0]
        self.assertEqual(event["satellites"], ["NOAA-20"])
        self.assertEqual(event["pass_count"], 1)
        self.assertEqual(event["current_level"], LEVEL_CANDIDATE)
        self.assertEqual(service.count_rows("firms_outbox"), 0)

    def test_satellite_aliases_do_not_create_false_multisatellite(self):
        t = datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)
        client = MutableClient([
            detection("N20", t, -38.0000, -63.0000, "n", 1.0),
            detection("VIIRS NOAA-20", t, -38.0020, -63.0020, "n", 1.0),
        ])
        service = self.service(client)
        service.poll(now=t + timedelta(minutes=5))

        event = service.list_events()[0]
        self.assertEqual(event["satellites"], ["NOAA-20"])
        self.assertEqual(event["pass_count"], 1)
        self.assertEqual(event["current_level"], LEVEL_INITIAL)
        self.assertNotEqual(event["current_level"], LEVEL_MULTISATELLITE)


    def test_restart_migrates_existing_alias_event_out_of_false_multisatellite(self):
        t = datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)
        service = self.service(MutableClient([]))
        event_id = "legacy-alias-event"
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                """INSERT INTO firms_events(
                       event_id, waypoint_id, first_seen, last_seen, latest_latitude, latest_longitude,
                       partidos_json, satellites_json, pass_count, observation_count, total_pixels,
                       max_pixels_in_observation, has_high_confidence, max_frp, current_level,
                       communicated_level, ever_communicated, status
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE')""",
                (event_id, 123456789, t.isoformat(), t.isoformat(), -38.0, -63.0,
                 '["Puan"]', '["N20", "VIIRS NOAA-20"]', 2, 2, 2, 1, 0, 1.0,
                 LEVEL_MULTISATELLITE, LEVEL_MULTISATELLITE, 1),
            )
            conn.execute(
                """INSERT INTO firms_observations(
                       observation_id, satellite, acquired_at, latitude, longitude, pixel_count,
                       max_frp, has_high_confidence, partidos_json, event_id
                   ) VALUES ('obs-a', 'N20', ?, -38.0, -63.0, 1, 1.0, 0, '["Puan"]', ?)""",
                (t.isoformat(), event_id),
            )
            conn.execute(
                """INSERT INTO firms_observations(
                       observation_id, satellite, acquired_at, latitude, longitude, pixel_count,
                       max_frp, has_high_confidence, partidos_json, event_id
                   ) VALUES ('obs-b', 'VIIRS NOAA-20', ?, -38.001, -63.001, 1, 1.0, 0, '["Puan"]', ?)""",
                (t.isoformat(), event_id),
            )
            conn.commit()

        restarted = self.service(MutableClient([]))
        event = [e for e in restarted.list_events() if e["event_id"] == event_id][0]
        self.assertEqual(event["satellites"], ["NOAA-20"])
        self.assertEqual(event["pass_count"], 1)
        self.assertEqual(event["current_level"], LEVEL_CANDIDATE)
        self.assertEqual(event["communicated_level"], LEVEL_CANDIDATE)

    def test_three_firms_events_in_one_poll_use_one_burst_text_and_individual_waypoints(self):
        t = datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)
        client = MutableClient([detection("N20", t, -37.0, -62.0, "n", 1.0)])
        service = self.service(client, channels=(1, 2))
        service.poll(now=t + timedelta(minutes=1))  # establish bootstrap with a silent candidate

        client.detections.extend([
            detection("N20", t + timedelta(minutes=10), -38.00, -63.00, "h", 2.0),
            detection("N20", t + timedelta(minutes=10), -38.10, -63.10, "h", 2.0),
            detection("N20", t + timedelta(minutes=10), -38.20, -63.20, "h", 2.0),
        ])
        result = service.poll(now=t + timedelta(minutes=11))
        self.assertEqual(result["queued_events"], 3)

        pending = service.get_pending_outbox()
        self.assertEqual(len([a for a in pending if a["action_type"] == ACTION_TEXT]), 0)
        self.assertEqual(len([a for a in pending if a["action_type"] == ACTION_TEXT_BURST]), 2)
        self.assertEqual(len([a for a in pending if a["action_type"] == "WAYPOINT_CREATE"]), 6)

        iface = FakeIface(ok=True)
        self.assertEqual(process_pending_actions(iface, service), 8)
        self.assertEqual(len(iface.text), 2)
        self.assertEqual(len(iface.waypoints), 6)
        self.assertTrue(all("FIRMS | 3 eventos térmicos" in message for message, _ in iface.text))
        self.assertTrue(all("Puan: 3" in message for message, _ in iface.text))

    def test_closing_communicated_event_queues_waypoint_delete_once_per_channel(self):
        t = datetime(2026, 9, 14, 18, 6, tzinfo=timezone.utc)
        client = MutableClient([
            detection("N21", t, -38.01120, -63.21709),
            detection("N21", t, -38.01053, -63.21273),
        ])
        service = self.service(client, channels=(1, 2))
        service.poll(now=t + timedelta(minutes=5))

        # Initial alert is communicated: TEXT + WAYPOINT_CREATE on each channel.
        iface = FakeIface(ok=True)
        self.assertEqual(process_pending_actions(iface, service), 4)
        self.assertEqual(len(iface.waypoints), 2)

        closed = service.close_expired_events(now=t + timedelta(hours=14, minutes=1))
        self.assertEqual(closed, 1)

        pending = service.get_pending_outbox()
        deletes = [a for a in pending if a["action_type"] == "WAYPOINT_DELETE"]
        self.assertEqual(len(deletes), 2)

        sent = process_pending_actions(iface, service)
        self.assertEqual(sent, 2)
        self.assertEqual(
            sorted(item["channel_index"] for item in iface.deleted_waypoints),
            [1, 2],
        )
        waypoint_id = service.list_events()[0]["waypoint_id"]
        self.assertTrue(all(item["waypoint_id"] == waypoint_id for item in iface.deleted_waypoints))

        # Calling close again must not enqueue or resend duplicate deletes.
        self.assertEqual(
            service.close_expired_events(now=t + timedelta(hours=15)),
            0,
        )
        self.assertEqual(len(service.get_pending_outbox()), 0)

    def test_closing_never_communicated_candidate_does_not_delete_waypoint(self):
        t = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
        client = MutableClient([detection("N20", t, -38.0, -63.0, "n", 1.0)])
        service = self.service(client)
        service.poll(now=t + timedelta(minutes=1))
        self.assertFalse(service.list_events()[0]["ever_communicated"])

        self.assertEqual(service.close_expired_events(now=t + timedelta(hours=14, minutes=1)), 1)
        self.assertEqual(
            [a for a in service.get_pending_outbox() if a["action_type"] == "WAYPOINT_DELETE"],
            [],
        )


if __name__ == "__main__":
    unittest.main()
