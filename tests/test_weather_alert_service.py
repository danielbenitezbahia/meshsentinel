"""Tests para el agrupado/formato de alertas SMN compartido entre el
resumen diario del BBS (bbs_system.py: build_smn_messages_by_partido,
se manda por LoRa a las 11 y 18hs) y la pestaña Alertas de la webapp
(api.py: /api/alerts/smn-grouped). Esta lógica vivía como métodos de
BBSSystem sin usar self para nada -- se movió a weather_alert_service.py
el 2026-11-02 para no reimplementarla en TypeScript ni depender de
instanciar un BBSSystem completo (abre el puerto serie) solo para leer
colores/orden."""
import unittest
from unittest.mock import patch

import weather_alert_service as was


class SeverityColorTests(unittest.TestCase):
    def test_moderate_amarillo(self):
        self.assertEqual(was.severity_to_color("Moderate"), "🟡")

    def test_severe_naranja(self):
        self.assertEqual(was.severity_to_color("Severe"), "🟠")

    def test_extreme_rojo(self):
        self.assertEqual(was.severity_to_color("Extreme"), "🔴")

    def test_desconocido_o_vacio_blanco(self):
        self.assertEqual(was.severity_to_color("algo_raro"), "⚪")
        self.assertEqual(was.severity_to_color(""), "⚪")
        self.assertEqual(was.severity_to_color(None), "⚪")


class SeverityLabelTests(unittest.TestCase):
    """Nombre del color sin artículo ni sustantivo ("Alerta"/"Aviso") para
    no tener que elegir género gramatical -- pedido explícito del usuario."""

    def test_moderate_amarillo(self):
        self.assertEqual(was.severity_to_label("Moderate"), "Amarillo")

    def test_severe_naranja(self):
        self.assertEqual(was.severity_to_label("Severe"), "Naranja")

    def test_extreme_rojo(self):
        self.assertEqual(was.severity_to_label("Extreme"), "Rojo")

    def test_desconocido_o_vacio_sin_label(self):
        self.assertEqual(was.severity_to_label("algo_raro"), "")
        self.assertEqual(was.severity_to_label(""), "")
        self.assertEqual(was.severity_to_label(None), "")


class AlertIconTests(unittest.TestCase):
    def test_tormenta(self):
        self.assertEqual(was.alert_icon("TORMENTAS FUERTES"), "⛈")

    def test_viento(self):
        self.assertEqual(was.alert_icon("Viento Zonda"), "💨")

    def test_lluvia(self):
        self.assertEqual(was.alert_icon("Lluvias intensas"), "🌧")

    def test_nieve(self):
        self.assertEqual(was.alert_icon("Nieve"), "🌨")

    def test_niebla(self):
        self.assertEqual(was.alert_icon("Niebla densa"), "🌫")

    def test_sin_match_generico(self):
        self.assertEqual(was.alert_icon("Granizo"), "⚠")

    def test_vacio_generico(self):
        self.assertEqual(was.alert_icon(None), "⚠")

    def test_case_insensitive(self):
        self.assertEqual(was.alert_icon("TORMENTA"), "⛈")


class FormatAlertWindowTests(unittest.TestCase):
    def test_mismo_dia_mismo_momento(self):
        # Ambos dentro de la tarde (12-18hs) del mismo día.
        txt = was.format_alert_window_es("2026-11-02T13:00:00-03:00", "2026-11-02T17:00:00-03:00")
        self.assertEqual(txt, "lunes tarde")

    def test_mismo_dia_distinto_momento(self):
        txt = was.format_alert_window_es("2026-11-02T10:00:00-03:00", "2026-11-02T20:00:00-03:00")
        self.assertEqual(txt, "lunes mañana/noche")

    def test_dias_distintos(self):
        txt = was.format_alert_window_es("2026-11-02T22:00:00-03:00", "2026-11-03T08:00:00-03:00")
        self.assertEqual(txt, "lunes noche → martes mañana")

    def test_fecha_invalida_cae_a_vigente(self):
        self.assertEqual(was.format_alert_window_es("no-es-fecha", "tampoco"), "vigente")
        self.assertEqual(was.format_alert_window_es(None, None), "vigente")


class MergePartidoAlertsTests(unittest.TestCase):
    def test_duplicado_exacto_se_elimina(self):
        a = {"title": "Tormentas", "severity": "Severe", "onset": "2026-11-02T10:00:00-03:00", "expires": "2026-11-02T14:00:00-03:00"}
        merged = was.merge_partido_alerts([dict(a), dict(a)])
        self.assertEqual(len(merged), 1)

    def test_ventanas_contiguas_se_fusionan(self):
        # Gap de 2hs entre el fin de la primera y el inicio de la segunda (<=3h): se fusiona.
        a1 = {"title": "Tormentas", "severity": "Severe", "onset": "2026-11-02T08:00:00-03:00", "expires": "2026-11-02T12:00:00-03:00"}
        a2 = {"title": "Tormentas", "severity": "Severe", "onset": "2026-11-02T14:00:00-03:00", "expires": "2026-11-02T18:00:00-03:00"}
        merged = was.merge_partido_alerts([a1, a2])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["expires"], "2026-11-02T18:00:00-03:00")

    def test_ventanas_separadas_no_se_fusionan(self):
        # Gap de 6hs (>3h): quedan separadas.
        a1 = {"title": "Tormentas", "severity": "Severe", "onset": "2026-11-02T08:00:00-03:00", "expires": "2026-11-02T10:00:00-03:00"}
        a2 = {"title": "Tormentas", "severity": "Severe", "onset": "2026-11-02T16:00:00-03:00", "expires": "2026-11-02T18:00:00-03:00"}
        merged = was.merge_partido_alerts([a1, a2])
        self.assertEqual(len(merged), 2)

    def test_distinto_titulo_no_se_fusiona(self):
        a1 = {"title": "Tormentas", "severity": "Severe", "onset": "2026-11-02T08:00:00-03:00", "expires": "2026-11-02T12:00:00-03:00"}
        a2 = {"title": "Viento", "severity": "Severe", "onset": "2026-11-02T12:30:00-03:00", "expires": "2026-11-02T16:00:00-03:00"}
        merged = was.merge_partido_alerts([a1, a2])
        self.assertEqual(len(merged), 2)

    def test_ordena_por_severidad_extreme_primero(self):
        moderate = {"title": "Lluvias", "severity": "Moderate", "onset": "2026-11-02T08:00:00-03:00", "expires": "2026-11-02T10:00:00-03:00"}
        extreme = {"title": "Tormentas", "severity": "Extreme", "onset": "2026-11-02T12:00:00-03:00", "expires": "2026-11-02T14:00:00-03:00"}
        severe = {"title": "Viento", "severity": "Severe", "onset": "2026-11-02T16:00:00-03:00", "expires": "2026-11-02T18:00:00-03:00"}
        merged = was.merge_partido_alerts([moderate, extreme, severe])
        self.assertEqual([m["severity"] for m in merged], ["Extreme", "Severe", "Moderate"])


class OrderedPartidosTests(unittest.TestCase):
    def test_partido_order_primero_resto_alfabetico(self):
        ordered = was.ordered_partidos()
        self.assertEqual(ordered[:4], was.PARTIDO_ORDER)
        resto = ordered[4:]
        self.assertEqual(resto, sorted(resto))

    def test_todos_los_target_partidos_estan(self):
        ordered = was.ordered_partidos()
        self.assertEqual(set(ordered), set(was.TARGET_PARTIDOS.values()))


class GroupAlertsByPartidoTests(unittest.TestCase):
    def test_una_alerta_en_varios_partidos(self):
        alerts = [{"title": "x", "affected_partidos": ["Bahia Blanca", "Tornquist"]}]
        grouped = was.group_alerts_by_partido(alerts)
        self.assertIn("Bahia Blanca", grouped)
        self.assertIn("Tornquist", grouped)
        self.assertEqual(len(grouped["Bahia Blanca"]), 1)

    def test_sin_affected_partidos_no_rompe(self):
        alerts = [{"title": "x"}]
        self.assertEqual(was.group_alerts_by_partido(alerts), {})


class GetGroupedAlertsTests(unittest.TestCase):
    """get_grouped_alerts(): el punto de entrada único que usan tanto el
    resumen LoRa como la webapp."""

    def test_sin_alertas_devuelve_lista_vacia(self):
        with patch.object(was, "get_sudoeste_ba_alerts", return_value=[]):
            self.assertEqual(was.get_grouped_alerts(), [])

    def test_estructura_completa_con_una_alerta(self):
        alerts = [{
            "title": "Tormentas fuertes", "severity": "Severe",
            "onset": "2026-11-02T10:00:00-03:00", "expires": "2026-11-02T14:00:00-03:00",
            "affected_partidos": ["Bahia Blanca"],
        }]
        with patch.object(was, "get_sudoeste_ba_alerts", return_value=alerts):
            grouped = was.get_grouped_alerts()

        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["partido"], "Bahia Blanca")
        a = grouped[0]["alerts"][0]
        self.assertEqual(a["title"], "Tormentas fuertes")
        self.assertEqual(a["severity"], "Severe")
        self.assertEqual(a["severity_color"], "🟠")
        self.assertEqual(a["severity_label"], "Naranja")
        self.assertEqual(a["icon"], "⛈")
        self.assertEqual(a["window_text"], "lunes mañana/tarde")

    def test_respeta_el_orden_de_partidos(self):
        alerts = [
            {"title": "x", "severity": "Moderate", "onset": "2026-11-02T10:00:00-03:00",
             "expires": "2026-11-02T12:00:00-03:00", "affected_partidos": ["Tornquist"]},
            {"title": "y", "severity": "Moderate", "onset": "2026-11-02T10:00:00-03:00",
             "expires": "2026-11-02T12:00:00-03:00", "affected_partidos": ["Bahia Blanca"]},
        ]
        with patch.object(was, "get_sudoeste_ba_alerts", return_value=alerts):
            grouped = was.get_grouped_alerts()
        # Bahia Blanca va antes que Tornquist en PARTIDO_ORDER, aunque en
        # la lista de alertas de entrada venga después.
        self.assertEqual([g["partido"] for g in grouped], ["Bahia Blanca", "Tornquist"])


if __name__ == "__main__":
    unittest.main()
