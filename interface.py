import json
import os
import logging
from collections import deque
from meshtastic.serial_interface import SerialInterface
from pubsub import pub
import time
import requests
import traffic_stats


CONFIG_FILE = "meshtastic_config.json"
LOG_FILE = "listener.log"
MAX_TEXT_LEN = 180
MAX_BYTES = 220  # límite real de Meshtastic es 233 bytes, margen de seguridad
BRANDSEN_NODE_ID = "!33695e54"


# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

class Interface:
    def __init__(self):
        self.interface = None
        self.handle_message = None  # Callback for message handling
        self.on_tick = None  # callback opcional para tareas periódicas
        self.bbs = None
        self._own_node_id = None
        self._traceroute_queue: deque = deque()
        self._traceroute_last_refill = 0
        self._traceroute_last_send = 0
        self._channel_hash_to_name: dict = {}  # hash → nombre del canal
        self._track_traceroute_last: dict = {}  # node_id → último traceroute por movimiento

    def load_device_path(self):
        """Load the device path from the configuration file."""
        if not os.path.exists(CONFIG_FILE):
            logger.error(f"Configuration file '{CONFIG_FILE}' not found. Please run setup.py to create it.")
            return None

        try:
            with open(CONFIG_FILE, "r") as config_file:
                config = json.load(config_file)
                device_path = config.get("device_path")
                if not device_path:
                    logger.error(f"'device_path' not found in '{CONFIG_FILE}'.")
                    return None
                logger.info(f"Loaded device path from config: {device_path}")
                return device_path
        except Exception as e:
            logger.error(f"Error reading configuration file '{CONFIG_FILE}': {e}")
            return None

    def connect(self):
        """Attempt to connect to the Meshtastic device."""
        device_path = self.load_device_path()
        if not device_path:
            logger.error("Device path could not be loaded. Exiting...")
            return

        logger.info(f"Attempting to connect to the Meshtastic device at {device_path}...")
        try:
            # Initialize the SerialInterface object with the specified device path
            self.interface = SerialInterface(devPath=device_path)
            logger.info(f"Successfully connected to Meshtastic device on {device_path}")
            pub.subscribe(self.on_receive, "meshtastic.receive.text")
            pub.subscribe(self.on_receive, "meshtastic.receive.telemetry")
            pub.subscribe(self.on_receive, "meshtastic.receive.neighborinfo")
            pub.subscribe(self.on_receive, "meshtastic.receive.position")
            pub.subscribe(self.on_receive, "meshtastic.receive.traceroute")
            try:
                self._own_node_id = f"!{self.interface.myInfo.my_node_num:08x}"
                logger.info("Own node ID: %s", self._own_node_id)
            except Exception:
                logger.exception("Could not determine own node ID")
            self._sync_node_positions()
            self._sync_channel_names()
            self._hook_raw_packets()
        except Exception as e:
            logger.error(f"Failed to connect to Meshtastic device: {e}")
            self.interface = None

    def _sync_node_positions(self):
        """Lee posiciones cacheadas del SDK al arrancar, sin esperar POSITION_APP."""
        try:
            nodes = getattr(self.interface, "nodes", {}) or {}
            count = 0
            for node_id, info in nodes.items():
                if not isinstance(info, dict):
                    continue
                pos = info.get("position") or {}
                if not isinstance(pos, dict):
                    continue
                lat_i = pos.get("latitudeI") or pos.get("latitude_i")
                lon_i = pos.get("longitudeI") or pos.get("longitude_i")
                if not lat_i or not lon_i:
                    continue
                nid = node_id.lower() if isinstance(node_id, str) else f"!{node_id:08x}"
                traffic_stats.update_node_position(nid, lat_i / 1e7, lon_i / 1e7, pos.get("altitude"), touch_last_seen=False)
                count += 1
            logger.info("Seeded %d node positions from device node database", count)
        except Exception:
            logger.exception("Error syncing node positions from device")

    def _sync_channel_names(self):
        """Lee los canales del radio y construye hash→nombre.
        Hash = XOR de todos los bytes del PSK (algoritmo del firmware Meshtastic)."""
        try:
            local_node = getattr(self.interface, "localNode", None)
            channels = getattr(local_node, "channels", None) if local_node else None
            if channels is None:
                channels = getattr(self.interface, "channels", None)
            if not channels:
                return

            self._channel_hash_to_name.clear()
            items = channels.values() if isinstance(channels, dict) else channels
            for ch in items:
                try:
                    role    = getattr(ch, "role", None)
                    setts   = getattr(ch, "settings", None)
                    name    = (getattr(setts, "name", None) or "") if setts else ""
                    psk_raw = getattr(setts, "psk", b"") if setts else b""
                    psk     = bytes(psk_raw) if psk_raw else b""
                    if psk and name and str(role) not in ("0", "DISABLED", "ChannelRole.DISABLED"):
                        ch_hash = 0
                        for byte in psk:
                            ch_hash ^= byte
                        self._channel_hash_to_name[ch_hash] = name
                except Exception:
                    pass
            logger.info("channel names mapeados: %s", self._channel_hash_to_name)
        except Exception:
            logger.exception("Error en _sync_channel_names")

    def disconnect(self):
        """Safely disconnect the Meshtastic device."""
        if self.interface:
            try:
                logger.info("Disconnecting Meshtastic device...")
                self.interface.close()
                logger.info("Disconnected successfully.")
            except Exception as e:
                logger.error(f"Error during disconnection: {e}")
            finally:
                self.interface = None

    def on_receive(self, packet, interface):
        try:
            decoded = packet.get("decoded", {}) or {}
            portnum = decoded.get("portnum")
            text = decoded.get("text", None)

            # --- NORMALIZAR SENDER (fromId o from int) ---
            sender = packet.get("fromId")
            if not sender:
                frm = packet.get("from")
                if isinstance(frm, int):
                    sender = f"!{frm:08x}"
            if isinstance(sender, str):
                sender = sender.lower()

            to_id = packet.get("toId")
            to_num = packet.get("to")

            # Debug Brandsen (antes de filtros)
            if sender == BRANDSEN_NODE_ID:
                logger.info(
                    "BRANDSEN PACKET portnum=%s decodedKeys=%s decodedPartial=%s",
                    portnum, list(decoded.keys()), str(decoded)[:500]
                )

            # Clasificación básica (para stats)
            is_text = (portnum == "TEXT_MESSAGE_APP")
            is_broadcast = (to_id == "^all" or (isinstance(to_num, int) and to_num == 0xFFFFFFFF))
            is_dm = bool(sender and is_text and not is_broadcast)
            text_len = len(text) if isinstance(text, str) else 0

            # resolver nombres y posición del nodo desde el caché del SDK
            short_name = None
            long_name = None
            try:
                nodes = getattr(self.interface, "nodes", {}) or {}
                info = nodes.get(sender, {}) if sender else {}
                user = info.get("user", {}) if isinstance(info.get("user"), dict) else {}
                short_name = user.get("shortName") or info.get("shortName")
                long_name = user.get("longName") or info.get("longName")
                # Actualizar posición si el SDK la tiene cacheada
                if sender:
                    pos = info.get("position") or {}
                    if isinstance(pos, dict):
                        lat_i = pos.get("latitudeI") or pos.get("latitude_i")
                        lon_i = pos.get("longitudeI") or pos.get("longitude_i")
                        if lat_i and lon_i:
                            traffic_stats.update_node_position(
                                sender, lat_i / 1e7, lon_i / 1e7, pos.get("altitude")
                            )
            except Exception:
                pass

            if sender:
                if sender != self._own_node_id and traffic_stats.is_new_node(sender):
                    bbs = getattr(self, "bbs", None)
                    if bbs and hasattr(bbs, "notify_new_node"):
                        try:
                            bbs.notify_new_node(sender, short_name, long_name)
                        except Exception:
                            logger.exception("notify_new_node failed for %s", sender)

                traffic_stats.record_packet(
                    sender_id=sender,
                    short_name=short_name,
                    long_name=long_name,
                    is_text=is_text,
                    is_dm=is_dm,
                    is_broadcast=is_broadcast,
                    text_len=text_len
                )
                # Actualizar hops y SNR desde la perspectiva del BBS
                snr_val  = packet.get("rxSnr") or packet.get("snr")
                hop_start = packet.get("hopStart")
                hop_limit = packet.get("hopLimit")
                hops_val  = (hop_start - hop_limit) if (hop_start is not None and hop_limit is not None) else None
                if snr_val is not None or hops_val is not None:
                    traffic_stats.update_node_hop_info(sender, hops_val, snr_val)


            # ------------------------------------------------------------
            # ✅ CAPTURAR TELEMETRY_APP (todos los nodos)
            # ------------------------------------------------------------
            if portnum == "TELEMETRY_APP":
                telem = decoded.get("telemetry", {}) or {}
                if not isinstance(telem, dict):
                    telem = {}

                # environmentMetrics → solo BRANDSEN (para el clima del menú)
                if sender == BRANDSEN_NODE_ID:
                    env = telem.get("environmentMetrics") or telem.get("environment_metrics")
                    if isinstance(env, dict):
                        bbs = getattr(self, "bbs", None)
                        if bbs and hasattr(bbs, "update_brandsen_weather_from_telemetry"):
                            bbs.update_brandsen_weather_from_telemetry(env)
                            logger.info("Captured BRANDSEN env telemetry: %s", env)
                    else:
                        logger.info(
                            "BRANDSEN telemetry without env. telemKeys=%s telem=%s",
                            list(telem.keys()),
                            str(telem)[:800]
                        )

                # deviceMetrics → todos los nodos (channel utilization, etc.)
                if sender:
                    dm = telem.get("deviceMetrics") or telem.get("device_metrics")
                    if isinstance(dm, dict):
                        ch_util  = dm.get("channelUtilization") or dm.get("channel_utilization")
                        air_util = dm.get("airUtilTx") or dm.get("air_util_tx")
                        batt     = dm.get("batteryLevel") or dm.get("battery_level")
                        volt     = dm.get("voltage")
                        uptime   = dm.get("uptimeSeconds") or dm.get("uptime_seconds")
                        traffic_stats.record_device_metrics(
                            node_id=sender,
                            channel_util=ch_util,
                            air_util_tx=air_util,
                            battery_level=batt,
                            voltage=volt,
                            uptime_seconds=uptime,
                        )
                        logger.debug("device_metrics recorded for %s: chutil=%.1f air=%.1f",
                                     sender, ch_util or 0, air_util or 0)

                return  # no responder a telemetría

            # ------------------------------------------------------------
            # ✅ POSITION_APP — coordenadas GPS del nodo
            # ------------------------------------------------------------
            if portnum == "POSITION_APP" and sender:
                pos = decoded.get("position") or {}
                if isinstance(pos, dict):
                    lat_i = pos.get("latitudeI") or pos.get("latitude_i")
                    lon_i = pos.get("longitudeI") or pos.get("longitude_i")
                    if lat_i and lon_i:
                        lat = lat_i / 1e7
                        lon = lon_i / 1e7
                        alt = pos.get("altitude")
                        speed = pos.get("speed") or pos.get("groundSpeed")
                        heading = pos.get("groundTrack") or pos.get("heading")
                        relay_node = traffic_stats.get_node_side_relay(sender)
                        rx_snr = packet.get("rxSnr") or packet.get("snr")
                        hop_start = packet.get("hopStart")
                        hop_limit = packet.get("hopLimit")
                        hop_count = (hop_start - hop_limit) if (hop_start is not None and hop_limit is not None) else None
                        logger.info("POSITION_APP from %s | packetKeys=%s | relayNode=%s hopStart=%s hopLimit=%s",
                                    sender, list(packet.keys()), packet.get("relayNode"), packet.get("hopStart"), packet.get("hopLimit"))
                        traffic_stats.update_node_position(sender, lat, lon, alt)
                        if sender != self._own_node_id:
                            inserted = traffic_stats.record_track_point(
                                node_id=sender,
                                short_name=short_name,
                                long_name=long_name,
                                lat=lat,
                                lon=lon,
                                altitude=alt,
                                speed=float(speed) if speed is not None else None,
                                heading=int(heading) if heading is not None else None,
                                relay_node=relay_node,
                                rx_snr=float(rx_snr) if rx_snr is not None else None,
                                hop_count=hop_count,
                            )
                            if inserted:
                                self._maybe_prioritize_traceroute(sender)
                        logger.debug("position recorded for %s: %.5f, %.5f", sender, lat, lon)
                return

            # ------------------------------------------------------------
            # ✅ NEIGHBORINFO_APP — topología de la mesh
            # ------------------------------------------------------------
            if portnum == "NEIGHBORINFO_APP" and sender:
                ni = decoded.get("neighborinfo") or decoded.get("neighbor_info") or {}
                if not isinstance(ni, dict):
                    ni = {}
                neighbors = ni.get("neighbors") or []
                if neighbors:
                    traffic_stats.record_neighbors(sender, neighbors)
                    logger.debug("neighborinfo recorded for %s: %d vecinos", sender, len(neighbors))
                # Actualizar hops/snr desde perspectiva del BBS
                snr_val  = packet.get("rxSnr") or packet.get("snr")
                hops_val = packet.get("hopStart") and (packet.get("hopStart", 0) - packet.get("hopLimit", 0))
                if snr_val is not None or hops_val:
                    traffic_stats.update_node_hop_info(sender, hops_val or None, snr_val)
                return

            # ------------------------------------------------------------
            # ✅ TRACEROUTE_APP — ruta descubierta por traceroute
            # ------------------------------------------------------------
            if portnum == "TRACEROUTE_APP":
                tr = decoded.get("traceroute") or decoded.get("route_discovery") or {}
                if not isinstance(tr, dict):
                    tr = {}
                route_nums = tr.get("route") or []
                snr_list = tr.get("snrTowards") or tr.get("snr_towards") or []
                if route_nums and self._own_node_id and sender:
                    # Build full path: own_bbs → intermediates → sender
                    path = [self._own_node_id] + [f"!{n:08x}" for n in route_nums]
                    if sender not in path:
                        path.append(sender)
                    # Derive SNR per link from snrList (fixed-point ×4, sint32)
                    snr_decoded = [(s / 4.0) if s != 0 else None for s in snr_list]
                    # Record each consecutive pair as a neighbor link
                    for i in range(len(path) - 1):
                        a, b = path[i], path[i + 1]
                        snr = snr_decoded[i] if i < len(snr_decoded) else None
                        traffic_stats.record_neighbors(a, [{"node_id": b, "snr": snr}])
                    # Store full path for web display
                    traffic_stats.record_traceroute_path(sender, path)
                    # Backfill relay_node en puntos recientes sin relay (últimos 10 min)
                    if len(path) >= 3:
                        node_relay = path[-2]
                        since = int(time.time()) - 600
                        traffic_stats.backfill_relay_node(sender, node_relay, since)
                        logger.info("backfill relay_node=%s for %s since -%ds", node_relay, sender, 600)
                    logger.info("traceroute path recorded: %s (len=%d)", " → ".join(path), len(path))
                return

            # ------------------------------------------------------------
            # --- DM ONLY filter (texto) ---
            # ------------------------------------------------------------
            if portnum != "TEXT_MESSAGE_APP":
                return

            # Ignorar canal/broadcast
            if is_broadcast:
                logger.info(f"Ignoring channel message from {sender}: {text}")
                return

            # Handle standard DM text messages
            if text and sender:
                logger.info(f"Message received from {sender}: {text}")
                if self.handle_message:
                    response = self.handle_message(sender, text)
                    if response:
                        self.send_message(sender, response)

        except Exception:
            try:
                if 'sender' in locals() and sender:
                    traffic_stats.record_error(sender)
            except Exception:
                pass
            logger.exception("Error processing received packet")
            
    def _split_chunks(self, s: str, max_len: int = MAX_TEXT_LEN):
        """
        Split text into chunks that fit Meshtastic sendText() limits.
        Mide en bytes UTF-8 (no chars) para soportar emojis y Unicode.
        """
        if not s:
            return []

        def blen(t: str) -> int:
            return len(t.encode("utf-8"))

        def hard_split(ln: str) -> list:
            """Parte una línea larga respetando el límite de bytes."""
            parts = []
            while blen(ln) > MAX_BYTES:
                # Encontrar cuántos chars caben en MAX_BYTES bytes
                hi = len(ln)
                lo = 0
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    if blen(ln[:mid]) <= MAX_BYTES:
                        lo = mid
                    else:
                        hi = mid - 1
                parts.append(ln[:lo])
                ln = ln[lo:]
            parts.append(ln)
            return parts

        lines = s.splitlines(True)
        chunks = []
        cur = ""

        for ln in lines:
            if blen(cur) + blen(ln) <= MAX_BYTES:
                cur += ln
                continue

            if cur:
                chunks.append(cur.rstrip("\n"))
                cur = ""

            if blen(ln) > MAX_BYTES:
                parts = hard_split(ln.rstrip("\n"))
                chunks.extend(parts[:-1])
                cur = parts[-1]
            else:
                cur = ln

        if cur:
            chunks.append(cur.rstrip("\n"))

        return chunks

    def _hook_raw_packets(self):
        """Parchea _handleFromRadio para interceptar todos los MeshPackets
        antes de desencriptar, incluyendo canales sin clave configurada."""
        import sys

        if not callable(getattr(self.interface, "_handleFromRadio", None)):
            logger.warning("channel tracking: _handleFromRadio no encontrado")
            return

        # Busca FromRadio en los módulos ya cargados por el SDK (evita hardcodear el path)
        FromRadio = None
        for mod_name, mod in sys.modules.items():
            if "meshtastic" in mod_name and hasattr(mod, "FromRadio"):
                FromRadio = mod.FromRadio
                logger.info("channel tracking: FromRadio encontrado en %s", mod_name)
                break

        if FromRadio is None:
            logger.warning("channel tracking: FromRadio no encontrado en módulos cargados")
            return

        original = self.interface._handleFromRadio

        PORTNUM_NAMES = {
            1: "TEXT_MESSAGE_APP", 3: "POSITION_APP", 4: "NODEINFO_APP",
            67: "TELEMETRY_APP", 70: "TRACEROUTE_APP", 71: "NEIGHBORINFO_APP",
            72: "DETECTION_SENSOR_APP", 73: "PAXCOUNTER_APP",
        }

        def _patched(from_radio_bytes):
            try:
                fr = FromRadio()
                fr.ParseFromString(from_radio_bytes)
                payload_type = fr.WhichOneof("payload_variant")
                if payload_type == "packet":
                    mp          = fr.packet
                    channel_idx = int(getattr(mp, "channel", 0) or 0)
                    from_num    = int(getattr(mp, "from", 0) or 0)
                    if not from_num:
                        return original(from_radio_bytes)
                    from_id = f"!{from_num:08x}"
                    if from_id == self._own_node_id:
                        return original(from_radio_bytes)
                    pkt_type = mp.WhichOneof("payload_variant")
                    ch_name  = self._channel_hash_to_name.get(channel_idx, "")
                    rx_snr   = float(mp.rx_snr) if mp.rx_snr else None
                    if pkt_type == "encrypted":
                        portnum      = "ENCRYPTED"
                        is_encrypted = 1
                    else:
                        raw_portnum  = getattr(mp.decoded, "portnum", 0)
                        portnum      = PORTNUM_NAMES.get(int(raw_portnum), f"PORT_{raw_portnum}")
                        is_encrypted = 0
                    traffic_stats.record_channel_packet(
                        channel_idx, from_id, portnum, 0, ch_name, is_encrypted, rx_snr
                    )
            except Exception:
                logger.exception("Error in _hook_raw_packets")
            return original(from_radio_bytes)

        self.interface._handleFromRadio = _patched
        logger.info("Raw packet hook instalado en _handleFromRadio")

    # ── traceroute-based topology discovery ──────────────────────────────────

    TRACK_TRACEROUTE_INTERVAL = 600  # mínimo 10 min entre traceroutes por nodo en movimiento

    def _maybe_prioritize_traceroute(self, node_id: str):
        """Si el nodo se movió y pasaron ≥10 min desde el último traceroute, lo pone al frente de la cola."""
        now = time.time()
        if now - self._track_traceroute_last.get(node_id, 0) < self.TRACK_TRACEROUTE_INTERVAL:
            return
        if node_id in self._traceroute_queue:
            self._traceroute_queue.remove(node_id)
        self._traceroute_queue.appendleft(node_id)
        self._track_traceroute_last[node_id] = now
        logger.info("traceroute prioritized for moving node %s", node_id)

    def _refill_traceroute_queue(self):
        """Carga todos los nodos vistos en las últimas 2h (excepto el propio) en la cola."""
        import sqlite3 as _sq
        now = int(time.time())
        since = now - 7200
        try:
            con = _sq.connect(traffic_stats.DB_PATH)
            rows = con.execute(
                "SELECT node_id FROM node_stats WHERE last_seen_ts >= ?", (since,)
            ).fetchall()
            con.close()
        except Exception:
            logger.exception("traceroute: error querying node_stats")
            return
        candidates = [r[0] for r in rows if r[0] != self._own_node_id]
        self._traceroute_queue = deque(candidates)
        logger.info("traceroute: queue refilled with %d nodes", len(self._traceroute_queue))

    def _send_traceroute(self, node_id: str):
        try:
            dest = int(node_id.lstrip("!"), 16)
            self.interface.sendTraceRoute(dest, hopLimit=5)
            logger.debug("traceroute sent to %s", node_id)
        except Exception:
            logger.exception("traceroute: failed to send to %s", node_id)

    def _tick_traceroute(self, now: float):
        REFILL_INTERVAL = 1800   # refill queue every 30 min
        SEND_INTERVAL   = 15     # one traceroute every 15 s

        if not self.interface or not self._own_node_id:
            return

        if now - self._traceroute_last_refill >= REFILL_INTERVAL:
            self._refill_traceroute_queue()
            self._traceroute_last_refill = now

        if self._traceroute_queue and (now - self._traceroute_last_send >= SEND_INTERVAL):
            target = self._traceroute_queue.popleft()
            self._send_traceroute(target)
            self._traceroute_last_send = now

    def send_message(self, user_id, message):
        """Send a message back to the user (auto-split long messages)."""
        try:
            destination = int(user_id.lstrip("!"), 16)  # Remove `!` and convert to int

            # Si es muy largo, mandarlo en partes
            chunks = self._split_chunks(message, MAX_TEXT_LEN) if isinstance(message, str) else [str(message)]

            for i, chunk in enumerate(chunks, start=1):
                # opcional: prefijo para saber que vienen partes
                # if len(chunks) > 1:
                #     chunk = f"[{i}/{len(chunks)}] {chunk}"

                self.interface.sendText(chunk, destinationId=destination)
                logger.info(f"Sent chunk {i}/{len(chunks)} to {user_id}: {chunk!r}")
                time.sleep(0.2)  # mini pausa para no saturar

        except Exception as e:
            logger.error(f"Failed to send message to {user_id}: {e}")


    def log_telemetry(self, sender, latitude, longitude, altitude, timestamp):
        """Log telemetry data to a CSV file."""
        try:
            with open("telemetry_log.csv", "a") as log_file:
                log_file.write(f"{sender},{latitude},{longitude},{altitude},{timestamp}\n")
            logger.info("Telemetry data logged successfully.")
        except Exception as e:
            logger.error(f"Error logging telemetry data: {e}")

    def run(self):
        """Run the interface."""
        try:
            self.connect()
            if not self.interface:
                logger.error("Could not connect to the Meshtastic device. Exiting...")
                return

            logger.info("Listening for messages... Press Ctrl+C to exit.")
            logger.info("Listening for messages... Press Ctrl+C to exit.")
            last_tick = 0

            while self.interface:
                now = time.time()                


                if self.on_tick and (now - last_tick) >= 1.0:
                    logger.info(
                        "TICK FIRE: calling on_tick() diff=%.3f",
                        now - last_tick
                    )
                    last_tick = now
                    try:
                        self.on_tick()
                    except Exception as e:
                        logger.exception("on_tick() failed: %s", e)

                time.sleep(0.01)
        except Exception as e:
            logger.warning(f"Connection lost: {e}")
        except KeyboardInterrupt:
            logger.info("Shutting down on Ctrl+C...")
        finally:
            self.disconnect()
            logger.info("Interface stopped.")

    def send_channel_message(self, message, channel_index=0, chunk_delay=0.2):
        """
        Send a broadcast text message on a specific Meshtastic channel.
        chunk_delay: segundos entre chunks (usá valores altos para alertas ordenadas).
        """
        try:
            chunks = self._split_chunks(message, MAX_TEXT_LEN) if isinstance(message, str) else [str(message)]

            for i, chunk in enumerate(chunks, start=1):
                self.interface.sendText(
                    chunk,
                    destinationId="^all",
                    wantAck=False,
                    channelIndex=channel_index,
                )
                logger.info(
                    "Sent channel chunk %s/%s on channelIndex=%s: %r",
                    i, len(chunks), channel_index, chunk
                )
                if i < len(chunks):
                    time.sleep(chunk_delay)

        except Exception as e:
            logger.error(f"Failed to send channel message on channelIndex={channel_index}: {e}")

if __name__ == "__main__":
    interface = Interface()
    interface.run()

