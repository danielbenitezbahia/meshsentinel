import unittest
from datetime import datetime, timezone

from sof_client import SofClient, _parse_detection_time, _to_firms_detection


class FakeResponse:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class SofClientTests(unittest.TestCase):
    def test_parse_detection_time(self):
        value = _parse_detection_time("2026-09-16T12:10:00+00:00_214505365")
        self.assertEqual(value, datetime(2026, 9, 16, 12, 10, tzinfo=timezone.utc))

    def test_maps_sof_item_to_shared_detection(self):
        detection = _to_firms_detection(
            {
                "cat": "W",
                "conf": 10,
                "date": "2026-09-16T12",
                "id": "2026-09-16T12:10:00+00:00_214505365",
                "sat": "noaa-goes19",
                "x": -62.90066146850586,
                "y": -6.857888698577881,
            }
        )
        self.assertEqual(detection.satellite, "GOES-19")
        self.assertEqual(detection.confidence, "10")
        self.assertEqual(detection.longitude, -62.90066146850586)
        self.assertEqual(detection.latitude, -6.857888698577881)
        self.assertEqual(detection.frp, 0.0)

    def test_fetches_and_paginates(self):
        page1 = {
            "data": {
                "getPublicWildfireByDateRangeNewIds": {
                    "items": [
                        {
                            "cat": "W",
                            "conf": 10,
                            "date": "2026-09-16T12",
                            "id": "2026-09-16T12:10:00+00:00_1",
                            "sat": "noaa-goes19",
                            "x": -62.0,
                            "y": -38.0,
                        }
                    ],
                    "nextToken": "page-2",
                }
            }
        }
        page2 = {
            "data": {
                "getPublicWildfireByDateRangeNewIds": {
                    "items": [
                        {
                            "cat": "W",
                            "conf": 33,
                            "date": "2026-09-16T12",
                            "id": "2026-09-16T12:20:00+00:00_2",
                            "sat": "noaa-goes19",
                            "x": -62.1,
                            "y": -38.1,
                        }
                    ],
                    "nextToken": None,
                }
            }
        }
        session = FakeSession([FakeResponse(page1), FakeResponse(page2)])
        client = SofClient(api_key="test", session=session, window_minutes=65)
        now = datetime(2026, 9, 16, 13, 0, tzinfo=timezone.utc)

        detections = client.fetch_detections(now=now)

        self.assertEqual(len(detections), 2)
        self.assertEqual(len(session.calls), 2)
        second_payload = session.calls[1][1]["json"]
        self.assertEqual(second_payload["variables"]["nextToken"], "page-2")
        self.assertEqual(session.calls[0][1]["headers"]["x-api-key"], "test")


if __name__ == "__main__":
    unittest.main()
