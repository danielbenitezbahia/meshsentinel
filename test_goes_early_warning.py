import unittest

from firms_notifier import build_text_payload, build_waypoint_payload


def base_event(**overrides):
    event = {
        "event_id": "e1",
        "waypoint_id": 123,
        "first_seen": "2026-09-16T13:40:00+00:00",
        "last_seen": "2026-09-16T13:40:00+00:00",
        "latest_latitude": -38.73062,
        "latest_longitude": -61.96828,
        "partidos": ["Coronel Rosales"],
        "satellites": ["GOES-19"],
        "pass_count": 1,
        "observation_count": 1,
        "total_pixels": 1,
        "max_pixels_in_observation": 1,
        "has_high_confidence": False,
        "max_frp": 0.0,
        "current_level": 0,
        "communicated_level": 0,
        "ever_communicated": False,
        "status": "ACTIVE",
    }
    event.update(overrides)
    return event


class GoesEarlyWarningNotifierTests(unittest.TestCase):
    def test_first_goes_candidate_is_possible(self):
        payload = build_text_payload(base_event(), previous_level=0)
        self.assertIn("POSIBLE FOCO TÉRMICO", payload["message"])
        self.assertIn("sin confirmar", payload["message"])

        waypoint = build_waypoint_payload(base_event(), 24)
        self.assertTrue(waypoint["name"].startswith("Posible foco"))
        self.assertEqual(waypoint["icon"], ord("⚠"))

    def test_second_goes_pass_is_confirmed(self):
        event = base_event(
            current_level=2,
            pass_count=2,
            observation_count=2,
            ever_communicated=True,
        )
        payload = build_text_payload(event, previous_level=0)
        self.assertIn("FOCO TÉRMICO CONFIRMADO", payload["message"])
        self.assertIn("2 pasadas", payload["message"])

        waypoint = build_waypoint_payload(event, 24)
        self.assertTrue(waypoint["name"].startswith("Foco térmico"))
        self.assertEqual(waypoint["icon"], ord("🔥"))

    def test_multisatellite_after_possible_is_confirmed(self):
        event = base_event(
            current_level=3,
            satellites=["GOES-19", "N20"],
            pass_count=2,
            observation_count=2,
            ever_communicated=True,
        )
        payload = build_text_payload(event, previous_level=0)
        self.assertIn("FOCO TÉRMICO CONFIRMADO", payload["message"])
        self.assertIn("GOES-19", payload["message"])
        self.assertIn("NOAA-20", payload["message"])


if __name__ == "__main__":
    unittest.main()
