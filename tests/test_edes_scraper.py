"""Tests para edes_scraper.py: scraper de cortes programados de EDES.

Cubre: parseo contra el HTML real bajado a mano (fixture), casos
sinteticos (varias localidades el mismo dia, varios dias en la pagina,
pagina sin cortes), el bug ya encontrado y corregido de "Av." truncando
la lista de calles, inferencia de año sin romper en fin de año, y el
guardado/lectura en sqlite (que get_latest_outages() devuelva solo la
corrida mas reciente). No depende de red: parse() se prueba con HTML
fijo, fetch_html() no se llama en los tests.
"""
import importlib.util
import os
import tempfile
import unittest
from datetime import date

MODULE_PATH = os.path.join(os.path.dirname(__file__), "..", "edes_scraper.py")
FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "edes_sample.html")

spec = importlib.util.spec_from_file_location("edes_scraper", MODULE_PATH)
edes_scraper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(edes_scraper)


class TestParseRealFixture(unittest.TestCase):
    """HTML real bajado de https://www.infoedes.com/obras-en-tu-barrio/
    el 2026-10-22 (ver tests/fixtures/edes_sample.html)."""

    def setUp(self):
        with open(FIXTURE_PATH, encoding="utf-8") as f:
            self.html = f.read()

    def test_encuentra_las_dos_franjas_horarias(self):
        rows = edes_scraper.parse(self.html, reference=date(2026, 10, 22))
        self.assertEqual(len(rows), 2)

    def test_fecha_y_localidad_correctas(self):
        rows = edes_scraper.parse(self.html, reference=date(2026, 10, 22))
        for r in rows:
            self.assertEqual(r["outage_date"], "2026-10-22")
            self.assertEqual(r["locality"], "BAHIA BLANCA")

    def test_horarios_correctos(self):
        rows = edes_scraper.parse(self.html, reference=date(2026, 10, 22))
        self.assertEqual((rows[0]["time_start"], rows[0]["time_end"]), ("09:00", "13:00"))
        self.assertEqual((rows[1]["time_start"], rows[1]["time_end"]), ("10:00", "14:00"))

    def test_calles_con_abreviatura_av_no_se_truncan(self):
        # Bug real encontrado: "Av. Colon" se cortaba en "Av" porque el
        # regex paraba en el primer punto, sin saber que "Av." es una
        # abreviatura y no el fin de la lista.
        rows = edes_scraper.parse(self.html, reference=date(2026, 10, 22))
        self.assertIn("Av. Colon", rows[0]["streets"])
        self.assertNotEqual(rows[0]["streets"], "Paunero, Fitz Roy, Brickman, Av")

    def test_todas_las_calles_de_la_segunda_franja(self):
        rows = edes_scraper.parse(self.html, reference=date(2026, 10, 22))
        self.assertIn("Misiones", rows[1]["streets"])
        self.assertIn("Berutti", rows[1]["streets"])


class TestParseSintetico(unittest.TestCase):
    def test_sin_cortes_programados_devuelve_lista_vacia(self):
        html = "<html><body><p>No hay obras programadas por el momento.</p></body></html>"
        self.assertEqual(edes_scraper.parse(html, reference=date(2026, 10, 22)), [])

    def test_html_vacio_devuelve_lista_vacia(self):
        self.assertEqual(edes_scraper.parse("", reference=date(2026, 10, 22)), [])

    def test_varias_localidades_el_mismo_dia(self):
        html = """
        <p><b>Viernes 23 de octubre</b></p>
        <ul>
          <li><strong>BAHIA BLANCA</strong><br>– de 8 a 12hs, afecta al cuadrante comprendido por las calles: Mitre, Rivadavia.</li>
          <li><strong>PUNTA ALTA</strong><br>– de 14 a 18hs, afecta al cuadrante comprendido por las calles: San Martin.</li>
        </ul>
        """
        rows = edes_scraper.parse(html, reference=date(2026, 10, 22))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["locality"], "BAHIA BLANCA")
        self.assertEqual(rows[1]["locality"], "PUNTA ALTA")

    def test_varios_dias_en_la_misma_pagina(self):
        html = """
        <p><b>Jueves 22 de octubre</b></p>
        <ul><li><strong>BAHIA BLANCA</strong><br>– de 9 a 13hs, afecta al cuadrante comprendido por las calles: Mitre.</li></ul>
        <p><b>Viernes 23 de octubre</b></p>
        <ul><li><strong>TORNQUIST</strong><br>– de 10 a 15hs, afecta al cuadrante comprendido por las calles: Belgrano.</li></ul>
        """
        rows = edes_scraper.parse(html, reference=date(2026, 10, 22))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["outage_date"], "2026-10-22")
        self.assertEqual(rows[1]["outage_date"], "2026-10-23")
        self.assertEqual(rows[1]["locality"], "TORNQUIST")

    def test_li_sin_strong_se_ignora_sin_romper(self):
        html = """
        <p><b>Jueves 22 de octubre</b></p>
        <ul><li>algo sin formato esperado</li></ul>
        """
        self.assertEqual(edes_scraper.parse(html, reference=date(2026, 10, 22)), [])

    def test_entidades_html_se_decodifican(self):
        html = """
        <p><b>Jueves 22 de octubre</b></p>
        <ul><li><strong>BAH&Iacute;A BLANCA</strong><br>&#8211; de 9 a 13hs, afecta al cuadrante comprendido por las calles: Alem &amp; Rivadavia.</li></ul>
        """
        rows = edes_scraper.parse(html, reference=date(2026, 10, 22))
        self.assertEqual(rows[0]["locality"], "BAHÍA BLANCA")
        self.assertIn("&", rows[0]["streets"])


class TestResolveYear(unittest.TestCase):
    def test_mismo_año_en_caso_normal(self):
        year = edes_scraper._resolve_year(25, 10, date(2026, 10, 22))
        self.assertEqual(year, 2026)

    def test_fin_de_año_la_pagina_menciona_enero_del_siguiente(self):
        # Estamos en diciembre 2026 y la pagina ya anuncia un corte para
        # "lunes 5 de enero" -- debe inferir 2027, no 2026.
        year = edes_scraper._resolve_year(5, 1, date(2026, 12, 28))
        self.assertEqual(year, 2027)


class TestStorageRoundtrip(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self._tmp.close()
        self._orig_path = edes_scraper.DB_PATH
        edes_scraper.DB_PATH = self._tmp.name
        edes_scraper.init_db()

    def tearDown(self):
        edes_scraper.DB_PATH = self._orig_path
        if os.path.exists(self._tmp.name):
            os.unlink(self._tmp.name)

    def test_get_latest_outages_sin_datos(self):
        self.assertEqual(edes_scraper.get_latest_outages(), [])

    def test_store_y_get_latest_roundtrip(self):
        rows = [{
            "outage_date": "2026-10-22", "locality": "BAHIA BLANCA",
            "time_start": "09:00", "time_end": "13:00",
            "streets": "Mitre, Rivadavia", "raw_line": "raw",
        }]
        edes_scraper.store_scrape(rows, scraped_at=1000)
        latest = edes_scraper.get_latest_outages()
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["locality"], "BAHIA BLANCA")

    def test_get_latest_solo_devuelve_la_corrida_mas_reciente(self):
        viejo = [{
            "outage_date": "2026-10-20", "locality": "VIEJO",
            "time_start": "09:00", "time_end": "10:00",
            "streets": "x", "raw_line": "raw",
        }]
        nuevo = [{
            "outage_date": "2026-10-22", "locality": "NUEVO",
            "time_start": "09:00", "time_end": "10:00",
            "streets": "y", "raw_line": "raw",
        }]
        edes_scraper.store_scrape(viejo, scraped_at=1000)
        edes_scraper.store_scrape(nuevo, scraped_at=2000)
        latest = edes_scraper.get_latest_outages()
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["locality"], "NUEVO")

    def test_store_scrape_vacio_no_rompe(self):
        edes_scraper.store_scrape([], scraped_at=1000)
        self.assertEqual(edes_scraper.get_latest_outages(), [])


if __name__ == "__main__":
    unittest.main()
