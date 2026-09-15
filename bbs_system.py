import os
import importlib
import store_forward
import time
import logging
import bbs_users
import bbs_messages
import random
from datetime import datetime

from interface import Interface
import weather_alert_notifier
import weather_alert_service
import weather_alert_scraper
import short_term_alert_scraper
import traffic_stats
import firms_notifier
import firms_service

logger = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 2 * 60 * 60  # 2 horas
BBS_WELCOME_PHRASES = [
    "CQ CQ CQ… alguien está ahí afuera.",
    "Escuchando el silencio entre portadoras.",
    "El éter recuerda lo que los hombres olvidan.",
    "Las ondas viajan donde los pies no llegan.",
    "Cada nodo es una historia esperando ser oída.",
    "El ruido también contiene mensajes.",
    "DX no es distancia, es paciencia.",
    "Una portadora nunca está sola.",
    "El espectro está vivo.",
    "Señal débil, intención fuerte.",
    "Hay rutas que sólo existen cuando transmitís.",
    "Cuando el canal calla, la red sigue respirando.",
    "La propagación decide, pero vos insistís.",
    "Si escuchás lo suficiente, el mundo responde.",
    "La noche guarda mejores señales.",
    "Entre estática y luz, viaja tu mensaje.",
    "El camino más largo también entrega.",
    "Donde no hay cobertura, a veces hay eco.",
    "El silencio no es vacío: es espera.",
    "Todo enlace empieza con un intento.",
]

# Agosto 2026: frase fija en vez de la aleatoria (cita de "Neuromante", W. Gibson)
FIXED_WELCOME_PHRASE_PERIOD = (2026, 8)  # (año, mes)
FIXED_WELCOME_PHRASE = (
    "El cielo sobre el puerto tenía el color de una pantalla "
    "de televisor sintonizado en un canal muerto."
)


BRANDSEN_NODE_ID = "!33695e54"
WEATHER_REFRESH_SECONDS = 15  # 30 min
SMN_PUBLIC_CHANNEL = "SMN_Alertas"
SMN_BROADCAST_HOURS = {11, 18}  # horas locales en que se emite el resumen diario
SMN_REPORT_RETRY_SECONDS = 180  # reintento del resumen diario mientras no haya nada que reportar
SHORT_TERM_INTERVAL_SECONDS = 5 * 60  # cada 5 minutos
SMN_SECRET_COMMAND = "SMN NOW 9XQ7"
SMN_ALERTS_CHANNEL_INDEX = 1   # canal primario SMN_Alertas
SMN_ALERTS_CHANNEL_INDEX_2 = 2  # canal secundario SMN_Alertas (sin clave)
WEATHER_SCRAPER_INTERVAL_SECONDS = 10 * 60  # 10 minutos para actualizar alertas
FIRMS_POLL_INTERVAL_SECONDS = 10 * 60
FIRMS_DELIVERY_INTERVAL_SECONDS = 60
PARTIDO_ORDER = ["Bahia Blanca", "Monte Hermoso", "Coronel Dorrego", "Tornquist"]
SMN_BETWEEN_PARTIDOS_DELAY = 10  # segundos entre mensaje y mensaje
NEW_NODE_NOTIFY_NODES = ["!da4846ec", "!33686e48"]  # pilgrim, daniel movil

# Broadcasts programados a canal 0 — (año, mes, día, hora local AR, mensaje)
_SENTINEL_LOG_SLOTS = [
    ((2026, 6,  9, 20), ">> SENTINEL BBS // LOG PÚBLICO #1\n>> Red activa. 24hs de datos disponibles.\n>> URL: sentinelmesh.ar\n>> Que tal una partida de MeshWars?"),
    ((2026, 6, 12, 20), ">> SENTINEL BBS // LOG PÚBLICO #2\n>> Red activa. 24hs de datos disponibles.\n>> URL: sentinelmesh.ar\n>> Vamos Argentina!!!"),
    ((2026, 6, 14, 20), ">> SENTINEL BBS // LOG PÚBLICO #3\n>> Red activa. 24hs de datos disponibles.\n>> URL: sentinelmesh.ar\n>> Buen fin de semana!"),
    ((2026, 8, 21, 16), "Podés entrar a https://sentinelmesh.ar para ver la actividad en vivo de la Sudoeste Mesh."),
    ((2026, 8, 23, 16), "Podés entrar a https://sentinelmesh.ar para ver la actividad en vivo de la Sudoeste Mesh."),
    ((2026, 8, 28, 16), "Podés entrar a https://sentinelmesh.ar para ver la actividad en vivo de la Sudoeste Mesh."),
    ((2026, 8, 21, 14),
     "⚠️ Corrección SMNAlert: la clave que mandamos hoy a las 13hs tenía un carácter de menos. La clave correcta es:\n"
     "s5kHDQ1KmyL8e21jV/tZh5XA6MScZxOuC1WrgwysavY="),
]

# Invitación al canal SMNAlert — una vez por día en las fechas indicadas, al
# canal 0 (primario), en 3 mensajes separados para poder copiar/pegar fácil
# el nombre del canal y la clave.
SMNALERT_INVITE_DATES = {(2026, 8, 21), (2026, 8, 23), (2026, 8, 28)}
SMNALERT_INVITE_HOUR = 13  # hora local AR a la que se manda
SMNALERT_INVITE_MESSAGES = [
    "📡 SMNAlert es un canal que tenemos para reportar las alertas meteorológicas "
    "de muy corto plazo y los reportes de alertas meteorológicas activas, "
    "diariamente a las 11am y 18hs, para los partidos del sudoeste bonaerense.\n"
    "A continuación, el nombre del canal y la clave que tienen que agregar:",
    "SMNAlert",
    "s5kHDQ1KmyL8e21jV/tZh5XA6MScZxOuC1WrgwysavY=",
]

# Respuesta amistosa cuando alguien escribe en el canal SMNAlert preguntando/
# comentando algo — hasta 2 por día por persona (una variante cada vez).
SMNALERT_CHANNEL_REPLY_MESSAGES = [
    "😄 Todo bien por acá, no se preocupen — el canal funciona normal. Si no sabían de mí "
    "es porque no hay alertas meteorológicas vigentes para el sudoeste bonaerense en este "
    "momento. Pero gracias por preguntar, se agradece que les importe mi salud 🙏",
    "🤖 Sigo con vida, tranquilos. Simplemente no hay nada para alertar ahora mismo en la "
    "zona — por eso el silencio. Igual gracias por el chequeo, cualquier cosa rara les "
    "aviso enseguida 😉",
]


class BBSSystem:
    def __init__(self):
        self.users = {}
        self.menu_modules = self.load_menu_modules()
        self.interface = Interface()
        self.interface.handle_message = self.handle_message
        self.interface.on_channel_message = self.handle_channel_message
        self._last_smn_public_broadcast = 0
        self._last_smn_broadcast_content = None  # contenido del último broadcast emitido
        self._last_smn_report_attempt = 0  # último intento del resumen diario (para reintentos)
        self._smn_report_state = (None, None)  # (slot_key, contenido publicado) del resumen diario
        self._smnalert_channel_replies: dict = {}  # sender_id → (fecha, cantidad hoy)

        store_forward.init_db()
        bbs_users.init_db()
        bbs_messages.init_db()
        weather_alert_notifier.init_db()
        traffic_stats.init_db()
        self.firms_service = firms_service.FirmsService()

        self._last_delivery = 0
        self._last_weather_alert_delivery = 0
        self._last_short_term_run = 0
        self._last_weather_scraper_run = 0
        self._last_firms_poll = 0
        self._last_firms_delivery = 0
        self._last_firms_cleanup = 0
        self._last_cleanup_run = 0
        self._brandsen_weather = traffic_stats.load_brandsen_weather()
        self._last_brandsen_weather_refresh = 0

        self.interface.bbs = self

        def tick():
            logger.debug("SF tick called")
            now = time.time()

            # Nota: las marcas de "ya enviado" se persisten en SQLite
            # (traffic_stats.scheduled_broadcasts_sent), no solo en memoria,
            # para que un restart del proceso durante la misma hora no
            # dispare el mismo envío de nuevo.
            _now_dt = datetime.now()
            _today_str = _now_dt.strftime("%Y-%m-%d")

            if _now_dt.hour in SMN_BROADCAST_HOURS:
                _key = f"smn_report:{_today_str}:{_now_dt.hour}"
                if (now - self._last_smn_report_attempt) >= SMN_REPORT_RETRY_SECONDS:
                    self._last_smn_report_attempt = now
                    _already_sent = traffic_stats.broadcast_already_sent(_key)
                    _state_key, _prev_content = self._smn_report_state
                    if _state_key != _key:
                        _prev_content = None

                    # Envío inicial: se reintenta durante la hora hasta que el
                    # scraper (corre cada 10 min) tenga algo que reportar; solo
                    # se marca el slot cuando realmente se publicó algo.
                    # Ya enviado: se re-publica si el conjunto de alertas cambió
                    # (solo mientras este proceso conserve qué mandó).
                    if not _already_sent or _prev_content is not None:
                        _did_send, _content = self.broadcast_smn_daily_report(
                            hour=_now_dt.hour,
                            previous_content=_prev_content if _already_sent else None,
                        )
                        if _did_send:
                            self._smn_report_state = (_key, _content)
                            if not _already_sent:
                                traffic_stats.mark_broadcast_sent(_key)

            _now_key = (_now_dt.year, _now_dt.month, _now_dt.day, _now_dt.hour)
            for idx, (slot, _msg) in enumerate(_SENTINEL_LOG_SLOTS, start=1):
                if _now_key == slot:
                    _key = "sentinel_log:{}-{:02d}-{:02d}-{:02d}".format(*slot)
                    if not traffic_stats.broadcast_already_sent(_key):
                        traffic_stats.mark_broadcast_sent(_key)
                        try:
                            self.interface.send_channel_message(_msg, channel_index=0)
                            logger.info("Sentinel Log #%d enviado al canal 0", idx)
                        except Exception as _exc:
                            logger.warning("Error enviando Sentinel Log #%d: %s", idx, _exc)

            _today_ymd = (_now_dt.year, _now_dt.month, _now_dt.day)
            if _today_ymd in SMNALERT_INVITE_DATES and _now_dt.hour == SMNALERT_INVITE_HOUR:
                _key = f"smnalert_invite:{_today_str}"
                if not traffic_stats.broadcast_already_sent(_key):
                    traffic_stats.mark_broadcast_sent(_key)
                    self.send_smnalert_invite()

            if (now - self._last_delivery) >= store_forward.DELIVERY_INTERVAL_SECONDS:
                self._last_delivery = now
                self.deliver_pending()

            if (now - self._last_weather_alert_delivery) >= weather_alert_notifier.DELIVERY_INTERVAL_SECONDS:
                self._last_weather_alert_delivery = now
                self.deliver_weather_alerts()

            if (now - self._last_weather_scraper_run) >= WEATHER_SCRAPER_INTERVAL_SECONDS:
                self._last_weather_scraper_run = now
                self.refresh_weather_alerts()

            if (now - self._last_firms_poll) >= FIRMS_POLL_INTERVAL_SECONDS:
                self._last_firms_poll = now
                self.refresh_firms()

            if (now - self._last_firms_delivery) >= FIRMS_DELIVERY_INTERVAL_SECONDS:
                self._last_firms_delivery = now
                self.deliver_firms()

            if (now - self._last_short_term_run) >= SHORT_TERM_INTERVAL_SECONDS:
                self._last_short_term_run = now
                self.check_short_term_alerts()

            self.refresh_brandsen_weather_if_needed()

            # Cleanup diario: elimina series temporales > 7 días
            if (now - self._last_cleanup_run) >= 86400:
                self._last_cleanup_run = now
                try:
                    traffic_stats.cleanup_old_data(days=7)
                    logger.info("Cleanup diario de traffic_stats completado.")
                except Exception as exc:
                    logger.warning("Error en cleanup diario: %s", exc)
                try:
                    cleanup = self.firms_service.prune_old_data()
                    logger.info("Cleanup diario FIRMS completado: %s", cleanup)
                except Exception as exc:
                    logger.warning("Error en cleanup diario FIRMS: %s", exc)

        self.interface.on_tick = tick

    def load_menu_modules(self):
        """
        Dynamically load all menu modules from the 'modules' folder.
        """
        menu_modules = {}
        current_dir = os.path.dirname(os.path.abspath(__file__))
        modules_folder = os.path.join(current_dir, "modules")

        print(f"Looking for modules in: {modules_folder}")

        if not os.path.exists(modules_folder):
            print("Modules folder does not exist!")
            return menu_modules

        for item in os.listdir(modules_folder):
            item_path = os.path.join(modules_folder, item)
            if os.path.isfile(item_path) and item.endswith(".py") and not item.startswith("__"):
                module_name = item[:-3]
                try:
                    module = importlib.import_module(f"modules.{module_name}")
                    if hasattr(module, "menu_name") and hasattr(module, "process_command"):
                        menu_name = module.menu_name.strip()
                        if menu_name:
                            print(f"Loaded module: {menu_name}")
                            menu_modules[menu_name] = module
                        else:
                            print(f"Skipping {module_name}: Empty menu_name")
                    else:
                        print(f"Skipping {module_name}: Missing required attributes")
                except Exception as e:
                    print(f"Error loading module '{module_name}': {e}")
            elif os.path.isdir(item_path):
                submenu = {}
                for sub_file in os.listdir(item_path):
                    if sub_file.endswith(".py") and not sub_file.startswith("__"):
                        sub_module_name = sub_file[:-3]
                        try:
                            sub_module = importlib.import_module(f"modules.{item}.{sub_module_name}")
                            if hasattr(sub_module, "menu_name") and hasattr(sub_module, "process_command"):
                                menu_name = sub_module.menu_name.strip()
                                if menu_name:
                                    print(f"Loaded submodule: {menu_name} under menu '{item}'")
                                    submenu[menu_name] = sub_module
                                else:
                                    print(f"Skipping {sub_module_name}: Empty menu_name")
                            else:
                                print(f"Skipping {sub_module_name}: Missing required attributes")
                        except Exception as e:
                            print(f"Error loading submodule '{sub_module_name}': {e}")
                if submenu:
                    menu_modules[item] = {"submodules": submenu}

        print(f"Loaded modules: {list(menu_modules.keys())}")
        return menu_modules

    def handle_message(self, user_id, message):
        short_name = None
        long_name = None
        try:
            serial_iface = getattr(self.interface, "interface", None)
            nodes = getattr(serial_iface, "nodes", {}) if serial_iface else {}
            info = nodes.get(user_id, {}) if isinstance(nodes, dict) else {}
            user = info.get("user", {}) if isinstance(info.get("user"), dict) else {}
            short_name = user.get("shortName") or info.get("shortName")
            long_name = user.get("longName") or info.get("longName")
        except Exception:
            pass

        bbs_users.record_visit(
            node_id=user_id,
            short_name=short_name,
            long_name=long_name,
            last_message=(message[:120] if isinstance(message, str) else str(message)),
        )

        msg = message.strip()

        if msg.strip() == SMN_SECRET_COMMAND:
            self.broadcast_smn_alerts()
            return "SMN broadcast manual enviado."

        smn_response = self.handle_smn_command(user_id, msg)
        if smn_response:
            return smn_response

        if msg.lower().startswith("tell "):
            parts = msg.split(" ", 2)
            if len(parts) < 3:
                return "Uso: tell !nodeId mensaje"
            to_id = store_forward.normalize_node_id(parts[1])
            body = parts[2].strip()
            mid = store_forward.enqueue(user_id, to_id, body)
            return f"BBS> mensaje encolado #{mid} para {to_id}"

        now = time.time()
        user_state = self.users.get(user_id)

        if user_state is not None:
            started = user_state.get("session_started_at", now)

            if (now - started) >= SESSION_TTL_SECONDS:
                logger.info("Session expired for user %s", user_id)
                self.users.pop(user_id, None)
                return "BBS> Sesión expirada (2h). Nueva sesión iniciada.\n\n" + self.start_session(user_id)

            user_state["last_activity_at"] = now

        if user_id not in self.users:
            response = self.start_session(user_id)
        else:
            response = self.process_command(user_id, message)
        return response

    def start_session(self, user_id):
        now = time.time()
        self.users[user_id] = {
            "menu": ["main"],
            "session_started_at": now,
            "last_activity_at": now,
        }

        self.refresh_brandsen_weather_if_needed()

        weather_line = self.format_brandsen_weather_line()
        phrase = self.get_random_welcome_phrase()

        parts = []
        if weather_line:
            parts.append(weather_line)
        parts.append(phrase)
        parts.append("")
        parts.append(self.display_menu(user_id))

        return "\n".join(parts)

    def process_command(self, user_id, command):
        """
        Process commands based on the user's current menu.
        """
        if command.strip().lower() in ["logout", "reset", "exit"]:
            self.users.pop(user_id, None)
            return "BBS> Sesión cerrada.\n\n" + self.start_session(user_id)

        current_menu = self.users[user_id]["menu"][-1]

        if "module_control" in self.users[user_id]:
            module = self.users[user_id]["module_control"]
            if command.strip().lower() == "cd ..":
                del self.users[user_id]["module_control"]
                if len(self.users[user_id]["menu"]) > 1:
                    self.users[user_id]["menu"].pop()
                return self.display_menu(user_id)
            result = module.process_command(user_id, command, self)
            if result == "__back__":
                del self.users[user_id]["module_control"]
                if len(self.users[user_id]["menu"]) > 1:
                    self.users[user_id]["menu"].pop()
                return self.display_menu(user_id)
            return result

        if command.strip().lower() == "top":
            self.users[user_id]["menu"] = ["main"]
            return self.display_menu(user_id)
        if command.strip().lower() == "cd ..":
            if len(self.users[user_id]["menu"]) > 1:
                self.users[user_id]["menu"].pop()
                return self.display_menu(user_id)
            return "You are already at the main menu."

        if current_menu == "main":
            return self.handle_main_menu(user_id, command)
        if current_menu in self.menu_modules:
            menu_data = self.menu_modules[current_menu]
            if isinstance(menu_data, dict) and "submodules" in menu_data:
                return self.handle_submenu(user_id, command, menu_data["submodules"])
            if hasattr(menu_data, "process_command"):
                self.users[user_id]["module_control"] = menu_data
                return menu_data.display_menu() if hasattr(menu_data, "display_menu") else "Entering module..."
            return "Invalid command."
        return "Invalid command."

    def handle_main_menu(self, user_id, command):
        """
        Handle user input in the main menu.
        """
        try:
            command_index = int(command) - 1
            menu_names = list(self.menu_modules.keys())

            if not (0 <= command_index < len(menu_names)):
                return "Invalid option."

            selected_menu = menu_names[command_index]
            menu_entry = self.menu_modules.get(selected_menu)

            self.users[user_id]["menu"].append(selected_menu)

            if isinstance(menu_entry, dict) and "submodules" in menu_entry:
                return self.display_submenu(selected_menu)

            if hasattr(menu_entry, "process_command"):
                self.users[user_id]["module_control"] = menu_entry

            if hasattr(menu_entry, "display_menu"):
                return menu_entry.display_menu()

            if "module_control" in self.users[user_id]:
                return "Entering module..."

            return "Invalid option."

        except ValueError:
            return "Invalid input. Please enter a number."
        except Exception as e:
            logger.exception("handle_main_menu error: %s", e)
            return "Error processing option."

    def handle_submenu(self, user_id, command, submodules):
        """
        Handle user input in a submenu.
        """
        try:
            command_index = int(command) - 1
            submenu_names = list(submodules.keys())
            if command_index == len(submenu_names):
                if len(self.users[user_id]["menu"]) > 1:
                    self.users[user_id]["menu"].pop()
                return self.display_menu(user_id)
            if 0 <= command_index < len(submenu_names):
                selected_submodule = submodules[submenu_names[command_index]]
                self.users[user_id]["module_control"] = selected_submodule
                return selected_submodule.display_menu() if hasattr(selected_submodule, "display_menu") else "No menu available."
            return "Invalid option."
        except ValueError:
            return "Invalid input. Please enter a number."

    # Iconos para menús y submódulos
    MENU_ICONS = {
        "Estadisticas": "📊",
        "Mensajes":     "💬",
        "Nodos":        "📡",
        "Juegos":       "🎮",
        "Mail":         "📬",
    }
    SUBMODULE_ICONS = {
        "Estado de Red":    "🔍",
        "Trafico":          "📈",
        "Canales":          "📻",
        "Usuarios BBS":     "👥",
        "Board":            "📝",
        "Address List":     "📋",
        "Tic Tac Toe":      "⭕",
        "Escape Room":      "🚪",
        "Hot Cold":         "🌡",
        "Zork":             "🗺",
    }

    def display_menu(self, user_id):
        current_menu = self.users[user_id]["menu"][-1]

        if current_menu == "main":
            lines = [
                "=[ SOMesh BBS ]=",
                "-- SO Bonaerense -",
            ]
            for index, name in enumerate(self.menu_modules.keys(), start=1):
                icon = self.MENU_ICONS.get(name, "▸")
                lines.append(f"[{index}] {icon} {name}")
            lines.append("> opcion:")
            return "\n".join(lines)

        if current_menu in self.menu_modules and "submodules" in self.menu_modules[current_menu]:
            return self.display_submenu(current_menu)

        return "Invalid menu."

    def display_submenu(self, menu_name):
        submodules = self.menu_modules[menu_name]["submodules"]
        icon = self.MENU_ICONS.get(menu_name, "▸")
        lines = [
            f"=[ {icon} {menu_name.upper()} ]=",
        ]
        for index, sub_name in enumerate(submodules.keys(), start=1):
            sub_icon = self.SUBMODULE_ICONS.get(sub_name, "▸")
            lines.append(f"[{index}] {sub_icon} {sub_name}")
        volver_num = len(submodules) + 1
        lines.append(f"[{volver_num}] ↩ Volver")
        lines.append("> opcion:")
        return "\n".join(lines)

    def notify_new_node(self, node_id: str, short_name: str, long_name: str,
                        hops=None, snr=None,
                        heard_by=None, heard_by_short=None, heard_by_long=None,
                        traceroute_path=None):
        traffic_stats.record_node_event(
            node_id=node_id,
            short_name=short_name,
            long_name=long_name,
            event_type="new_node",
            hops=hops,
            snr=snr,
            heard_by=heard_by,
            heard_by_short=heard_by_short,
            heard_by_long=heard_by_long,
        )
        lines = ["🆕 Nodo nuevo"]
        if long_name:
            lines.append(f"Largo: {long_name}")
        if short_name:
            lines.append(f"Corto: {short_name}")
        lines.append(f"ID: {node_id}")
        if hops is not None:
            lines.append(f"Hops: {hops}")
        if traceroute_path and len(traceroute_path) > 1:
            lines.append(f"Ruta: {' → '.join(traceroute_path)}")
        elif heard_by:
            heard_name = heard_by_long or heard_by_short
            heard_label = f"{heard_by} ({heard_name})" if heard_name else heard_by
            lines.append(f"Escuchado por: {heard_label}")
        msg = "\n".join(lines)
        for target in NEW_NODE_NOTIFY_NODES:
            try:
                self.interface.send_message(target, msg)
                logger.info("notify_new_node: DM enviado a %s sobre %s", target, node_id)
            except Exception as exc:
                logger.warning("notify_new_node: error enviando a %s: %s", target, exc)
        try:
            self.interface.send_channel_message(msg, channel_index=0)
            logger.info("notify_new_node: broadcast canal 0 sobre %s", node_id)
        except Exception as exc:
            logger.warning("notify_new_node: error broadcast canal 0: %s", exc)

    def deliver_pending(self):
        serial_iface = self.interface.interface
        if not serial_iface:
            return

        try:
            nodes = getattr(serial_iface, "nodes", {}) or {}
            now = int(time.time())

            online = []
            for node_id, info in nodes.items():
                last = info.get("lastHeard") or info.get("lastHeardTimestamp") or info.get("last_heard")
                if isinstance(last, (int, float)):
                    age = now - int(last)
                    if age <= store_forward.ONLINE_WINDOW_SECONDS:
                        user = info.get("user", {}) if isinstance(info.get("user"), dict) else {}
                        short = user.get("shortName") or info.get("shortName") or user.get("short_name") or info.get("short_name")
                        longn = user.get("longName") or info.get("longName") or user.get("long_name") or info.get("long_name")
                        online.append((node_id, age, short, longn))

            logger.info(
                "SF tick: known_nodes=%d online_nodes=%d window=%ds",
                len(nodes),
                len(online),
                store_forward.ONLINE_WINDOW_SECONDS,
            )

            for node_id, age, short, longn in sorted(online, key=lambda x: x[1]):
                label = short or longn or ""
                if label:
                    logger.info("SF online: %s (%s) lastHeard=%ds ago", node_id, label, age)
                else:
                    logger.info("SF online: %s lastHeard=%ds ago", node_id, age)

        except Exception as e:
            logger.error("SF tick: failed to list online nodes: %s", e)

        for msg_id, from_id, to_id, body, _attempts in store_forward.pending(limit=50):
            to_id = store_forward.normalize_node_id(to_id)

            if not store_forward.is_online(serial_iface, to_id):
                continue

            try:
                payload = f"MAIL de {from_id}: {body}"
                self.interface.send_message(to_id, payload)

                store_forward.inc_attempts(msg_id)
                store_forward.mark_delivered(msg_id)

                self.interface.send_message(from_id, f"BBS> mensaje #{msg_id} enviado a {to_id}")

            except Exception:
                store_forward.inc_attempts(msg_id)
                continue

    def get_random_welcome_phrase(self) -> str:
        _now = datetime.now()
        if (_now.year, _now.month) == FIXED_WELCOME_PHRASE_PERIOD:
            return FIXED_WELCOME_PHRASE
        return random.choice(BBS_WELCOME_PHRASES)

    def _extract_environment_metrics(self, info: dict):
        """
        Extrae temperatura, humedad y presión del node info.
        Soporta diferentes formatos del lib Meshtastic.
        """
        if not isinstance(info, dict):
            return None

        candidates = []

        if isinstance(info.get("environmentMetrics"), dict):
            candidates.append(info["environmentMetrics"])

        telem = info.get("telemetry")
        if isinstance(telem, dict):
            if isinstance(telem.get("environmentMetrics"), dict):
                candidates.append(telem["environmentMetrics"])
            if isinstance(telem.get("environment_metrics"), dict):
                candidates.append(telem["environment_metrics"])

        if isinstance(info.get("environment_metrics"), dict):
            candidates.append(info["environment_metrics"])

        for em in candidates:
            temp = em.get("temperature") or em.get("temp") or em.get("temperature_c")
            hum = em.get("relativeHumidity") or em.get("relative_humidity") or em.get("humidity") or em.get("hum")
            pres = em.get("barometricPressure") or em.get("barometric_pressure") or em.get("pressure") or em.get("pres")

            def to_float(v):
                try:
                    return float(v)
                except Exception:
                    return None

            temp = to_float(temp)
            hum = to_float(hum)
            pres = to_float(pres)

            if temp is None and hum is None and pres is None:
                continue

            return {"temp_c": temp, "hum_pct": hum, "pres_hpa": pres}

        return None

    def refresh_brandsen_weather_if_needed(self):
        """
        Actualiza el cache de clima del nodo BRANDSEN (!33695e54).
        """
        now = time.time()

        if self._brandsen_weather is not None:
            updated_at = self._brandsen_weather.get("updated_at", 0)
            if (now - updated_at) < WEATHER_REFRESH_SECONDS:
                return

        self._last_brandsen_weather_refresh = now

        serial_iface = getattr(self.interface, "interface", None)
        nodes = getattr(serial_iface, "nodes", {}) if serial_iface else {}

        if not isinstance(nodes, dict) or not nodes:
            return

        info = nodes.get(BRANDSEN_NODE_ID)
        if not isinstance(info, dict):
            return

        em = self._extract_environment_metrics(info)
        if not em:
            return

        self._brandsen_weather = {
            **em,
            "updated_at": now,
            "source": BRANDSEN_NODE_ID,
        }
        traffic_stats.save_brandsen_weather(self._brandsen_weather)
        logger.info("BRANDSEN weather updated: %s", self._brandsen_weather)

    def update_brandsen_weather_from_telemetry(self, env: dict):
        """
        env: dict con keys posibles: temperature, relativeHumidity, barometricPressure
        """
        now = time.time()

        def to_float(v):
            try:
                return float(v)
            except Exception:
                return None

        temp = to_float(env.get("temperature") or env.get("temp"))
        hum = to_float(env.get("relativeHumidity") or env.get("relative_humidity") or env.get("humidity"))
        pres = to_float(env.get("barometricPressure") or env.get("barometric_pressure") or env.get("pressure"))

        if temp is None and hum is None and pres is None:
            return

        self._brandsen_weather = {
            "temp_c": temp,
            "hum_pct": hum,
            "pres_hpa": pres,
            "updated_at": now,
            "source": BRANDSEN_NODE_ID,
        }
        self._last_brandsen_weather_refresh = now
        traffic_stats.save_brandsen_weather(self._brandsen_weather)
        logger.info("BRANDSEN weather updated from TELEMETRY_APP: %s", self._brandsen_weather)

    def format_brandsen_weather_line(self):
        w = self._brandsen_weather
        if not w:
            return None

        def fmt(v, unit, decimals=1):
            if v is None:
                return "--"
            return f"{v:.{decimals}f}{unit}"

        temp = fmt(w.get("temp_c"), "°C")
        hum = fmt(w.get("hum_pct"), "%")
        pres = fmt(w.get("pres_hpa"), "hPa")

        return f"BRANDSEN> {temp} | {hum} | {pres}"


    def refresh_firms(self):
        """Poll NASA FIRMS and persist new detections/events/outbox actions."""
        if not self.firms_service.enabled:
            logger.debug("FIRMS disabled: FIRMS_MAP_KEY is not configured")
            return
        try:
            result = self.firms_service.poll()
            logger.info("FIRMS poll complete: %s", result)
        except Exception as exc:
            logger.exception("Error polling FIRMS: %s", exc)

    def deliver_firms(self):
        """Retry pending FIRMS text/waypoint actions independently from NASA polling."""
        if not self.firms_service.enabled:
            return
        try:
            sent = firms_notifier.process_pending_actions(self.interface, self.firms_service)
            if sent:
                logger.info("FIRMS outbox actions sent: %s", sent)
        except Exception as exc:
            logger.exception("Error delivering FIRMS outbox: %s", exc)

    def deliver_weather_alerts(self):
        try:
            sent = weather_alert_notifier.process_all_subscribed_nodes(self.interface)
            if sent > 0:
                logger.info("Weather alerts sent in this pass: %s", sent)
        except Exception as exc:
            logger.exception("Error delivering weather alerts: %s", exc)

    def refresh_weather_alerts(self):
        """
        Ejecuta el scraper de alertas meteorológicas para descargar y actualizar
        la base de datos con alertas vigentes del SMN.
        """
        try:
            logger.info("Running weather alerts scraper...")
            weather_alert_scraper.run()
            logger.info("Weather alerts scraper completed successfully")
        except Exception as exc:
            logger.exception("Error running weather alerts scraper: %s", exc)

    def handle_smn_command(self, sender: str, text: str):
        parts = text.strip().upper().split()

        if len(parts) == 2 and parts[0] == "SMN":
            if parts[1] == "SUB":
                weather_alert_service.subscribe_node(sender)
                return "Suscripcion a alertas SMN activada."

            if parts[1] == "UNSUB":
                weather_alert_service.unsubscribe_node(sender)
                return "Suscripcion a alertas SMN desactivada."

            if parts[1] == "STATUS":
                subscribed = weather_alert_service.is_node_subscribed(sender)
                if subscribed:
                    return "SMN STATUS: suscripto."
                return "SMN STATUS: no suscripto."

        return None

    def run(self):
        """
        Start the interface and BBS system.
        """
        print("BBS System running...")
        self.interface.run()

    def severity_to_color(self, severity: str) -> str:
        mapping = {
            "Moderate": "🟡",
            "Severe":   "🟠",
            "Extreme":  "🔴",
        }
        return mapping.get((severity or "").strip(), "⚪")

    def alert_icon(self, title: str) -> str:
        t = (title or "").lower()
        if "torment" in t:
            return "⛈"
        if "viento" in t:
            return "💨"
        if "lluv" in t:
            return "🌧"
        if "nieve" in t:
            return "🌨"
        if "niebla" in t:
            return "🌫"
        return "⚠"

    def day_abbr_es(self, dt: datetime) -> str:
        days = {
            0: "lunes",
            1: "martes",
            2: "miércoles",
            3: "jueves",
            4: "viernes",
            5: "sábado",
            6: "domingo",
        }
        return days[dt.weekday()]

    def moment_of_day_es(self, dt: datetime) -> str:
        h = dt.hour
        if 0 <= h < 6:
            return "madrugada"
        if 6 <= h < 12:
            return "mañana"
        if 12 <= h < 18:
            return "tarde"
        return "noche"

    def format_alert_window_es(self, onset_str: str, expires_str: str) -> str:
        try:
            onset_dt = datetime.fromisoformat(onset_str)
            expires_dt = datetime.fromisoformat(expires_str)
        except Exception:
            return "vigente"

        day1 = self.day_abbr_es(onset_dt)
        day2 = self.day_abbr_es(expires_dt)
        part1 = self.moment_of_day_es(onset_dt)
        part2 = self.moment_of_day_es(expires_dt)

        if day1 == day2 and part1 == part2:
            return f"{day1} {part1}"
        if day1 == day2:
            return f"{day1} {part1}/{part2}"
        return f"{day1} {part1} → {day2} {part2}"

    def _merge_partido_alerts(self, alerts: list) -> list:
        """Elimina duplicados exactos y fusiona ventanas contiguas (gap ≤ 3h) del mismo título+severidad."""
        from datetime import timedelta

        def parse_dt(s):
            try:
                return datetime.fromisoformat(s) if s else None
            except Exception:
                return None

        GAP_TOLERANCE = timedelta(hours=3)
        SEV_ORDER = {"Extreme": 0, "Severe": 1, "Moderate": 2, "": 3}

        groups = {}
        for alert in alerts:
            key = (
                (alert.get("title") or "").strip().upper(),
                (alert.get("severity") or "").strip(),
            )
            groups.setdefault(key, []).append(alert)

        merged = []
        for group in groups.values():
            # Paso 1: eliminar duplicados exactos por (onset, expires)
            seen_windows = set()
            deduped = []
            for a in group:
                w = (a.get("onset") or "", a.get("expires") or "")
                if w not in seen_windows:
                    seen_windows.add(w)
                    deduped.append(a)

            # Paso 2: ordenar por onset como string (ISO ordena bien lexicográficamente)
            sorted_group = sorted(deduped, key=lambda a: a.get("onset") or "")

            current = dict(sorted_group[0])
            for nxt in sorted_group[1:]:
                current_expires = parse_dt(current.get("expires"))
                nxt_onset       = parse_dt(nxt.get("onset"))
                nxt_expires     = parse_dt(nxt.get("expires"))
                if current_expires and nxt_onset and (nxt_onset - current_expires) <= GAP_TOLERANCE:
                    if nxt_expires and nxt_expires > current_expires:
                        current["expires"] = nxt["expires"]
                else:
                    merged.append(current)
                    current = dict(nxt)
            merged.append(current)

        merged.sort(key=lambda a: (SEV_ORDER.get(a.get("severity") or "", 3), a.get("onset") or ""))
        return merged

    def build_smn_messages_by_partido(self, hour: int = None) -> list:
        if hour is None:
            hour = datetime.now().hour

        alerts = weather_alert_service.get_sudoeste_ba_alerts()

        by_partido: dict = {}
        for alert in alerts:
            for partido in (alert.get("affected_partidos") or []):
                by_partido.setdefault(partido, []).append(alert)

        all_partidos = list(weather_alert_service.TARGET_PARTIDOS.values())
        rest = sorted(p for p in all_partidos if p not in PARTIDO_ORDER)
        ordered = PARTIDO_ORDER + rest

        messages = []
        header_added = False

        for partido in ordered:
            partido_alerts = self._merge_partido_alerts(by_partido.get(partido, []))

            if not partido_alerts:
                continue

            lines = []

            if not header_added:
                lines += [
                    "⚡🚨 ALERTAS SMN 🚨⚡",
                    f"── Reporte {hour}hs · 72hs ──",
                    "",
                ]
                header_added = True

            lines.append(f"📍 {partido.upper()}")

            for alert in partido_alerts:
                icon = self.alert_icon(alert["title"])
                sev = self.severity_to_color(alert["severity"])
                when_txt = self.format_alert_window_es(alert["onset"], alert["expires"])
                lines += [
                    "· · · · · · · · · ·",
                    f"{sev}{icon} {alert['title'].upper()}",
                    f"🕐 {when_txt}",
                ]

            messages.append("\n".join(lines))

        return messages

    def _send_smn_messages(self, messages: list):
        for idx, msg in enumerate(messages):
            for ch in (SMN_ALERTS_CHANNEL_INDEX, SMN_ALERTS_CHANNEL_INDEX_2):
                self.interface.send_channel_message(msg, channel_index=ch, chunk_delay=5)
            if idx < len(messages) - 1:
                time.sleep(SMN_BETWEEN_PARTIDOS_DELAY)

    def broadcast_smn_daily_report(self, hour: int, previous_content=None):
        """Publica el resumen diario de alertas SMN al canal.

        Envía si hay alertas vigentes y, o bien es el envío inicial de la franja
        (previous_content is None), o bien el contenido cambió respecto de
        previous_content (re-publicación por alerta nueva/actualizada).

        Devuelve (se_publicó: bool, contenido_actual: str).
        """
        try:
            messages = self.build_smn_messages_by_partido(hour=hour)
            if not messages:
                logger.info("SMN resumen diario: sin alertas para reportar (hora=%s).", hour)
                return False, ""

            content = "\n---\n".join(messages)
            if previous_content is not None and content == previous_content:
                return False, content

            self._send_smn_messages(messages)
            self._last_smn_broadcast_content = content
            logger.info(
                "SMN resumen diario %s (hora=%s, partidos=%d).",
                "re-publicado: alertas cambiaron" if previous_content is not None else "publicado",
                hour, len(messages),
            )
            return True, content

        except Exception as exc:
            logger.exception("Error en broadcast_smn_daily_report: %s", exc)
            return False, ""

    def broadcast_smn_alerts(self):
        try:
            messages = self.build_smn_messages_by_partido()
            if not messages:
                logger.info("No hay alertas para el sudoeste bonaerense; no se publica nada.")
                return

            self._send_smn_messages(messages)
            self._last_smn_broadcast_content = "\n---\n".join(messages)
            logger.info("SMN manual broadcast enviado (partidos=%d, channelIndex=%s)",
                        len(messages), SMN_ALERTS_CHANNEL_INDEX)

        except Exception as exc:
            logger.exception("Error broadcasting SMN alerts: %s", exc)

    def send_smnalert_invite(self):
        """Invitación al canal SMNAlert: 3 mensajes separados (texto, nombre, clave)
        al canal 0 para que se puedan copiar/pegar fácil."""
        try:
            total = len(SMNALERT_INVITE_MESSAGES)
            for idx, msg in enumerate(SMNALERT_INVITE_MESSAGES, start=1):
                self.interface.send_channel_message(msg, channel_index=0)
                logger.info("Invitación SMNAlert %d/%d enviada al canal 0", idx, total)
                if idx < total:
                    time.sleep(5)
        except Exception:
            logger.exception("Error enviando invitación SMNAlert")

    def handle_channel_message(self, sender: str, text: str, channel_idx: int):
        """Alguien escribió en un canal (no DM). Por ahora solo reacciona en
        SMNAlert: responde amistosamente que el canal está activo, hasta 2
        veces por día por persona."""
        if channel_idx != SMN_ALERTS_CHANNEL_INDEX:
            return None

        today = datetime.now().strftime("%Y-%m-%d")
        last_date, count = self._smnalert_channel_replies.get(sender, (None, 0))
        count = count + 1 if last_date == today else 1
        self._smnalert_channel_replies[sender] = (today, count)

        if count > len(SMNALERT_CHANNEL_REPLY_MESSAGES):
            return None
        return SMNALERT_CHANNEL_REPLY_MESSAGES[count - 1]

    def build_short_term_alert_messages(self, alert: dict) -> list:
        """Devuelve una lista de mensajes, uno por partido afectado (header solo en el primero)."""
        try:
            dt = datetime.fromisoformat(alert["dc_date"])
            when = dt.strftime("%d/%m %H:%M")
        except Exception:
            when = alert.get("dc_date", "")

        description = alert.get("description_clean", "").strip()
        phenomenon  = alert.get("phenomenon", "ALERTA METEOROLOGICA").upper()
        affected    = alert.get("affected_partidos", [])

        priority_affected = [p for p in PARTIDO_ORDER if p in affected]
        rest_affected     = sorted(p for p in affected if p not in PARTIDO_ORDER)
        ordered_affected  = priority_affected + rest_affected

        messages = []
        for i, partido in enumerate(ordered_affected):
            lines = []
            if i == 0:
                lines += [
                    "🚨🔴🚨🔴🚨🔴🚨🔴🚨",
                    "⚠️ AVISO METEOROLÓGICO ⚠️",
                    "⚠️  MUY CORTO PLAZO  ⚠️",
                    "🚨🔴🚨🔴🚨🔴🚨🔴🚨",
                ]
            lines += [
                f"📍 {partido.upper()}",
                f"⛈ {phenomenon}",
                f"🕐 Emitido: {when}",
            ]
            if description:
                lines.append(f"▶ {description}")
            lines += [
                "─────────────────────",
                "⛔ NO ES UN REPORTE DIARIO.",
                "Tomá precauciones AHORA.",
                "Situación de riesgo inminente.",
                "─────────────────────",
                "Fuente: SMN Argentina",
            ]
            messages.append("\n".join(lines))

        return messages

    def check_short_term_alerts(self):
        """Consulta el feed de avisos a muy corto plazo y emite al canal si hay match con el SW."""
        import sqlite3

        try:
            items = short_term_alert_scraper.fetch_new_items()
        except Exception as exc:
            logger.warning("Error fetching short-term alerts feed: %s", exc)
            return

        partidos = weather_alert_service.load_target_partidos()

        with sqlite3.connect(short_term_alert_scraper.DB_PATH) as conn:
            short_term_alert_scraper.init_db(conn)

            for item in items:
                if short_term_alert_scraper.is_dispatched(conn, item["dc_date"]):
                    continue

                alert_polygon = item.get("polygon", [])
                if not alert_polygon:
                    continue

                matched_partidos = []
                for partido_name, partido_polygons in partidos.items():
                    for partido_poly in partido_polygons:
                        if weather_alert_service.polygons_intersect(alert_polygon, partido_poly):
                            matched_partidos.append(partido_name)
                            break

                if not matched_partidos:
                    continue

                item["affected_partidos"] = sorted(matched_partidos)
                messages = self.build_short_term_alert_messages(item)

                try:
                    for idx, msg in enumerate(messages):
                        for ch in (SMN_ALERTS_CHANNEL_INDEX, SMN_ALERTS_CHANNEL_INDEX_2):
                            self.interface.send_channel_message(msg, channel_index=ch, chunk_delay=5)
                        if idx < len(messages) - 1:
                            time.sleep(SMN_BETWEEN_PARTIDOS_DELAY)
                    short_term_alert_scraper.mark_dispatched(conn, item["dc_date"], item["phenomenon"])
                    logger.info("Short-term alert dispatched: %s → %s", item["dc_date"], matched_partidos)
                except Exception as exc:
                    logger.exception("Error sending short-term alert: %s", exc)


if __name__ == "__main__":
    bbs = BBSSystem()
    bbs.run()
