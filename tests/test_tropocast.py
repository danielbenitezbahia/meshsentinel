"""Tests para tropocast_client.py, tropocast_service.py y
modules/propagacion.py. Sigue el patrón de test_firms.py: sin librerías de
mocking HTTP, clases fake escritas a mano inyectadas por constructor.

El fixture SAMPLE_RESPONSE_* está recortado de una respuesta REAL de
GET /api/v1/device/text-forecast (verificada el 2026-11-02 con una key real,
lat=-38.1580 lon=-61.9524 radius_km=700 source=gdps period=next_72h), no
inventada -- los nombres de campo y la escala de score son los que devuelve
la API de verdad.
"""
import os
import unittest

import tropocast_client as tc_client
import tropocast_service as tc_service
from tropocast_client import TropocastError, TropocastRateLimited, TropocastUnauthorized


def _frame(offset_hour, score, text):
    return {
        "offset_hour": offset_hour,
        "valid_at": f"2026-11-0{2 + offset_hour // 24}T{(17 + offset_hour) % 24:02d}:00:00Z",
        "score": score,
        "coverage_percent": max(0.0, (score - 30) / 2),
        "samples": 7029,
        "local_time": {"text": text},
    }


SAMPLE_RESPONSE_GENERAL = {
    "generated_at": "2026-11-02T16:49:46.444878Z",
    "language": "es",
    "location": {
        "latitude": -38.158, "longitude": -61.9524, "radius_km": 700.0,
        "source": "gdps", "area": "world", "run": "2026-11-02T00:00:00Z",
    },
    "data_status": "complete",
    "missing": [],
    "summary": {
        "kind": "peak",
        "title": "Mejores condiciones previstas",
        "message": "En un radio de 700 km, las mejores condiciones se prevén hacia 03/11/2026 06:00 -03.",
        "level": "elevated",
        "level_label": "Condiciones elevadas",
        "details": "VHF/UHF · 03/11/2026 06:00 -03: potencial de propagación troposférica 71/100. "
                    "El 27,6% de las muestras válidas alcanza el umbral elegido (65/100).",
    },
    "alerts": [],
    "frames": [
        _frame(0, 48, "02/11/2026 14:00 -03"),
        _frame(3, 50, "02/11/2026 17:00 -03"),
        _frame(9, 65, "03/11/2026 00:00 -03"),
        _frame(15, 71, "03/11/2026 06:00 -03"),
        _frame(33, 39, "04/11/2026 00:00 -03"),
        _frame(57, 66, "05/11/2026 00:00 -03"),
        _frame(63, 70, "05/11/2026 06:00 -03"),
    ],
    "daily": [],
    "outlook_text": "Previsión troposférica VHF/UHF...",
    "notes": [],
    "sampling": {"score_method": "p80", "coverage_method": "fraction_of_valid_sampled_cells"},
}


def _make_response(run="2026-11-02T00:00:00Z", frames=None, data_status="complete"):
    resp = dict(SAMPLE_RESPONSE_GENERAL)
    resp["location"] = dict(resp["location"], run=run)
    resp["data_status"] = data_status
    if frames is not None:
        resp["frames"] = frames
    return resp


class FakeTropocastClient:
    """Implementa la misma interfaz que TropocastClient (enabled,
    fetch_forecast, fetch_metadata) sin tocar la red, como MutableClient en
    test_firms.py."""

    def __init__(self, enabled=True, responses_by_location=None, metadata=None,
                 raise_on_forecast=None, raise_on_metadata=True):
        self.enabled = enabled
        self.responses_by_location = responses_by_location or {}
        self.metadata = metadata
        self.raise_on_forecast = raise_on_forecast
        # Por default el chequeo de metadata "no está disponible" (como en
        # producción hasta confirmar el parámetro gdps/world), para no
        # condicionar los tests de refresh() a ese detalle.
        self.raise_on_metadata = raise_on_metadata
        self.forecast_calls = []
        self.metadata_calls = 0

    def fetch_metadata(self, source=None, area=None):
        self.metadata_calls += 1
        if self.raise_on_metadata:
            raise RuntimeError("metadata no disponible (simulado)")
        return self.metadata or {}

    def fetch_forecast(self, lat, lon, radius_km, **kwargs):
        self.forecast_calls.append((lat, lon, radius_km))
        if self.raise_on_forecast:
            raise self.raise_on_forecast
        key = (lat, lon, radius_km)
        if key in self.responses_by_location:
            return self.responses_by_location[key]
        return _make_response()


def _tmp_db_path():
    import tempfile
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    f.close()
    return f.name


class LevelScaleTests(unittest.TestCase):
    def test_level_at_score_boundaries(self):
        self.assertEqual(tc_service.level_at_score(0), "normal")
        self.assertEqual(tc_service.level_at_score(36), "normal")
        self.assertEqual(tc_service.level_at_score(37), "slightly_enhanced")
        self.assertEqual(tc_service.level_at_score(64), "slightly_enhanced")
        self.assertEqual(tc_service.level_at_score(65), "enhanced")
        self.assertEqual(tc_service.level_at_score(79), "enhanced")
        self.assertEqual(tc_service.level_at_score(80), "strongly_enhanced")
        self.assertEqual(tc_service.level_at_score(88), "very_strongly_enhanced")
        self.assertEqual(tc_service.level_at_score(100), "very_strongly_enhanced")

    def test_level_rank_is_monotonic(self):
        ranks = [tc_service.level_rank(code) for code, _ in tc_service.LEVEL_SCALE]
        self.assertEqual(ranks, sorted(ranks))

    def test_level_label_known_and_unknown(self):
        self.assertEqual(tc_service.level_label("enhanced"), "Elevadas")
        self.assertEqual(tc_service.level_label("algo_raro"), "algo_raro")


class TropocastClientErrorHandlingTests(unittest.TestCase):
    """Testea el parseo de status codes con una sesión fake, sin red real."""

    class _FakeHttpResponse:
        def __init__(self, status_code, json_body=None, text="", headers=None):
            self.status_code = status_code
            self._json_body = json_body
            self.text = text
            self.headers = headers or {}

        def json(self):
            return self._json_body

    class _FakeSession:
        def __init__(self, response):
            self._response = response
            self.last_call = None

        def get(self, url, params=None, headers=None, timeout=None):
            self.last_call = {"url": url, "params": params, "headers": headers, "timeout": timeout}
            return self._response

    def test_fetch_forecast_raises_unauthorized_without_key(self):
        client = tc_client.TropocastClient(api_key="", session=self._FakeSession(None))
        self.assertFalse(client.enabled)
        with self.assertRaises(TropocastUnauthorized):
            client.fetch_forecast(-38.0, -62.0, 700)

    def test_fetch_forecast_429_raises_rate_limited_with_retry_after(self):
        resp = self._FakeHttpResponse(429, text="rate limited", headers={"Retry-After": "30"})
        client = tc_client.TropocastClient(api_key="test-key", session=self._FakeSession(resp))
        with self.assertRaises(TropocastRateLimited) as ctx:
            client.fetch_forecast(-38.0, -62.0, 700)
        self.assertEqual(ctx.exception.retry_after, 30)

    def test_fetch_forecast_401_raises_unauthorized(self):
        resp = self._FakeHttpResponse(401, text="invalid key")
        client = tc_client.TropocastClient(api_key="bad-key", session=self._FakeSession(resp))
        with self.assertRaises(TropocastUnauthorized):
            client.fetch_forecast(-38.0, -62.0, 700)

    def test_fetch_forecast_200_returns_json(self):
        resp = self._FakeHttpResponse(200, json_body=SAMPLE_RESPONSE_GENERAL)
        client = tc_client.TropocastClient(api_key="test-key", session=self._FakeSession(resp))
        result = client.fetch_forecast(-38.0, -62.0, 700)
        self.assertEqual(result["data_status"], "complete")

    def test_fetch_forecast_sends_key_in_header_not_url(self):
        resp = self._FakeHttpResponse(200, json_body=SAMPLE_RESPONSE_GENERAL)
        session = self._FakeSession(resp)
        client = tc_client.TropocastClient(api_key="super-secret", session=session)
        client.fetch_forecast(-38.0, -62.0, 700)
        self.assertEqual(session.last_call["headers"]["X-Tropocast-Key"], "super-secret")
        self.assertNotIn("super-secret", session.last_call["url"])
        self.assertNotIn("key", str(session.last_call["params"]).lower())

    def test_fetch_metadata_sends_no_auth_header(self):
        resp = self._FakeHttpResponse(200, json_body={"run": "x"})
        session = self._FakeSession(resp)
        client = tc_client.TropocastClient(api_key="", session=session)
        client.fetch_metadata(source="gdps", area="world")
        self.assertEqual(session.last_call["headers"], {})


class TropocastServiceRefreshTests(unittest.TestCase):
    def setUp(self):
        self.db_path = _tmp_db_path()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _service(self, client, locations=None):
        locs = locations or [tc_service.Location("General (700km)", -38.158, -61.9524, 700)]
        return tc_service.TropocastService(client=client, db_path=self.db_path, locations=locs)

    def test_refresh_saves_new_forecast(self):
        client = FakeTropocastClient()
        service = self._service(client)
        result = service.refresh()
        self.assertEqual(result, {"General (700km)": "ok"})
        self.assertIsNotNone(service._latest_for("General (700km)"))

    def test_refresh_same_run_twice_does_not_duplicate(self):
        client = FakeTropocastClient()
        service = self._service(client)
        service.refresh()
        result2 = service.refresh()
        self.assertEqual(result2, {"General (700km)": "sin_cambios"})
        with service._connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM tropocast_forecasts").fetchone()["c"]
        self.assertEqual(count, 1)

    def test_refresh_new_run_saves_second_row(self):
        client = FakeTropocastClient()
        service = self._service(client)
        service.refresh()
        client.responses_by_location = {
            (-38.158, -61.9524, 700): _make_response(run="2026-11-02T12:00:00Z")
        }
        result2 = service.refresh()
        self.assertEqual(result2, {"General (700km)": "ok"})
        with service._connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM tropocast_forecasts").fetchone()["c"]
        self.assertEqual(count, 2)

    def test_refresh_handles_rate_limited_without_crashing(self):
        client = FakeTropocastClient(raise_on_forecast=TropocastRateLimited(retry_after=60))
        service = self._service(client)
        result = service.refresh()
        self.assertEqual(result, {"General (700km)": "error"})
        self.assertIsNone(service._latest_for("General (700km)"))

    def test_refresh_handles_unauthorized_without_crashing(self):
        client = FakeTropocastClient(raise_on_forecast=TropocastUnauthorized("no key"))
        service = self._service(client)
        result = service.refresh()
        self.assertEqual(result, {"General (700km)": "error"})

    def test_refresh_handles_generic_error_without_crashing(self):
        client = FakeTropocastClient(raise_on_forecast=TropocastError(503, "disabled"))
        service = self._service(client)
        result = service.refresh()
        self.assertEqual(result, {"General (700km)": "error"})

    def test_one_location_failing_does_not_block_others(self):
        loc_ok = tc_service.Location("Bahía Blanca", -38.7196, -62.2724, 100)
        loc_fail = tc_service.Location("Mar del Plata", -37.9975, -57.5428, 100)
        client = FakeTropocastClient(
            responses_by_location={(-38.7196, -62.2724, 100): _make_response()},
        )

        # fetch_forecast normal para Bahía Blanca, pero RuntimeError para
        # Mar del Plata -- simulado envolviendo fetch_forecast.
        original_fetch = client.fetch_forecast

        def flaky_fetch(lat, lon, radius_km, **kwargs):
            if (lat, lon, radius_km) == (-37.9975, -57.5428, 100):
                raise RuntimeError("boom")
            return original_fetch(lat, lon, radius_km, **kwargs)

        client.fetch_forecast = flaky_fetch
        service = self._service(client, locations=[loc_ok, loc_fail])
        result = service.refresh()
        self.assertEqual(result["Bahía Blanca"], "ok")
        self.assertEqual(result["Mar del Plata"], "error")
        self.assertIsNotNone(service._latest_for("Bahía Blanca"))


class TropocastServiceQueryTests(unittest.TestCase):
    def setUp(self):
        self.db_path = _tmp_db_path()
        self.client = FakeTropocastClient()
        self.locations = [
            tc_service.Location("General (700km)", -38.158, -61.9524, 700),
            tc_service.Location("Bahía Blanca", -38.7196, -62.2724, 100),
            tc_service.Location("Mar del Plata", -37.9975, -57.5428, 100),
        ]
        self.service = tc_service.TropocastService(
            client=self.client, db_path=self.db_path, locations=self.locations
        )

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_general_summary_before_any_refresh(self):
        msg = self.service.general_summary()
        self.assertIn("Todavía no hay pronóstico", msg)

    def test_unavailable_message_when_disabled(self):
        disabled_service = tc_service.TropocastService(
            client=FakeTropocastClient(enabled=False), db_path=_tmp_db_path()
        )
        self.assertFalse(disabled_service.enabled)
        self.assertIn("no disponible", disabled_service.general_summary())

    def test_general_summary_uses_real_summary_fields(self):
        self.service.refresh()
        msg = self.service.general_summary("General (700km)")
        self.assertIn("mejores condiciones", msg.lower())
        self.assertIn("71/100", msg)

    def test_favorable_windows_filters_by_level(self):
        self.service.refresh()
        msg = self.service.favorable_windows("General (700km)", min_level="enhanced")
        # Frames con score >= 65: offset 9 (65), 15 (71), 57 (66), 63 (70) -- 4 ventanas
        for expected_score in ("65/100", "71/100", "66/100", "70/100"):
            self.assertIn(expected_score, msg)
        self.assertNotIn("39/100", msg)   # score 39 (slightly_enhanced) no debe aparecer
        self.assertNotIn("48/100", msg)   # score 48 (slightly_enhanced) tampoco
        self.assertEqual(len(msg.splitlines()), 1 + 4)  # encabezado + 4 ventanas

    def test_favorable_windows_empty_when_nothing_qualifies(self):
        self.client.responses_by_location = {
            (-38.158, -61.9524, 700): _make_response(frames=[_frame(0, 10, "x")])
        }
        self.service.refresh()
        msg = self.service.favorable_windows("General (700km)", min_level="very_strongly_enhanced")
        self.assertIn("sin ventanas", msg.lower())

    def test_sector_forecast_is_per_location(self):
        self.client.responses_by_location = {
            (-38.158, -61.9524, 700): _make_response(),
            (-38.7196, -62.2724, 100): _make_response(),
        }
        self.service.refresh()
        msg = self.service.sector_forecast("Bahía Blanca")
        self.assertIn("Bahía Blanca", msg)

    def test_sector_list_matches_configured_locations(self):
        self.assertEqual(
            self.service.sector_list(),
            ["General (700km)", "Bahía Blanca", "Mar del Plata"],
        )

    def test_corridor_candidates_finds_overlap(self):
        shared_frames = [_frame(15, 71, "03/11/2026 06:00 -03")]
        self.client.responses_by_location = {
            (-38.7196, -62.2724, 100): _make_response(frames=shared_frames),
            (-37.9975, -57.5428, 100): _make_response(frames=shared_frames),
        }
        self.service.refresh()
        msg = self.service.corridor_candidates()
        self.assertIn("Bahía Blanca", msg)
        self.assertIn("Mar del Plata", msg)
        self.assertIn("no son enlaces garantizados", msg)

    def test_corridor_candidates_none_when_no_overlap(self):
        self.client.responses_by_location = {
            (-38.7196, -62.2724, 100): _make_response(frames=[_frame(0, 10, "x")]),
            (-37.9975, -57.5428, 100): _make_response(frames=[_frame(0, 10, "y")]),
        }
        self.service.refresh()
        msg = self.service.corridor_candidates()
        self.assertIn("Sin corredores", msg)

    def test_freshness_reports_age_and_status(self):
        self.service.refresh()
        msg = self.service.freshness()
        self.assertIn("General (700km)", msg)
        self.assertIn("complete", msg)

    def test_prune_old_data_removes_old_rows(self):
        self.service.refresh()  # guarda 1 fila por cada una de las 3 locations de este fixture
        old_ts = tc_service.time.time() - 31 * 86400
        with self.service._connect() as conn:
            conn.execute("UPDATE tropocast_forecasts SET fetched_at = ?", (int(old_ts),))
        deleted = self.service.prune_old_data(days=30)
        self.assertEqual(deleted, len(self.locations))
        self.assertIsNone(self.service._latest_for("General (700km)"))


class PropagacionMenuModuleTests(unittest.TestCase):
    def setUp(self):
        self.db_path = _tmp_db_path()
        self.client = FakeTropocastClient()
        self.locations = [
            tc_service.Location("General (700km)", -38.158, -61.9524, 700),
            tc_service.Location("Bahía Blanca", -38.7196, -62.2724, 100),
        ]
        self.service = tc_service.TropocastService(
            client=self.client, db_path=self.db_path, locations=self.locations
        )
        self.service.refresh()

        class _FakeBbsSystem:
            pass

        self.bbs = _FakeBbsSystem()
        self.bbs.tropocast_service = self.service

        import importlib
        self.module = importlib.import_module("modules.propagacion")

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_menu_name_set(self):
        self.assertEqual(self.module.menu_name, "Pronóstico Troposférico")

    def test_option_1_general_summary(self):
        result = self.module.process_command("!user", "1", self.bbs)
        self.assertIn("General (700km)", result)

    def test_option_2_favorable_windows(self):
        result = self.module.process_command("!user", "2", self.bbs)
        self.assertIn("Elevadas", result)

    def test_option_3_lists_sectors(self):
        result = self.module.process_command("!user", "3", self.bbs)
        self.assertIn("s1", result)
        self.assertIn("Bahía Blanca", result)

    def test_sector_selection_by_code(self):
        result = self.module.process_command("!user", "s2", self.bbs)
        self.assertIn("Bahía Blanca", result)

    def test_sector_selection_invalid_code(self):
        result = self.module.process_command("!user", "s99", self.bbs)
        self.assertIn("inválido", result.lower())

    def test_option_4_corridors(self):
        result = self.module.process_command("!user", "4", self.bbs)
        self.assertTrue(len(result) > 0)

    def test_option_5_freshness(self):
        result = self.module.process_command("!user", "5", self.bbs)
        self.assertIn("Vigencia", result)

    def test_option_7_goes_back(self):
        self.assertEqual(self.module.process_command("!user", "7", self.bbs), "__back__")

    def test_cd_dot_dot_not_handled_as_back(self):
        # A diferencia de otros módulos, acá "7" es el único back -- esto
        # documenta el comportamiento actual, no una limitación nueva.
        result = self.module.process_command("!user", "cd ..", self.bbs)
        self.assertNotEqual(result, "__back__")

    def test_help_option(self):
        result = self.module.process_command("!user", "ayuda", self.bbs)
        self.assertIn("Ayuda", result)

    def test_invalid_option(self):
        result = self.module.process_command("!user", "99", self.bbs)
        self.assertIn("inválida", result.lower())

    def test_empty_command_shows_menu(self):
        result = self.module.process_command("!user", "", self.bbs)
        self.assertIn("PROPAGACIÓN", result)

    def test_responses_fit_meshtastic_chunking_reasonably(self):
        """No exige un único paquete (180 bytes), pero sí que ninguna
        respuesta sea desmedida -- un par de chunks como mucho."""
        for cmd in ("1", "2", "4", "5"):
            result = self.module.process_command("!user", cmd, self.bbs)
            self.assertLess(len(result), 180 * 4, f"respuesta de '{cmd}' demasiado larga: {len(result)} chars")

    def test_module_does_not_import_tropocast_client(self):
        """Garantía estructural de que el menú nunca abre conexiones de red
        -- solo lee lo que tropocast_service ya tiene guardado."""
        source_path = os.path.join(os.path.dirname(__file__), "..", "modules", "propagacion.py")
        with open(source_path) as f:
            source = f.read()
        self.assertNotIn("tropocast_client", source)
        self.assertNotIn("import requests", source)


if __name__ == "__main__":
    unittest.main()
