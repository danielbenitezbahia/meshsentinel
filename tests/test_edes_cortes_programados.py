"""Tests para modules/edes_cortes_programados.py: el menu que lista los
cortes de EDES ya scrapeados, agrupados por localidad. Cubre: caso sin
cortes, agrupado/orden de localidades, filtro de fechas pasadas, texto
de ultima actualizacion, y volver. Monkeypatchea las funciones de
edes_scraper que el modulo llama (no toca la base real)."""
import importlib.util
import os
import unittest
from datetime import date, datetime, timedelta

MODULE_PATH = os.path.join(os.path.dirname(__file__), "..", "modules", "edes_cortes_programados.py")
spec = importlib.util.spec_from_file_location("edes_cortes_programados", MODULE_PATH)
menu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(menu)


def _outage(outage_date, locality, time_start, time_end, streets):
    return {
        "outage_date": outage_date, "locality": locality,
        "time_start": time_start, "time_end": time_end,
        "streets": streets, "raw_line": "raw",
    }


class ERCTestBase(unittest.TestCase):
    def setUp(self):
        self._orig_get_latest_outages = menu.edes_scraper.get_latest_outages
        self._orig_get_latest_scrape_ts = menu.edes_scraper.get_latest_scrape_ts

    def tearDown(self):
        menu.edes_scraper.get_latest_outages = self._orig_get_latest_outages
        menu.edes_scraper.get_latest_scrape_ts = self._orig_get_latest_scrape_ts

    def set_outages(self, rows, scrape_ts=1000):
        menu.edes_scraper.get_latest_outages = lambda: rows
        menu.edes_scraper.get_latest_scrape_ts = lambda: scrape_ts


class TestSinCortes(ERCTestBase):
    def test_sin_cortes_muestra_mensaje_fijo(self):
        self.set_outages([])
        out = menu.display_menu()
        self.assertIn("No hay cortes programados de EDES informados por el momento.", out)
        self.assertIn("0. ↩ Volver", out)

    def test_sin_corridas_todavia_no_rompe(self):
        self.set_outages([], scrape_ts=None)
        out = menu.display_menu()
        self.assertIn("No hay cortes", out)
        self.assertNotIn("Última actualización", out)


class TestConCortes(ERCTestBase):
    def test_un_corte_muestra_localidad_y_horario(self):
        self.set_outages([_outage(
            date.today().isoformat(), "BAHIA BLANCA", "09:00", "13:00",
            "Paunero, Fitz Roy, Brickman, Av. Colon",
        )])
        out = menu.display_menu()
        self.assertIn("BAHIA BLANCA", out)
        self.assertIn("09:00 a 13:00", out)
        self.assertIn("Av. Colon", out)
        self.assertNotIn("No hay cortes", out)

    def test_varios_cortes_misma_localidad_se_listan_todos(self):
        hoy = date.today().isoformat()
        self.set_outages([
            _outage(hoy, "BAHIA BLANCA", "09:00", "13:00", "Mitre"),
            _outage(hoy, "BAHIA BLANCA", "10:00", "14:00", "Rivadavia"),
        ])
        out = menu.display_menu()
        self.assertIn("Mitre", out)
        self.assertIn("Rivadavia", out)

    def test_orden_de_localidades_sigue_partido_order(self):
        hoy = date.today().isoformat()
        self.set_outages([
            _outage(hoy, "TORNQUIST", "09:00", "10:00", "x"),
            _outage(hoy, "BAHIA BLANCA", "09:00", "10:00", "y"),
            _outage(hoy, "MONTE HERMOSO", "09:00", "10:00", "z"),
        ])
        out = menu.display_menu()
        pos_bb = out.index("BAHIA BLANCA")
        pos_mh = out.index("MONTE HERMOSO")
        pos_to = out.index("TORNQUIST")
        self.assertLess(pos_bb, pos_mh)
        self.assertLess(pos_mh, pos_to)

    def test_localidad_fuera_de_la_lista_va_al_final_alfabetico(self):
        hoy = date.today().isoformat()
        self.set_outages([
            _outage(hoy, "PUNTA ALTA", "09:00", "10:00", "x"),
            _outage(hoy, "BAHIA BLANCA", "09:00", "10:00", "y"),
        ])
        out = menu.display_menu()
        self.assertLess(out.index("BAHIA BLANCA"), out.index("PUNTA ALTA"))

    def test_fecha_pasada_se_filtra(self):
        ayer = (date.today() - timedelta(days=1)).isoformat()
        self.set_outages([_outage(ayer, "BAHIA BLANCA", "09:00", "10:00", "x")])
        out = menu.display_menu()
        self.assertIn("No hay cortes", out)
        self.assertNotIn("BAHIA BLANCA", out)

    def test_fecha_de_hoy_no_se_filtra(self):
        hoy = date.today().isoformat()
        self.set_outages([_outage(hoy, "BAHIA BLANCA", "09:00", "10:00", "x")])
        out = menu.display_menu()
        self.assertIn("BAHIA BLANCA", out)

    def test_fecha_futura_no_se_filtra(self):
        mañana = (date.today() + timedelta(days=1)).isoformat()
        self.set_outages([_outage(mañana, "BAHIA BLANCA", "09:00", "10:00", "x")])
        out = menu.display_menu()
        self.assertIn("BAHIA BLANCA", out)

    def test_ultima_actualizacion_se_muestra(self):
        ts = int(datetime(2026, 10, 22, 8, 3).timestamp())
        self.set_outages(
            [_outage(date.today().isoformat(), "BAHIA BLANCA", "09:00", "10:00", "x")],
            scrape_ts=ts,
        )
        out = menu.display_menu()
        self.assertIn("Última actualización: 22/10 08:03", out)


class TestVolver(ERCTestBase):
    def test_back_with_0(self):
        self.set_outages([])
        self.assertEqual(menu.process_command("!u", "0", None), "__back__")

    def test_back_with_cd_dotdot(self):
        self.set_outages([])
        self.assertEqual(menu.process_command("!u", "cd ..", None), "__back__")
        self.assertEqual(menu.process_command("!u", "CD ..", None), "__back__")

    def test_otra_entrada_vuelve_a_mostrar_el_listado(self):
        self.set_outages([_outage(date.today().isoformat(), "BAHIA BLANCA", "09:00", "10:00", "x")])
        out = menu.process_command("!u", "1", None)
        self.assertIn("BAHIA BLANCA", out)


if __name__ == "__main__":
    unittest.main()
