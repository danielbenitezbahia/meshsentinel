"""Tests para modules/Juegos/escape_room_criollo.py.

Cubre el mapa completo (7 ubicaciones + salida), cada puzzle (linterna+pila,
caja fuerte con codigo, ropero+llave, cableado de la antena con reintentos,
panel de breakers del sotano a oscuras, la decision final con sus dos
finales), el diario coleccionable en sus 3 estados, casos borde/invalidos,
persistencia de estado por usuario, y los dos recorridos completos felices
(final "cortar" y final "dejar"). No depende de meshtastic/pubsub -- el
modulo del juego es puro.
"""
import importlib.util
import os
import unittest

MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "modules", "Juegos", "escape_room_criollo.py"
)
spec = importlib.util.spec_from_file_location("escape_room_criollo", MODULE_PATH)
erc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(erc)


class FakeBBS:
    """Stub minimo de bbs_system: el juego solo toca .users."""

    def __init__(self):
        self.users = {}


class ERCTestBase(unittest.TestCase):
    def setUp(self):
        self.bbs = FakeBBS()
        self.user = "!testuser"

    def send(self, command, user=None):
        return erc.process_command(user or self.user, command, self.bbs)

    def game(self, user=None):
        return self.bbs.users[user or self.user]["escape_room_criollo"]

    def get_pila(self):
        self.send("norte")          # entrada -> deposito (no hace falta, solo para variar)
        self.send("sur")            # vuelta a entrada
        self.send("este")           # entrada -> cocina
        self.send("examinar alacena")
        self.send("recoger pila")
        self.send("oeste")          # cocina -> entrada

    def get_linterna(self):
        self.send("examinar perchero")
        self.send("recoger linterna")

    def arreglar_linterna_completo(self):
        """Desde la entrada: consigue linterna + pila y la arregla."""
        self.get_linterna()
        self.get_pila()
        return self.send("arreglar linterna")

    def resolver_caja(self):
        """Desde la entrada: va al deposito y abre la caja fuerte."""
        self.send("norte")  # entrada -> deposito
        return self.send("usar 1503")

    def resolver_ropero(self):
        """Requiere haber resuelto la caja antes (para tener la llave). Deja al jugador en el dormitorio."""
        self.send("este")  # deposito -> dormitorio
        return self.send("usar llave")

    def resolver_cableado(self):
        """Desde la entrada: va a la sala de equipos y conecta los 4 cables en orden."""
        self.send("este")   # entrada -> cocina
        self.send("norte")  # cocina -> sala_equipos
        for color in ["verde", "rojo", "amarillo", "azul"]:
            out = self.send(f"conectar {color}")
        self.send("sur")    # sala_equipos -> cocina
        self.send("oeste")  # cocina -> entrada
        return out


class TestInitAndBack(ERCTestBase):
    def test_back_with_0(self):
        self.assertEqual(self.send("0"), "__back__")

    def test_back_with_cd_dotdot(self):
        self.assertEqual(self.send("cd .."), "__back__")

    def test_first_command_initializes_game(self):
        self.send("examinar")
        game = self.game()
        self.assertEqual(game["current_room"], "entrada")
        self.assertEqual(game["inventory"], [])
        self.assertFalse(game["linterna_arreglada"])
        self.assertFalse(game["caja_abierta"])
        self.assertFalse(game["ropero_abierto"])
        self.assertEqual(game["cableado_progreso"], 0)
        self.assertFalse(game["cableado_resuelto"])
        self.assertFalse(game["breaker_resuelto"])
        self.assertIsNone(game["final_decidido"])
        self.assertFalse(game["terminado"])

    def test_state_persists_across_calls(self):
        self.send("este")
        self.send("oeste")
        self.assertEqual(self.game()["current_room"], "entrada")

    def test_independent_state_per_user(self):
        self.send("este", user="!a")
        self.assertEqual(self.game(user="!a")["current_room"], "cocina")
        self.send("examinar", user="!b")
        self.assertEqual(self.game(user="!b")["current_room"], "entrada")


class TestMovementAndMap(ERCTestBase):
    def test_examine_room_shows_exits(self):
        out = self.send("examinar")
        self.assertIn("Salidas: este, norte, afuera", out)

    def test_invalid_direction_from_entrada(self):
        self.assertEqual(self.send("arriba"), "No podés ir hacia ahí.")

    def test_moverse_muestra_salidas_de_la_nueva_sala(self):
        # Reporte real de playtesting: al moverse se describia la sala nueva
        # pero no se mostraban sus salidas (solo "examinar" las mostraba).
        out = self.send("este")  # entrada -> cocina
        self.assertIn("Salidas: oeste, norte.", out)

    def test_moverse_muestra_salidas_en_sala_con_una_sola_salida(self):
        self.send("norte")   # deposito
        self.send("este")    # dormitorio
        out = self.send("abajo")  # sotano, unica salida: arriba
        self.assertIn("Salidas: arriba.", out)

    def test_full_map_is_connected(self):
        # entrada -> cocina -> sala_equipos -> cocina -> entrada -> deposito ->
        # dormitorio -> sotano -> dormitorio -> deposito -> entrada
        self.assertIn("cocina", self.send("este"))
        self.assertIn("sala de equipos", self.send("norte"))
        self.assertIn("cocina", self.send("sur"))
        self.assertIn("entrada", self.send("oeste"))
        self.assertIn("depósito", self.send("norte"))
        self.assertIn("dormitorio", self.send("este"))
        self.assertIn("sótano", self.send("abajo"))
        self.assertIn("dormitorio", self.send("arriba"))
        self.assertIn("depósito", self.send("oeste"))
        self.assertIn("entrada", self.send("sur"))
        self.assertEqual(self.game()["current_room"], "entrada")

    def test_torre_blocked_before_cableado(self):
        self.send("este")
        self.send("norte")  # sala_equipos
        out = self.send("arriba")
        self.assertIn("cableado de la antena está cortado", out)
        self.assertEqual(self.game()["current_room"], "sala_equipos")

    def test_torre_accessible_after_cableado(self):
        self.resolver_cableado()
        self.send("este")
        self.send("norte")  # sala_equipos
        out = self.send("arriba")
        self.assertIn("antena", out)
        self.assertEqual(self.game()["current_room"], "torre")

    def test_afuera_blocked_before_decision(self):
        out = self.send("afuera")
        self.assertEqual(out, "La puerta está trabada, no cede ni un poco.")
        self.assertFalse(self.game()["terminado"])


class TestMapaComando(ERCTestBase):
    # Pedido explicito: mapa compacto de SOLO la sala actual y sus salidas
    # inmediatas (no todo lo descubierto), por el espacio limitado en ancho
    # (celular) y alto (tamaño de mensaje del mesh).
    def test_mapa_muestra_posicion_actual(self):
        out = self.send("mapa")
        self.assertIn("Estás en la entrada", out)

    def test_mapa_en_entrada_muestra_salidas_inmediatas(self):
        out = self.send("mapa")
        self.assertIn("Norte: el depósito", out)
        self.assertIn("Este: la cocina", out)
        self.assertIn("afuera", out)

    def test_mapa_no_muestra_salas_no_adyacentes(self):
        out = self.send("mapa")
        self.assertNotIn("sótano", out)
        self.assertNotIn("dormitorio", out)

    def test_mapa_en_sala_con_una_sola_salida(self):
        self.send("norte")  # deposito
        self.send("este")   # dormitorio
        self.send("abajo")  # sotano
        out = self.send("mapa")
        self.assertIn("Estás en el sótano", out)
        self.assertIn("Arriba: el dormitorio", out)

    def test_mapa_cambia_al_moverse(self):
        self.send("este")  # entrada -> cocina
        out = self.send("mapa")
        self.assertIn("Estás en la cocina", out)
        self.assertIn("Oeste: la entrada", out)
        self.assertIn("Norte: la sala de equipos", out)

    def test_mapa_no_depende_de_haber_examinado_antes(self):
        # A diferencia del viejo mapa acumulativo, este no necesita ningun
        # estado de "descubierto" -- solo mira las salidas de la sala actual.
        out = self.send("mapa")
        self.assertNotIn("Comando inválido", out)


class TestLinternaPuzzle(ERCTestBase):
    def test_examine_perchero_reveals_linterna(self):
        out = self.send("examinar perchero")
        self.assertIn("linterna", out)
        self.assertIn("linterna", self.game()["rooms"]["entrada"]["items"])

    def test_perchero_empty_after_pickup(self):
        self.send("examinar perchero")
        self.send("recoger linterna")
        out = self.send("examinar perchero")
        self.assertIn("vacío", out)

    def test_arreglar_sin_linterna_falla(self):
        self.get_pila()
        out = self.send("arreglar linterna")
        self.assertEqual(out, "No tenés ninguna linterna.")

    def test_arreglar_sin_pila_falla(self):
        self.get_linterna()
        out = self.send("arreglar linterna")
        self.assertEqual(out, "La linterna necesita una pila para funcionar.")

    def test_arreglar_linterna_exitoso(self):
        out = self.arreglar_linterna_completo()
        self.assertIn("Prende con una luz amarilla", out)
        self.assertTrue(self.game()["linterna_arreglada"])

    def test_arreglar_linterna_dos_veces(self):
        self.arreglar_linterna_completo()
        out = self.send("arreglar linterna")
        self.assertIn("ya está arreglada", out)

    def test_usar_linterna_es_alias_de_arreglar(self):
        # Reporte real de playtesting: "usar linterna" es el comando mas
        # intuitivo (consistente con el resto del juego) y antes no andaba.
        self.get_linterna()
        self.get_pila()
        out = self.send("usar linterna")
        self.assertIn("Prende con una luz amarilla", out)
        self.assertTrue(self.game()["linterna_arreglada"])

    def test_usar_pila_es_alias_de_arreglar(self):
        self.get_linterna()
        self.get_pila()
        out = self.send("usar pila")
        self.assertIn("Prende con una luz amarilla", out)
        self.assertTrue(self.game()["linterna_arreglada"])

    def test_bateria_es_sinonimo_de_pila_para_recoger(self):
        self.send("este")  # entrada -> cocina, donde esta la alacena
        self.send("examinar alacena")
        out = self.send("recoger bateria")
        self.assertEqual(out, "Recogiste: pila.")
        self.assertIn("pila", self.game()["inventory"])

    def test_bateria_es_sinonimo_de_pila_para_usar(self):
        self.get_linterna()
        self.send("este")  # entrada -> cocina
        self.send("examinar alacena")
        self.send("recoger bateria")
        self.send("oeste")  # cocina -> entrada (arreglar linterna no exige sala, pero por las dudas)
        out = self.send("usar bateria")
        self.assertIn("Prende con una luz amarilla", out)
        self.assertTrue(self.game()["linterna_arreglada"])


class TestCajaFuertePuzzle(ERCTestBase):
    def test_examine_caja_antes(self):
        self.send("norte")
        out = self.send("examinar caja")
        self.assertIn("disco de combinación", out)
        self.assertIn("usar", out.lower())

    def test_codigo_en_habitacion_incorrecta_falla(self):
        out = self.send("usar 1503")  # sigue en entrada
        self.assertEqual(out, "No podés usar eso acá.")
        self.assertFalse(self.game()["caja_abierta"])

    def test_usar_caja_sin_codigo_da_pista(self):
        # Reporte real de playtesting: "usar caja" (el primer intento mas
        # obvio) no daba ninguna pista de la sintaxis real ("usar <codigo>").
        self.send("norte")  # deposito
        out = self.send("usar caja")
        self.assertIn("usar", out.lower())
        self.assertIn("1234", out)
        self.assertFalse(self.game()["caja_abierta"])

    def test_usar_caja_cuando_ya_esta_abierta(self):
        self.resolver_caja()
        out = self.send("usar caja")
        self.assertEqual(out, "La caja fuerte ya está abierta.")

    def test_codigo_incorrecto_da_feedback_claro(self):
        self.send("norte")  # deposito
        out = self.send("usar 9999")
        self.assertIn("no pasa nada", out.lower())
        self.assertFalse(self.game()["caja_abierta"])

    def test_codigo_incorrecto_dos_veces_sigue_cerrada(self):
        self.send("norte")  # deposito
        self.send("usar 1111")
        out = self.send("usar 1503")
        self.assertIn("clic", out.lower())
        self.assertTrue(self.game()["caja_abierta"])

    def test_usar_objeto_no_relacionado_en_deposito_sigue_invalido(self):
        self.send("norte")  # deposito
        out = self.send("usar anemometro")
        self.assertEqual(out, "No podés usar eso acá.")

    def test_abrir_caja_con_codigo(self):
        out = self.resolver_caja()
        self.assertIn("clic", out.lower())
        self.assertTrue(self.game()["caja_abierta"])
        self.assertIn("llave", self.game()["rooms"]["deposito"]["items"])
        self.assertIn("pagina1", self.game()["rooms"]["deposito"]["items"])

    def test_abrir_caja_dos_veces(self):
        self.resolver_caja()
        out = self.send("usar 1503")
        self.assertEqual(out, "La caja fuerte ya está abierta.")

    def test_examine_caja_despues(self):
        self.resolver_caja()
        out = self.send("examinar caja")
        self.assertEqual(out, "La caja fuerte está abierta.")


class TestRoperoPuzzle(ERCTestBase):
    def test_usar_llave_sin_tenerla_falla(self):
        self.send("norte")  # deposito
        self.send("este")   # dormitorio
        out = self.send("usar llave")
        self.assertEqual(out, "No tenés ninguna llave.")

    def test_abrir_ropero_con_llave(self):
        self.resolver_caja()
        self.send("recoger llave")
        out = self.resolver_ropero()
        self.assertIn("ropero se abre", out)
        self.assertTrue(self.game()["ropero_abierto"])
        self.assertIn("pagina2", self.game()["rooms"]["dormitorio"]["items"])

    def test_abrir_ropero_dos_veces(self):
        self.resolver_caja()
        self.send("recoger llave")
        self.resolver_ropero()
        out = self.send("usar llave")
        self.assertEqual(out, "El ropero ya está abierto.")

    def test_examine_ropero_antes_y_despues(self):
        self.send("norte")
        self.send("este")
        out_antes = self.send("examinar ropero")
        self.assertIn("cerrado con llave", out_antes)
        self.send("oeste")
        self.resolver_caja()
        self.send("recoger llave")
        self.send("este")
        self.send("usar llave")
        out_despues = self.send("examinar ropero")
        self.assertIn("abierto", out_despues)


class TestCableadoPuzzle(ERCTestBase):
    def goto_sala_equipos(self):
        self.send("este")
        self.send("norte")

    def test_conectar_fuera_de_sala_equipos_falla(self):
        out = self.send("conectar verde")
        self.assertEqual(out, "No hay ningún cable para conectar acá.")

    def test_color_invalido(self):
        self.goto_sala_equipos()
        out = self.send("conectar rosa")
        self.assertEqual(out, "No hay un cable de ese color acá.")

    def test_orden_correcto_progresivo(self):
        self.goto_sala_equipos()
        out1 = self.send("conectar verde")
        self.assertIn("1/4", out1)
        out2 = self.send("conectar rojo")
        self.assertIn("2/4", out2)
        out3 = self.send("conectar amarillo")
        self.assertIn("3/4", out3)
        out4 = self.send("conectar azul")
        self.assertIn("cableado está resuelto", out4)
        self.assertTrue(self.game()["cableado_resuelto"])

    def test_orden_incorrecto_resetea(self):
        self.goto_sala_equipos()
        self.send("conectar verde")
        self.send("conectar amarillo")  # fuera de orden
        self.assertEqual(self.game()["cableado_progreso"], 0)
        # Confirmar que hay que arrancar de nuevo desde verde
        out = self.send("conectar rojo")
        self.assertEqual(self.game()["cableado_progreso"], 0)
        self.assertIn("chispa", out)

    def test_conectar_despues_de_resuelto(self):
        self.goto_sala_equipos()
        for color in ["verde", "rojo", "amarillo", "azul"]:
            self.send(f"conectar {color}")
        out = self.send("conectar verde")
        self.assertEqual(out, "Los cables ya están bien conectados.")

    def test_examine_panel_progreso_y_resuelto(self):
        self.goto_sala_equipos()
        out_inicial = self.send("examinar panel")
        self.assertIn("0/4", out_inicial)
        self.send("conectar verde")
        out_progreso = self.send("examinar panel")
        self.assertIn("1/4", out_progreso)
        self.send("conectar rojo")
        self.send("conectar amarillo")
        self.send("conectar azul")
        out_final = self.send("examinar panel")
        self.assertIn("bien conectados", out_final)


class TestSotanoYBreaker(ERCTestBase):
    def goto_sotano(self):
        self.send("norte")  # deposito
        self.send("este")   # dormitorio
        self.send("abajo")  # sotano

    def test_examine_a_oscuras(self):
        self.goto_sotano()
        out = self.send("examinar panel")
        self.assertEqual(out, "Está muy oscuro para ver nada. Vas a necesitar algo de luz.")

    def test_examine_cualquier_cosa_a_oscuras(self):
        self.goto_sotano()
        out = self.send("examinar marciano")
        self.assertEqual(out, "Está muy oscuro para ver nada. Vas a necesitar algo de luz.")

    def test_prender_breaker_a_oscuras_falla(self):
        self.goto_sotano()
        out = self.send("prender breaker 3")
        self.assertEqual(out, "Está muy oscuro para encontrar nada.")

    def test_prender_breaker_fuera_del_sotano_falla(self):
        self.arreglar_linterna_completo()
        out = self.send("prender breaker 3")
        self.assertEqual(out, "No hay ningún breaker acá.")

    def test_prender_breaker_2_da_advertencia(self):
        self.arreglar_linterna_completo()
        self.goto_sotano()
        out = self.send("prender breaker 2")
        self.assertIn("advertía el cartel", out)
        self.assertFalse(self.game()["breaker_resuelto"])

    def test_prender_breaker_1_o_4_no_hace_nada(self):
        self.arreglar_linterna_completo()
        self.goto_sotano()
        out = self.send("prender breaker 1")
        self.assertIn("No pasa nada visible", out)
        self.assertFalse(self.game()["breaker_resuelto"])

    def test_prender_breaker_3_resuelve(self):
        self.arreglar_linterna_completo()
        self.goto_sotano()
        out = self.send("prender breaker 3")
        self.assertIn("se encienden del todo", out)
        self.assertTrue(self.game()["breaker_resuelto"])

    def test_examine_despues_de_resuelto(self):
        self.arreglar_linterna_completo()
        self.goto_sotano()
        self.send("prender breaker 3")
        out = self.send("examinar panel")
        self.assertIn("ya está prendido", out)


class TestDiario(ERCTestBase):
    def test_sin_paginas(self):
        self.assertEqual(self.send("leer diario"), "No tenés ninguna página del diario.")

    def test_con_una_pagina(self):
        self.resolver_caja()
        self.send("recoger pagina1")
        out = self.send("leer diario")
        self.assertIn("1 de 3", out)

    def test_con_dos_paginas(self):
        self.resolver_caja()
        self.send("recoger pagina1")
        self.send("recoger llave")
        self.resolver_ropero()
        self.send("recoger pagina2")
        out = self.send("leer diario")
        self.assertIn("2 de 3", out)

    def test_con_las_tres_paginas(self):
        self.resolver_caja()
        self.send("recoger pagina1")
        self.send("recoger llave")
        self.resolver_ropero()
        self.send("recoger pagina2")
        self.send("oeste")  # dormitorio -> deposito
        self.send("sur")    # deposito -> entrada
        self.resolver_cableado()
        self.send("este")
        self.send("norte")
        self.send("arriba")  # torre
        self.send("examinar antena")
        self.send("recoger pagina3")
        out = self.send("leer diario")
        self.assertIn("Ezequiel", out)
        self.assertIn("frecuencia", out)


class TestTorreYFinal(ERCTestBase):
    def goto_torre(self):
        self.resolver_cableado()
        self.send("este")
        self.send("norte")
        self.send("arriba")

    def test_cortar_fuera_de_torre_falla(self):
        out = self.send("cortar transmision")
        self.assertEqual(out, "Acá no hay ningún control para eso.")

    def test_dejar_fuera_de_torre_falla(self):
        out = self.send("dejar transmision")
        self.assertEqual(out, "Acá no hay ningún control para eso.")

    def test_examine_control(self):
        self.goto_torre()
        out = self.send("examinar control")
        self.assertIn("cortar transmision", out)
        self.assertIn("dejar transmision", out)

    def test_examinar_palancas_es_alias_de_control(self):
        # Reporte real de playtesting: la descripcion de la sala dice
        # "palancas", pero ese objeto se llama "control" -- debe reconocerse
        # con la palabra que el jugador realmente ve en pantalla.
        self.goto_torre()
        out = self.send("examinar palancas")
        self.assertIn("cortar transmision", out)
        self.assertIn("dejar transmision", out)

    def test_usar_palancas_sin_elegir_da_pista(self):
        self.goto_torre()
        out = self.send("usar palancas")
        self.assertIn("cortar transmision", out)
        self.assertIn("dejar transmision", out)
        self.assertIsNone(self.game()["final_decidido"])

    def test_usar_control_sin_elegir_da_pista(self):
        self.goto_torre()
        out = self.send("usar control")
        self.assertIn("cortar transmision", out)
        self.assertIn("dejar transmision", out)
        self.assertIsNone(self.game()["final_decidido"])

    def test_decidir_cortar(self):
        self.goto_torre()
        out = self.send("cortar transmision")
        self.assertIn("Silencio total", out)
        self.assertEqual(self.game()["final_decidido"], "cortar")

    def test_decidir_dejar(self):
        self.goto_torre()
        out = self.send("dejar transmision")
        self.assertIn("sigue zumbando", out)
        self.assertEqual(self.game()["final_decidido"], "dejar")

    def test_decidir_dos_veces_falla(self):
        self.goto_torre()
        self.send("cortar transmision")
        out = self.send("dejar transmision")
        self.assertIn("Ya tomaste una decisión", out)
        self.assertEqual(self.game()["final_decidido"], "cortar")  # no cambia

    def test_puerta_refleja_decision(self):
        out_antes = self.send("examinar puerta")
        self.assertIn("trabada, no cede", out_antes)
        self.goto_torre()
        self.send("cortar transmision")
        self.send("abajo")
        self.send("sur")
        self.send("oeste")  # de vuelta a la entrada
        out_despues = self.send("examinar puerta")
        self.assertIn("podría abrirse", out_despues)

    def test_afuera_despues_de_cortar(self):
        self.goto_torre()
        self.send("cortar transmision")
        self.send("abajo")
        self.send("sur")
        self.send("oeste")
        out = self.send("afuera")
        self.assertIn("estación por fin descansa", out)
        self.assertIn("Completaste la Estación El Jagüel", out)
        self.assertTrue(self.game()["terminado"])

    def test_afuera_despues_de_dejar(self):
        self.goto_torre()
        self.send("dejar transmision")
        self.send("abajo")
        self.send("sur")
        self.send("oeste")
        out = self.send("afuera")
        self.assertIn("todo en orden, cambio", out)
        self.assertIn("Completaste la Estación El Jagüel", out)
        self.assertTrue(self.game()["terminado"])

    def test_afuera_dos_veces(self):
        self.goto_torre()
        self.send("cortar transmision")
        self.send("abajo")
        self.send("sur")
        self.send("oeste")
        self.send("afuera")
        out = self.send("afuera")
        self.assertEqual(out, "La puerta ya está abierta. Podés irte cuando quieras.")


class TestMirarAliasDeExaminar(ERCTestBase):
    # El propio texto de ayuda del juego usa la palabra "mirar" para describir
    # la accion de ver la habitacion, pero antes solo "examinar" (y un "look"
    # en ingles que quedo colado) estaban reconocidos como comando real.
    def test_mirar_sin_objeto_muestra_sala(self):
        out = self.send("mirar")
        self.assertIn("galería de entrada", out)
        self.assertIn("Salidas:", out)

    def test_mirar_objeto_es_alias_de_examinar(self):
        out = self.send("mirar perchero")
        self.assertIn("linterna", out)
        self.assertIn("linterna", self.game()["rooms"]["entrada"]["items"])

    def test_look_en_ingles_ya_no_esta_reconocido(self):
        out = self.send("look")
        self.assertIn("Comando inválido", out)


class TestInventoryAndFallback(ERCTestBase):
    def test_inventario_vacio(self):
        self.assertEqual(self.send("inventario"), "Inventario: vacío")

    def test_inventario_con_items(self):
        self.send("examinar perchero")
        self.send("recoger linterna")
        self.assertEqual(self.send("inventario"), "Inventario: linterna")

    def test_comando_invalido_muestra_ayuda(self):
        out = self.send("bailar")
        self.assertIn("Comando inválido", out)
        self.assertIn("Movimiento: norte, sur, este, oeste, arriba, abajo", out)

    def test_recoger_sin_target(self):
        out = self.send("recoger")
        self.assertIn("Comando inválido", out)

    def test_usar_sin_target(self):
        out = self.send("usar")
        self.assertIn("Comando inválido", out)

    def test_recoger_objeto_inexistente(self):
        out = self.send("recoger silla")
        self.assertEqual(out, "No podés recoger eso.")

    def test_examinar_objeto_inexistente(self):
        out = self.send("examinar silla")
        self.assertEqual(out, "No ves eso acá.")


class TestFullHappyPathCortar(ERCTestBase):
    def test_recorrido_completo_final_cortar(self):
        out = self.send("examinar")
        self.assertIn("galería de entrada", out)

        # Linterna + pila
        self.send("examinar perchero")
        self.send("recoger linterna")
        self.send("este")
        self.send("examinar alacena")
        self.send("recoger pila")
        self.send("oeste")
        out = self.send("arreglar linterna")
        self.assertTrue(self.game()["linterna_arreglada"])

        # Caja fuerte -> llave + pagina1
        self.send("norte")
        self.send("usar 1503")
        self.send("recoger llave")
        self.send("recoger pagina1")

        # Ropero -> pagina2
        self.send("este")
        self.send("usar llave")
        self.send("recoger pagina2")

        # Sotano -> breaker
        self.send("abajo")
        self.send("prender breaker 3")
        self.assertTrue(self.game()["breaker_resuelto"])
        self.send("arriba")
        self.send("oeste")
        self.send("sur")

        # Cableado
        self.send("este")
        self.send("norte")
        for color in ["verde", "rojo", "amarillo", "azul"]:
            self.send(f"conectar {color}")
        self.assertTrue(self.game()["cableado_resuelto"])

        # Torre -> pagina3 + decision
        self.send("arriba")
        self.send("examinar antena")
        self.send("recoger pagina3")
        out = self.send("leer diario")
        self.assertIn("Ezequiel", out)
        out = self.send("cortar transmision")
        self.assertEqual(self.game()["final_decidido"], "cortar")

        # Volver y salir
        self.send("abajo")
        self.send("sur")
        self.send("oeste")
        out = self.send("afuera")
        self.assertIn("Completaste la Estación El Jagüel", out)
        self.assertTrue(self.game()["terminado"])


class TestFullHappyPathDejar(ERCTestBase):
    def test_recorrido_completo_final_dejar(self):
        """Mismo recorrido que el de 'cortar', usando los helpers, pero
        terminando con la otra rama de la decision final -- prueba que
        ambos finales son alcanzables de punta a punta."""
        self.arreglar_linterna_completo()  # termina en entrada
        self.resolver_caja()               # entrada -> deposito, abre caja
        self.send("recoger llave")
        self.send("recoger pagina1")
        self.resolver_ropero()             # deposito -> dormitorio, abre ropero
        self.send("recoger pagina2")
        self.send("abajo")                 # dormitorio -> sotano
        self.send("prender breaker 3")
        self.assertTrue(self.game()["breaker_resuelto"])
        self.send("arriba")                # sotano -> dormitorio
        self.send("oeste")                 # dormitorio -> deposito
        self.send("sur")                   # deposito -> entrada

        self.resolver_cableado()           # entrada -> ... -> entrada, cablea todo
        self.assertTrue(self.game()["cableado_resuelto"])

        self.send("este")                  # entrada -> cocina
        self.send("norte")                 # cocina -> sala_equipos
        self.send("arriba")                # sala_equipos -> torre
        self.send("examinar antena")
        self.send("recoger pagina3")
        out = self.send("leer diario")
        self.assertIn("Ezequiel", out)

        out = self.send("dejar transmision")
        self.assertEqual(self.game()["final_decidido"], "dejar")

        self.send("abajo")                 # torre -> sala_equipos
        self.send("sur")                   # sala_equipos -> cocina
        self.send("oeste")                 # cocina -> entrada
        out = self.send("afuera")
        self.assertIn("todo en orden, cambio", out)
        self.assertIn("Completaste la Estación El Jagüel", out)
        self.assertTrue(self.game()["terminado"])


if __name__ == "__main__":
    unittest.main()
