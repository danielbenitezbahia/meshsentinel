"""Tests generales para funciones de traffic_stats.py (fuera de las
específicas de nodos aislados, ver test_traffic_stats_orphans.py)."""
import importlib.util
import os
import tempfile
import unittest

MODULE_PATH = os.path.join(os.path.dirname(__file__), "..", "traffic_stats.py")
spec = importlib.util.spec_from_file_location("traffic_stats", MODULE_PATH)
ts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ts)


class PublicChannelMessagesTests(unittest.TestCase):
    """record_public_message / get_recent_public_messages: el feed "en vivo"
    del canal público que consume /api/alerts/public-channel."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self._tmp.close()
        self._orig_path = ts.DB_PATH
        ts.DB_PATH = self._tmp.name
        ts.init_db()

    def tearDown(self):
        ts.DB_PATH = self._orig_path
        if os.path.exists(self._tmp.name):
            os.unlink(self._tmp.name)

    def test_records_and_retrieves_message(self):
        ts.record_public_message("!aaa1", "ABC", "Nodo Alpha", "hola mesh")
        rows = ts.get_recent_public_messages()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["node_id"], "!aaa1")
        self.assertEqual(rows[0]["short_name"], "ABC")
        self.assertEqual(rows[0]["long_name"], "Nodo Alpha")
        self.assertEqual(rows[0]["text"], "hola mesh")
        self.assertIn("ts", rows[0])

    def test_orders_most_recent_first(self):
        ts.record_public_message("!aaa1", "A", "A", "primero")
        ts.record_public_message("!aaa2", "B", "B", "segundo")
        rows = ts.get_recent_public_messages()
        self.assertEqual([r["text"] for r in rows], ["segundo", "primero"])

    def test_respects_limit(self):
        for i in range(5):
            ts.record_public_message("!aaa1", "A", "A", f"msg{i}")
        rows = ts.get_recent_public_messages(limit=2)
        self.assertEqual(len(rows), 2)

    def test_cleanup_old_data_removes_old_public_messages(self):
        ts.record_public_message("!aaa1", "A", "A", "viejo")
        old_ts = ts._now() - 8 * 86400
        con = __import__("sqlite3").connect(ts.DB_PATH)
        con.execute("UPDATE public_channel_messages SET ts = ?", (old_ts,))
        con.commit()
        con.close()

        ts.cleanup_old_data(days=7)
        self.assertEqual(ts.get_recent_public_messages(), [])


if __name__ == "__main__":
    unittest.main()
