"""Tests para announcement.py: el aviso general que reemplaza la frase
random del login mientras esta vigente. Cubre: autorizacion (solo los 3
nodos de confianza), guardado/lectura, vencimiento inclusive/exclusivo,
reemplazo de un aviso por otro, y archivo ausente/corrupto. Usa un
archivo temporal propio (no toca community_announcement.json real).
"""
import importlib.util
import os
import tempfile
import unittest
from datetime import date

MODULE_PATH = os.path.join(os.path.dirname(__file__), "..", "announcement.py")

spec = importlib.util.spec_from_file_location("announcement", MODULE_PATH)
announcement = importlib.util.module_from_spec(spec)
spec.loader.exec_module(announcement)


class AnnouncementTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self._tmp.close()
        os.unlink(self._tmp.name)  # empieza sin archivo, como un deploy nuevo
        self._orig_path = announcement.FILE_PATH
        announcement.FILE_PATH = self._tmp.name

    def tearDown(self):
        announcement.FILE_PATH = self._orig_path
        if os.path.exists(self._tmp.name):
            os.unlink(self._tmp.name)


class TestIsTrusted(unittest.TestCase):
    def test_los_tres_nodos_de_confianza(self):
        self.assertTrue(announcement.is_trusted("!da4846ec"))  # pilgrim
        self.assertTrue(announcement.is_trusted("!33686e48"))  # Nueva Belgrano
        self.assertTrue(announcement.is_trusted("!b215cff4"))  # Villa Churrinche

    def test_case_insensitive_y_sin_signo_de_exclamacion(self):
        self.assertTrue(announcement.is_trusted("!DA4846EC"))
        self.assertTrue(announcement.is_trusted("da4846ec"))

    def test_nodo_no_confiable_rechazado(self):
        self.assertFalse(announcement.is_trusted("!deadbeef"))
        self.assertFalse(announcement.is_trusted("!122bc3a0"))


class TestSetAndGet(AnnouncementTestBase):
    def test_sin_archivo_no_hay_aviso_activo(self):
        self.assertIsNone(announcement.get_active_announcement())

    def test_set_y_get_dentro_de_vigencia(self):
        announcement.set_announcement("Corte de luz el jueves.", "2026-10-30", "!da4846ec")
        active = announcement.get_active_announcement(today=date(2026, 10, 25))
        self.assertIsNotNone(active)
        self.assertEqual(active["text"], "Corte de luz el jueves.")
        self.assertEqual(active["expires"], date(2026, 10, 30))

    def test_vencimiento_es_inclusive_el_mismo_dia_todavia_se_muestra(self):
        announcement.set_announcement("Aviso.", "2026-10-30", "!da4846ec")
        active = announcement.get_active_announcement(today=date(2026, 10, 30))
        self.assertIsNotNone(active)

    def test_al_dia_siguiente_del_vencimiento_ya_no_se_muestra(self):
        announcement.set_announcement("Aviso.", "2026-10-30", "!da4846ec")
        active = announcement.get_active_announcement(today=date(2026, 10, 31))
        self.assertIsNone(active)

    def test_un_aviso_nuevo_reemplaza_al_anterior(self):
        announcement.set_announcement("Primero.", "2026-10-30", "!da4846ec")
        announcement.set_announcement("Segundo.", "2026-11-05", "!33686e48")
        active = announcement.get_active_announcement(today=date(2026, 10, 28))
        self.assertEqual(active["text"], "Segundo.")
        self.assertEqual(active["expires"], date(2026, 11, 5))

    def test_set_by_queda_normalizado(self):
        announcement.set_announcement("Aviso.", "2026-10-30", "DA4846EC")
        with open(announcement.FILE_PATH) as f:
            import json
            data = json.load(f)
        self.assertEqual(data["set_by"], "!da4846ec")

    def test_archivo_corrupto_no_rompe_devuelve_none(self):
        with open(announcement.FILE_PATH, "w") as f:
            f.write("esto no es json valido {{{")
        self.assertIsNone(announcement.get_active_announcement())

    def test_archivo_sin_campo_expires_devuelve_none(self):
        with open(announcement.FILE_PATH, "w") as f:
            import json
            json.dump({"text": "algo"}, f)
        self.assertIsNone(announcement.get_active_announcement())


if __name__ == "__main__":
    unittest.main()
