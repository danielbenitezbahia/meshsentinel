"""Tests para scheduled_broadcasts.py: la cola de envios programados al
canal MediumFast (comando 'programar'). Cubre: encolar, due() respeta la
hora, mark_sent saca de due() pero no de pending(), cancel() solo borra
pendientes, y que coexistan varios programados a la vez (a diferencia de
announcement.py). Usa un archivo sqlite temporal propio.
"""
import importlib.util
import os
import tempfile
import unittest

MODULE_PATH = os.path.join(os.path.dirname(__file__), "..", "scheduled_broadcasts.py")
spec = importlib.util.spec_from_file_location("scheduled_broadcasts", MODULE_PATH)
sb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sb)


class SBTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self._tmp.close()
        self._orig_path = sb.DB_PATH
        sb.DB_PATH = self._tmp.name
        sb.init_db()

    def tearDown(self):
        sb.DB_PATH = self._orig_path
        if os.path.exists(self._tmp.name):
            os.unlink(self._tmp.name)


class TestEnqueueAndDue(SBTestBase):
    def test_due_vacio_al_principio(self):
        self.assertEqual(sb.due(now=1000), [])

    def test_due_antes_de_la_hora_no_aparece(self):
        sb.enqueue("mensaje", scheduled_at_ts=2000, scheduled_at_display="25/10 20:00", created_by="!da4846ec")
        self.assertEqual(sb.due(now=1000), [])

    def test_due_cuando_llega_la_hora(self):
        qid = sb.enqueue("mensaje", scheduled_at_ts=2000, scheduled_at_display="25/10 20:00", created_by="!da4846ec")
        rows = sb.due(now=2000)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], (qid, "mensaje", "25/10 20:00"))

    def test_due_tambien_para_fechas_ya_pasadas(self):
        # Decision confirmada: una combinacion fecha+hora ya pasada se
        # envia en el proximo chequeo, no se descarta.
        sb.enqueue("viejo", scheduled_at_ts=500, scheduled_at_display="20/10 09:00", created_by="!da4846ec")
        rows = sb.due(now=99999)
        self.assertEqual(len(rows), 1)

    def test_due_ordenado_por_fecha(self):
        sb.enqueue("segundo", scheduled_at_ts=2000, scheduled_at_display="25/10 20:00", created_by="!x")
        sb.enqueue("primero", scheduled_at_ts=1000, scheduled_at_display="25/10 09:00", created_by="!x")
        rows = sb.due(now=5000)
        self.assertEqual([r[1] for r in rows], ["primero", "segundo"])

    def test_multiples_programados_coexisten(self):
        # A diferencia de announcement.py (un solo slot), acá pueden
        # convivir varios sin pisarse.
        sb.enqueue("uno", 1000, "25/10 09:00", "!da4846ec")
        sb.enqueue("dos", 1001, "25/10 20:00", "!da4846ec")
        sb.enqueue("tres", 1002, "26/10 09:00", "!da4846ec")
        self.assertEqual(len(sb.pending()), 3)


class TestMarkSent(SBTestBase):
    def test_mark_sent_lo_saca_de_due(self):
        qid = sb.enqueue("mensaje", 1000, "25/10 09:00", "!da4846ec")
        sb.mark_sent(qid)
        self.assertEqual(sb.due(now=5000), [])

    def test_mark_sent_tambien_lo_saca_de_pending(self):
        qid = sb.enqueue("mensaje", 1000, "25/10 09:00", "!da4846ec")
        sb.mark_sent(qid)
        self.assertEqual(sb.pending(), [])

    def test_mark_sent_no_afecta_a_otros_pendientes(self):
        qid1 = sb.enqueue("uno", 1000, "25/10 09:00", "!da4846ec")
        sb.enqueue("dos", 1001, "25/10 20:00", "!da4846ec")
        sb.mark_sent(qid1)
        self.assertEqual(len(sb.pending()), 1)
        self.assertEqual(sb.pending()[0][1], "dos")


class TestPending(SBTestBase):
    def test_pending_vacio_al_principio(self):
        self.assertEqual(sb.pending(), [])

    def test_pending_incluye_created_by(self):
        sb.enqueue("mensaje", 1000, "25/10 09:00", "!da4846ec")
        rows = sb.pending()
        self.assertEqual(rows[0][3], "!da4846ec")

    def test_pending_no_depende_de_la_hora_actual(self):
        # A diferencia de due(), pending() muestra todo lo no enviado,
        # sin importar si ya "vencio" o todavia falta.
        sb.enqueue("futuro", 999999999, "lejos", "!x")
        self.assertEqual(len(sb.pending()), 1)


class TestCancel(SBTestBase):
    def test_cancel_borra_un_pendiente(self):
        qid = sb.enqueue("mensaje", 1000, "25/10 09:00", "!da4846ec")
        self.assertTrue(sb.cancel(qid))
        self.assertEqual(sb.pending(), [])

    def test_cancel_id_inexistente_devuelve_false(self):
        self.assertFalse(sb.cancel(9999))

    def test_cancel_no_afecta_a_otros(self):
        qid1 = sb.enqueue("uno", 1000, "25/10 09:00", "!da4846ec")
        sb.enqueue("dos", 1001, "25/10 20:00", "!da4846ec")
        sb.cancel(qid1)
        self.assertEqual(len(sb.pending()), 1)
        self.assertEqual(sb.pending()[0][1], "dos")

    def test_cancel_de_uno_ya_enviado_devuelve_false(self):
        qid = sb.enqueue("mensaje", 1000, "25/10 09:00", "!da4846ec")
        sb.mark_sent(qid)
        self.assertFalse(sb.cancel(qid))


if __name__ == "__main__":
    unittest.main()
