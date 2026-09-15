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
        self.on_channel_message = None  # Callback opcional para mensajes de canal/broadcast
        self.on_tick = None  # callback opcional para tareas periódicas
        self.bbs = None
        self._own_node_id = None
        self._traceroute_queue: deque = deque()
        self._traceroute_last_refill = 0
        self._traceroute_last_send = 0
        self._channel_hash_to_name: dict = {}  # hash → nombre del canal
        self._track_traceroute_last: dict = {}  # node_id → último traceroute por movimiento
        self._pkt_pub_keys: dict = {}           # from_id → public_key capturada del raw packet
        self._decrypt_key_used: dict = {}       # from_id → pub_key que funcionó para descifrar
        self._pending_new_node_notify: dict = {}  # node_id → datos del aviso en espera del traceroute
        self._traceroute_tick_last = 0
        self._recent_channel_msg_ids: dict = {}   # packet_id → ts, para no reprocesar duplicados de mesh

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
            pub.subscribe(self.on_receive, "meshtastic.receive.user")  # NODEINFO_APP
            pub.subscribe(self._on_receive_encrypted, "meshtastic.receive")
            try:
                self._own_node_id = f"!{self.interface.myInfo.my_node_num:08x}"
                logger.info("Own node ID: %s", self._own_node_id)
            except Exception:
                logger.exception("Could not determine own node ID")
            try:
                self.interface.localNode.setOwner(long_name="Sentinel BBS 👉 sentinelmesh.ar")
                logger.info("Node long name updated")
            except Exception:
                logger.exception("Could not update node long name")
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

    _DUPLICATE_PACKET_TTL = 300  # 5 min

    def _is_duplicate_packet(self, packet_id) -> bool:
        """True si ya procesamos este packet_id hace poco (mismo broadcast
        escuchado varias veces por distintos relays de la mesh)."""
        if not packet_id:
            return False
        now = time.time()
        stale = [pid for pid, ts in self._recent_channel_msg_ids.items()
                 if now - ts > self._DUPLICATE_PACKET_TTL]
        for pid in stale:
            del self._recent_channel_msg_ids[pid]
        if packet_id in self._recent_channel_msg_ids:
            return True
        self._recent_channel_msg_ids[packet_id] = now
        return False

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
                snr_val   = packet.get("rxSnr") or packet.get("snr")
                hop_start = packet.get("hopStart")
                hop_limit = packet.get("hopLimit")
                hops_val  = (hop_start - hop_limit) if (hop_start is not None and hop_limit is not None) else None
                relay_num = packet.get("relayNode")
                relay_id  = f"!{relay_num:08x}" if isinstance(relay_num, int) and relay_num else None

                if sender != self._own_node_id and traffic_stats.is_new_node(sender):
                    # Resolver nombre del nodo que está escuchando directamente al nuevo
                    # (se usa como fallback si el traceroute no responde a tiempo)
                    heard_by_short, heard_by_long = None, None
                    if relay_id:
                        try:
                            nodes = getattr(self.interface, "nodes", {}) or {}
                            ri = nodes.get(relay_id, {})
                            ru = ri.get("user", {}) if isinstance(ri.get("user"), dict) else {}
                            heard_by_short = ru.get("shortName") or ri.get("shortName")
                            heard_by_long  = ru.get("longName")  or ri.get("longName")
                        except Exception:
                            pass

                    # No avisar todavía: se dispara un traceroute y se espera la
                    # respuesta (o timeout) para mandar el aviso con la ruta completa.
                    self._pending_new_node_notify[sender] = {
                        "short_name": short_name,
                        "long_name": long_name,
                        "hops": hops_val,
                        "snr": snr_val,
                        "heard_by": relay_id,
                        "heard_by_short": heard_by_short,
                        "heard_by_long": heard_by_long,
                        "queued_at": time.time(),
                    }
                    logger.info("new node %s: aviso en espera de traceroute", sender)

                    # Traceroute inmediato al nodo nuevo
                    if sender not in self._traceroute_queue:
                        self._traceroute_queue.appendleft(sender)
                        logger.info("new node %s enqueued for immediate traceroute", sender)

                traffic_stats.record_packet(
                    sender_id=sender,
                    short_name=short_name,
                    long_name=long_name,
                    is_text=is_text,
                    is_dm=is_dm,
                    is_broadcast=is_broadcast,
                    text_len=text_len
                )
                if snr_val is not None or hops_val is not None:
                    traffic_stats.update_node_hop_info(sender, hops_val, snr_val)


            # ------------------------------------------------------------
            # ✅ CAPTURAR TELEMETRY_APP (todos los nodos)
            # ------------------------------------------------------------
            if portnum == "TELEMETRY_APP":
                telem = decoded.get("telemetry", {}) or {}
                if not isinstance(telem, dict):
                    telem = {}

                # environmentMetrics → todos los nodos
                if sender:
                    env = telem.get("environmentMetrics") or telem.get("environment_metrics")
                    if isinstance(env, dict):
                        traffic_stats.record_environment_metrics(sender, env)
                        # Brandsen también actualiza el clima del menú BBS
                        if sender == BRANDSEN_NODE_ID:
                            bbs = getattr(self, "bbs", None)
                            if bbs and hasattr(bbs, "update_brandsen_weather_from_telemetry"):
                                bbs.update_brandsen_weather_from_telemetry(env)
                    elif sender == BRANDSEN_NODE_ID:
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
                        traffic_stats.update_node_partido(sender, lat, lon)
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
            # ✅ NODE_STATUS_APP — status message del nodo
            # ------------------------------------------------------------
            if portnum == "NODE_STATUS_APP" and sender:
                try:
                    from meshtastic.protobuf import mesh_pb2
                    raw_payload = decoded.get("payload") or b""
                    if isinstance(raw_payload, (bytes, bytearray)) and raw_payload:
                        sm = mesh_pb2.StatusMessage()
                        sm.ParseFromString(bytes(raw_payload))
                        status_text = sm.status.strip()
                        if status_text:
                            traffic_stats.update_node_status_message(sender, status_text)
                            logger.info("NODE_STATUS from %s: %r", sender, status_text)
                except Exception:
                    logger.exception("Error decodificando NODE_STATUS_APP de %s", sender)
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
                        # Actualizar heard_by en node_events si este nodo fue detectado como nuevo recientemente
                        adjacent = path[-2]  # nodo directamente adyacente al sender en el path
                        nb_short, nb_long = None, None
                        try:
                            iface_nodes = getattr(self.interface, "nodes", {}) or {}
                            ni = iface_nodes.get(adjacent, {})
                            nu = ni.get("user", {}) if isinstance(ni.get("user"), dict) else {}
                            nb_short = nu.get("shortName") or ni.get("shortName")
                            nb_long  = nu.get("longName")  or ni.get("longName")
                        except Exception:
                            pass
                        if traffic_stats.update_node_event_heard_by(sender, adjacent, nb_short, nb_long):
                            logger.info("traceroute: heard_by actualizado a %s para nodo nuevo %s", adjacent, sender)
                    logger.info("traceroute path recorded: %s (len=%d)", " → ".join(path), len(path))

                    # Si este nodo tenía un aviso de "nodo nuevo" pendiente, ya
                    # llegó la respuesta del traceroute: mandarlo ahora con la ruta completa.
                    pending = self._pending_new_node_notify.pop(sender, None)
                    if pending:
                        adjacent_id = path[-2] if len(path) >= 2 else pending.get("heard_by")
                        heard_short, heard_long = None, None
                        if adjacent_id and adjacent_id != self._own_node_id:
                            try:
                                iface_nodes = getattr(self.interface, "nodes", {}) or {}
                                ai = iface_nodes.get(adjacent_id, {})
                                au = ai.get("user", {}) if isinstance(ai.get("user"), dict) else {}
                                heard_short = au.get("shortName") or ai.get("shortName")
                                heard_long  = au.get("longName")  or ai.get("longName")
                            except Exception:
                                pass
                        label_path = [self._label_for_node(n) for n in path]
                        bbs = getattr(self, "bbs", None)
                        if bbs and hasattr(bbs, "notify_new_node"):
                            try:
                                bbs.notify_new_node(
                                    sender, pending["short_name"], pending["long_name"],
                                    hops=len(path) - 1, snr=pending["snr"],
                                    heard_by=adjacent_id if adjacent_id != self._own_node_id else None,
                                    heard_by_short=heard_short or pending.get("heard_by_short"),
                                    heard_by_long=heard_long or pending.get("heard_by_long"),
                                    traceroute_path=label_path,
                                )
                                logger.info("notify_new_node (post-traceroute) enviado para %s: ruta=%s",
                                            sender, " → ".join(label_path))
                            except Exception:
                                logger.exception("notify_new_node (post-traceroute) failed for %s", sender)
                return

            # ------------------------------------------------------------
            # ✅ NODEINFO_APP — capturar clave pública del nodo
            # ------------------------------------------------------------
            if portnum == "NODEINFO_APP" and sender:
                try:
                    user_info = decoded.get("user") or {}
                    if isinstance(user_info, dict):
                        pk_raw = user_info.get("publicKey")
                        if pk_raw:
                            import base64 as _b64
                            try:
                                if isinstance(pk_raw, bytes):
                                    pk_bytes = pk_raw
                                elif isinstance(pk_raw, str):
                                    pk_bytes = _b64.b64decode(pk_raw + "==")
                                else:
                                    pk_bytes = bytes(pk_raw)
                                pk_b64 = _b64.b64encode(pk_bytes).decode()
                                logger.info("NODEINFO de %s: pubkey=%s (%s)",
                                            sender, pk_bytes.hex(), pk_b64)
                                if len(pk_bytes) == 32:
                                    self._pkt_pub_keys[sender] = pk_bytes
                                    logger.info("Clave de %s actualizada desde NodeInfo", sender)
                            except Exception as _pke:
                                logger.debug("NODEINFO pubkey parse error: %s", _pke)
                        else:
                            logger.info("NODEINFO de %s: sin clave pública", sender)
                except Exception:
                    logger.exception("Error procesando NODEINFO publicKey de %s", sender)
                return

            # ------------------------------------------------------------
            # --- DM ONLY filter (texto) ---
            # ------------------------------------------------------------
            if portnum != "TEXT_MESSAGE_APP":
                return

            # Ignorar canal/broadcast (salvo que haya un callback interesado)
            if is_broadcast:
                logger.info(f"Ignoring channel message from {sender}: {text}")
                if self.on_channel_message and sender and not self._is_duplicate_packet(packet.get("id")):
                    try:
                        channel_idx = packet.get("channel", 0) or 0
                        response = self.on_channel_message(sender, text, channel_idx)
                        if response:
                            self.send_channel_message(response, channel_index=channel_idx)
                    except Exception:
                        logger.exception("on_channel_message failed for %s", sender)
                return

            # Handle standard DM text messages
            logger.info("TEXT_MSG from=%s to_id=%s to_num=%s is_bcast=%s text=%r",
                        sender, to_id, to_num, is_broadcast, text)
            if text and sender:
                logger.info(f"Message received from {sender}: {text}")
                if self.handle_message:
                    response = self.handle_message(sender, text)
                    if response:
                        self.send_message(sender, response)
                else:
                    logger.warning("handle_message not set, cannot respond")

        except Exception:
            try:
                if 'sender' in locals() and sender:
                    traffic_stats.record_error(sender)
            except Exception:
                pass
            logger.exception("Error processing received packet")

    # ------------------------------------------------------------------
    # PKC (Public Key Cryptography) DM decryption
    # ------------------------------------------------------------------

    def _on_receive_encrypted(self, packet, interface):
        """
        Maneja paquetes que llegan encriptados (DMs que el firmware no pudo descifrar).
        Intenta descifrarlos via PKC (X25519+AES) o canal PSK.
        """
        try:
            if packet.get("decoded"):
                # Aprovechar para capturar publicKey de NODEINFO_APP aunque sea decoded
                decoded = packet.get("decoded") or {}
                if decoded.get("portnum") == "NODEINFO_APP":
                    sender = packet.get("fromId") or f"!{packet.get('from', 0):08x}"
                    if isinstance(sender, str):
                        sender = sender.lower()
                    user_info = decoded.get("user") or {}
                    if isinstance(user_info, dict):
                        pk_raw = user_info.get("publicKey")
                        if pk_raw:
                            import base64 as _b64
                            try:
                                if isinstance(pk_raw, bytes):
                                    pk_bytes = pk_raw
                                elif isinstance(pk_raw, str):
                                    pk_bytes = _b64.b64decode(pk_raw + "==")
                                else:
                                    pk_bytes = bytes(pk_raw)
                                if len(pk_bytes) == 32:
                                    self._pkt_pub_keys[sender] = pk_bytes
                                    logger.info("NodeInfo[encrypted-handler] de %s: pubkey=%s (%s)",
                                                sender, pk_bytes.hex(),
                                                _b64.b64encode(pk_bytes).decode())
                            except Exception as _pke:
                                logger.debug("NodeInfo pubkey parse error enc-handler: %s", _pke)
                return
            if "encrypted" not in packet:
                return

            to_id = (packet.get("toId") or "").lower()
            to_num = packet.get("to")
            is_broadcast = (to_id == "^all" or
                            (isinstance(to_num, int) and to_num == 0xFFFFFFFF))
            if is_broadcast:
                return

            own_id = (self._own_node_id or "").lower()
            if not own_id or to_id != own_id:
                return

            sender = packet.get("fromId")
            if not sender:
                frm = packet.get("from")
                if isinstance(frm, int):
                    sender = f"!{frm:08x}"
            if isinstance(sender, str):
                sender = sender.lower()

            logger.info("DM encriptado de %s → intentando descifrar", sender)

            # Log diagnóstico completo del paquete
            raw_pkt = packet.get("raw")
            if raw_pkt:
                try:
                    for fd, fv in raw_pkt.ListFields():
                        logger.debug("PKTFIELD: %s(#%d) type=%s val=%s",
                                     fd.name, fd.number, fd.type,
                                     (bytes(fv).hex()[:64] if fd.type == fd.TYPE_BYTES
                                      else str(fv)[:80]))
                except Exception as _e:
                    logger.debug("PKTFIELD scan error: %s", _e)
            # Campos clave del dict SDK
            logger.debug("PKT dict: pkiEncrypted=%s publicKey=%s channel=%s",
                         packet.get("pkiEncrypted"),
                         packet.get("publicKey"),
                         packet.get("channel"))

            text = self._try_decrypt_pki(packet)
            if not text:
                logger.warning("DM de %s: todos los intentos de descifrado fallaron", sender)
                return

            logger.info("DM de %s descifrado: %r", sender, text)
            if self.handle_message:
                response = self.handle_message(sender, text)
                if response:
                    # Usar la clave correcta para encriptar la respuesta si la tenemos
                    pki_pub = self._decrypt_key_used.get(sender)
                    if pki_pub:
                        self._send_pki_encrypted(sender, response, pki_pub)
                    else:
                        self.send_message(sender, response)

        except Exception:
            logger.exception("Error en _on_receive_encrypted")

    def _try_decrypt_pki(self, packet):
        """
        Intenta descifrar un DM encriptado.
        ALGORITMO CORRECTO según firmware Meshtastic:
          - PKC: AES-256-CCM (L=2 fijo) con clave = SHA256(X25519(priv, pub))
                 Nonce = packetId_LE8 + fromNode_LE4 + extraNonce_LE4 (los primeros 13 bytes)
                 Formato enc: [ciphertext][8-byte auth tag][4-byte extra_nonce]
          - Canal PSK: AES-128-CTR (o AES-256-CTR) con nonce estándar
        """
        import struct, base64, hashlib

        try:
            from cryptography.hazmat.primitives.asymmetric.x25519 import (
                X25519PrivateKey, X25519PublicKey
            )
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.backends import default_backend
        except ImportError:
            logger.warning("decrypt: 'cryptography' no instalado")
            return None

        try:
            from meshtastic import mesh_pb2
        except ImportError:
            logger.warning("decrypt: meshtastic.mesh_pb2 no disponible")
            return None

        # --- AES block encrypt (ECB) ---
        def aes_block(key, block_bytes):
            c = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
            return c.encryptor().update(bytes(block_bytes)) + c.encryptor().finalize()

        # --- AES-CCM decrypt, L=2 fijo, sin AAD (igual que firmware Meshtastic) ---
        def aes_ccm_decrypt(key, nonce13, ciphertext, auth_tag, M=8):
            L = 2
            a = bytearray(16)
            a[0] = L - 1  # Flags = L' = 1
            a[1:14] = nonce13  # copia los 13 bytes del nonce
            # S_0 = AES(key, A_0) donde A_0 tiene counter=0
            a[14] = 0; a[15] = 0
            S_0 = aes_block(key, a)
            T = bytes(x ^ y for x, y in zip(auth_tag[:M], S_0[:M]))
            # Descifrar ciphertext: plaintext = ct XOR S_i para i=1,2,...
            plain = bytearray()
            for i in range(1, (len(ciphertext) + 15) // 16 + 1):
                a[14] = (i >> 8) & 0xFF
                a[15] = i & 0xFF
                S_i = aes_block(key, a)
                s = (i - 1) * 16
                e = min(i * 16, len(ciphertext))
                plain.extend(x ^ y for x, y in zip(S_i[:e - s], ciphertext[s:e]))
            plaintext = bytes(plain)
            # Verificar: CBC-MAC sobre plaintext
            b = bytearray(16)
            b[0] = ((M - 2) // 2) << 3 | (L - 1)  # 0x19 para M=8
            b[1:14] = nonce13
            b[14] = (len(ciphertext) >> 8) & 0xFF
            b[15] = len(ciphertext) & 0xFF
            X = aes_block(key, b)
            for i in range(0, len(plaintext), 16):
                block = (plaintext[i:i + 16]).ljust(16, b'\x00')
                X = aes_block(key, bytes(x ^ y for x, y in zip(X, block)))
            if T[:M] == X[:M]:
                return plaintext
            logger.debug("ccm_decrypt: auth FAIL T=%s X=%s", T[:M].hex(), X[:M].hex())
            return None

        # --- Decodificar plaintext como proto o UTF-8 ---
        def parse_plaintext(pt, label):
            if not pt:
                return None
            try:
                d = mesh_pb2.Data()
                d.ParseFromString(pt)
                if d.portnum == 1:
                    text = d.payload.decode("utf-8", errors="replace")
                    logger.info("decrypt[%s]: proto OK → %r", label, text)
                    return text
                elif d.portnum != 0:
                    return None
            except Exception:
                pass
            try:
                raw = pt.decode("utf-8")
                if raw.strip() and all(c >= ' ' or c in '\n\r\t' for c in raw):
                    logger.info("decrypt[%s]: UTF-8 raw → %r", label, raw)
                    return raw.strip()
            except UnicodeDecodeError:
                pass
            return None

        sender_id = "?"
        try:
            priv_bytes = self.interface.localNode.localConfig.security.private_key
            if not priv_bytes or len(priv_bytes) != 32:
                logger.warning("decrypt: clave privada inválida (len=%s)",
                               len(priv_bytes) if priv_bytes else 0)
                return None

            sender_id = packet.get("fromId") or f"!{packet.get('from', 0):08x}"
            if isinstance(sender_id, str):
                sender_id = sender_id.lower()

            packet_id = packet.get("id", 0)
            from_num  = packet.get("from", 0)

            enc = packet.get("encrypted")
            if isinstance(enc, str):
                enc = base64.b64decode(enc)
            if not enc:
                return None
            enc = bytes(enc)
            # Loguear packet_id para diagnóstico de nonce
            logger.info("decrypt: packet_id=0x%08x from_num=0x%08x enc(%d)=%s",
                        packet_id, from_num, len(enc), enc.hex())

            priv_key = X25519PrivateKey.from_private_bytes(bytes(priv_bytes))

            # ── Nonce estándar para AES-CTR (canal PSK) ──────────────────
            nonce_ctr = (struct.pack("<Q", packet_id & 0xFFFFFFFF) +
                         struct.pack("<I", from_num & 0xFFFFFFFF) +
                         b'\x00\x00\x00\x00')

            # ── Función PKC: AES-256-CCM con SHA256(X25519) ───────────────
            def try_pki_ccm(pub_bytes, label):
                """PKC correcto: AES-CCM M=8 + SHA256(X25519).
                Nonce según firmware initNonce() (CryptoEngine.cpp):
                  nonce[0..3]  = packetId low 32 bits (LE)
                  nonce[4..7]  = extraNonce (LE, si != 0 sobrescribe high 32-bits de packetId)
                  nonce[8..11] = fromNode (LE)
                  nonce[12]    = 0 (siempre)
                enc layout: [ciphertext][8-byte auth tag][4-byte extraNonce]
                """
                if len(enc) < 13:  # mínimo: 1 byte ct + 8 auth + 4 extra_nonce
                    return None
                try:
                    pub_key = X25519PublicKey.from_public_bytes(bytes(pub_bytes))
                    shared = priv_key.exchange(pub_key)
                    hashed_key = hashlib.sha256(shared).digest()

                    # enc = ciphertext + tag(8) + extra_nonce(4)
                    extra_nonce = struct.unpack("<I", enc[-4:])[0]
                    ct  = enc[:-12]   # ciphertext
                    tag = enc[-12:-4] # 8-byte auth tag (M=8)

                    # Nonce EXACTO del firmware (initNonce en CryptoEngine.cpp):
                    # memcpy(nonce,   &packetId,   8);  → nonce[0..7]
                    # memcpy(nonce+8, &fromNode,   4);  → nonce[8..11]
                    # if(extraNonce) memcpy(nonce+4, &extraNonce, 4); → nonce[4..7] (PISA hi-32 de packetId)
                    nonce13 = bytearray(13)  # nonce[12] siempre queda 0
                    struct.pack_into("<I", nonce13, 0, packet_id & 0xFFFFFFFF)
                    struct.pack_into("<I", nonce13, 8, from_num & 0xFFFFFFFF)
                    if extra_nonce:
                        struct.pack_into("<I", nonce13, 4, extra_nonce & 0xFFFFFFFF)
                    nonce13 = bytes(nonce13)

                    logger.debug("pki_ccm[%s]: shared=%s hashed=%s extra=0x%08x "
                                 "nonce13=%s ct=%s tag=%s",
                                 label, shared.hex()[:16], hashed_key.hex()[:16],
                                 extra_nonce, nonce13.hex(), ct.hex()[:32], tag.hex())
                    pt = aes_ccm_decrypt(hashed_key, nonce13, ct, tag)
                    if pt is not None:
                        return parse_plaintext(pt, f"pki_ccm_{label}")
                except Exception as e:
                    logger.debug("pki_ccm[%s]: error: %s", label, e)
                return None

            # ── Función CTR (AES-CTR, para PSK o X25519 sin hash) ─────────
            def try_aes_ctr(key_bytes, nonce, label):
                try:
                    c = Cipher(algorithms.AES(key_bytes), modes.CTR(nonce),
                               backend=default_backend())
                    pt = c.decryptor().update(enc)
                    return parse_plaintext(pt, label)
                except Exception as e:
                    logger.debug("aes_ctr[%s]: error: %s", label, e)
                return None

            # === Obtener public key del sender ===
            nodes   = getattr(self.interface, "nodes", {}) or {}
            info    = nodes.get(sender_id, {})
            user    = info.get("user", {}) if isinstance(info.get("user"), dict) else {}
            pub_raw = user.get("publicKey")
            logger.debug("decrypt: sender=%s dict_pub=%s", sender_id, repr(pub_raw)[:60])

            # Candidatos de clave pública para PKC
            pub_candidates = []

            # ── 0. Claves verificadas manualmente (override por nodo) ───────
            KNOWN_NODE_KEYS = {
                # pilgrim: clave confirmada directamente desde su app Meshtastic
                "!da4846ec": "q55qEsjVIAYK0GCPcs/YT/P2rlRA55WmcUB/WsMccAs=",
            }
            known_b64 = KNOWN_NODE_KEYS.get(sender_id)
            if known_b64:
                try:
                    known_pb = base64.b64decode(known_b64)
                    if len(known_pb) == 32:
                        pub_candidates.append(("known", known_pb))
                except Exception:
                    pass

            # ── 1. Clave capturada del raw MeshPacket en el hook (MÁS CONFIABLE) ──
            hook_pub = self._pkt_pub_keys.get(sender_id)
            if hook_pub and len(hook_pub) == 32:
                pub_candidates.append(("hook_pkt", hook_pub))
                logger.info("decrypt: usando hook_pub=%s para %s", hook_pub.hex(), sender_id)

            # ── 2. Clave del nodo dict (SDK cache) ──
            if pub_raw:
                try:
                    if isinstance(pub_raw, bytes):
                        pb = pub_raw
                    else:
                        pb = base64.b64decode(str(pub_raw) + "==")
                    if len(pb) == 32 and pb != hook_pub:
                        pub_candidates.append(("dict", pb))
                except Exception:
                    pass

            # ── 3. Clave del paquete (campo raw del SDK, si existe) ──
            raw_pkt = packet.get("raw")
            if raw_pkt:
                try:
                    pkt_pub = bytes(raw_pkt.public_key) if raw_pkt.public_key else None
                    if pkt_pub and len(pkt_pub) == 32 and pkt_pub not in (hook_pub, pub_raw):
                        pub_candidates.append(("pkt_raw", pkt_pub))
                except Exception:
                    pass

            # === Paso 1: PKC con AES-CCM (CORRECTO según firmware) ========
            for label, pb in pub_candidates:
                r = try_pki_ccm(pb, label)
                if r:
                    # Guardar la clave que funcionó para usarla al responder
                    self._decrypt_key_used[sender_id] = bytes(pb)
                    return r

            # === Paso 2: PKC con AES-CTR (variante antigua, por si acaso) ==
            for label, pb in pub_candidates:
                try:
                    shared_ctr = priv_key.exchange(X25519PublicKey.from_public_bytes(pb))
                    r = try_aes_ctr(shared_ctr, nonce_ctr, f"X25519_ctr_{label}")
                    if r: return r
                except Exception:
                    pass

            # === Paso 3: Canal PSK con AES-CTR =============================
            try:
                channels = getattr(self.interface.localNode, 'channels', [])
                DEFAULT_PSK = bytes.fromhex("d4f1bb3a20290759f0bcffabcf4e6901")
                for i, ch in enumerate(channels):
                    psk_raw = bytes(ch.settings.psk)
                    if not psk_raw:
                        continue
                    if psk_raw == b'\x01':
                        psk = DEFAULT_PSK
                    elif len(psk_raw) in (16, 32):
                        psk = psk_raw
                    else:
                        continue
                    logger.debug("decrypt: ch%d PSK(%d)=%s", i, len(psk), psk.hex()[:16])
                    r = try_aes_ctr(psk, nonce_ctr, f"ch{i}_psk_ctr")
                    if r: return r
            except Exception as e:
                logger.debug("decrypt: canal PSK error: %s", e)

            logger.warning("decrypt: TODOS los intentos fallaron para %s enc=%s",
                           sender_id, enc.hex())
            return None

        except Exception as exc:
            logger.exception("decrypt: error inesperado para %s", sender_id)
            return None

    # ------------------------------------------------------------------
    # PKC response: encriptar y enviar con la clave correcta del sender
    # ------------------------------------------------------------------

    def _aes_ccm_encrypt(self, key: bytes, nonce13: bytes, plaintext: bytes, M: int = 8):
        """AES-CCM encrypt (L=2, M=8) — imagen especular del decrypt del firmware."""
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.backends import default_backend

        def aes_block(k, b):
            c = Cipher(algorithms.AES(k), modes.ECB(), backend=default_backend())
            return c.encryptor().update(bytes(b)) + c.encryptor().finalize()

        L = 2
        # CBC-MAC tag
        b = bytearray(16)
        b[0] = ((M - 2) // 2) << 3 | (L - 1)  # 0x19 para M=8, L=2
        b[1:14] = nonce13
        b[14] = (len(plaintext) >> 8) & 0xFF
        b[15] = len(plaintext) & 0xFF
        X = aes_block(key, b)
        for i in range(0, len(plaintext), 16):
            blk = (plaintext[i:i + 16] + b'\x00' * 16)[:16]
            X = aes_block(key, bytes(x ^ y for x, y in zip(X, blk)))
        cbc_mac = X[:M]

        # Encriptar ciphertext con CTR
        a = bytearray(16)
        a[0] = L - 1
        a[1:14] = nonce13
        ct = bytearray()
        for i in range(1, (len(plaintext) + 15) // 16 + 1):
            a[14] = (i >> 8) & 0xFF
            a[15] = i & 0xFF
            Si = aes_block(key, a)
            s = (i - 1) * 16
            e = min(i * 16, len(plaintext))
            ct.extend(x ^ y for x, y in zip(Si[:e - s], plaintext[s:e]))

        # Encriptar el tag con S0
        a[14] = 0; a[15] = 0
        S0 = aes_block(key, a)
        auth_tag = bytes(x ^ y for x, y in zip(cbc_mac, S0[:M]))

        return bytes(ct), auth_tag

    def _send_pki_encrypted(self, dest_id: str, text: str, sender_pub_bytes: bytes):
        """
        Encripta y envía un DM PKC con la clave pública correcta del destinatario,
        igual que lo haría el firmware. Bypassa el encriptado del firmware (que usa
        nodeDB y puede tener clave vieja).
        """
        import os, struct, hashlib

        try:
            from cryptography.hazmat.primitives.asymmetric.x25519 import (
                X25519PrivateKey, X25519PublicKey
            )
            from meshtastic import mesh_pb2
        except ImportError:
            logger.warning("_send_pki_encrypted: libs no disponibles, fallback sendText")
            self.send_message(dest_id, text)
            return

        try:
            priv_bytes = bytes(self.interface.localNode.localConfig.security.private_key)
            bbs_pub_bytes = bytes(self.interface.localNode.localConfig.security.public_key)
            priv_key = X25519PrivateKey.from_private_bytes(priv_bytes)
            dest_pub = X25519PublicKey.from_public_bytes(sender_pub_bytes)
            shared = priv_key.exchange(dest_pub)
            aes_key = hashlib.sha256(shared).digest()

            dest_num = int(dest_id.lstrip('!'), 16)
            from_num = self.interface.myInfo.my_node_num

            chunks = self._split_chunks(text)
            for chunk in chunks:
                # Construir Data proto (igual que el firmware enviaría)
                data_pb = mesh_pb2.Data()
                data_pb.portnum = 1  # TEXT_MESSAGE_APP
                data_pb.payload = chunk.encode('utf-8')
                plaintext = data_pb.SerializeToString()

                # Generar packet_id y extra_nonce aleatorios
                packet_id = struct.unpack("<I", os.urandom(4))[0] & 0x7FFFFFFF  # evitar negativos
                extra_nonce_bytes = os.urandom(4)
                extra_nonce = struct.unpack("<I", extra_nonce_bytes)[0]

                # Nonce exacto del firmware initNonce()
                nonce13 = bytearray(13)
                struct.pack_into("<I", nonce13, 0, packet_id & 0xFFFFFFFF)
                struct.pack_into("<I", nonce13, 8, from_num & 0xFFFFFFFF)
                if extra_nonce:
                    struct.pack_into("<I", nonce13, 4, extra_nonce & 0xFFFFFFFF)

                ct, auth_tag = self._aes_ccm_encrypt(aes_key, bytes(nonce13), plaintext)

                # enc = ciphertext + tag(8) + extra_nonce(4)
                encrypted_field = ct + auth_tag + extra_nonce_bytes

                # Construir MeshPacket con encrypted field ya listo
                mp = mesh_pb2.MeshPacket()
                mp.to = dest_num
                mp.id = packet_id
                mp.encrypted = encrypted_field
                mp.pki_encrypted = True
                mp.public_key = bbs_pub_bytes  # clave pública del BBS (sender)
                mp.want_ack = True
                mp.hop_limit = 5

                # Enviar via _sendPacket — preserva el campo encrypted sin re-encriptar
                self.interface._sendPacket(
                    mp,
                    destinationId=dest_num,
                    wantAck=True,
                    hopLimit=5,
                    pkiEncrypted=True,
                    publicKey=bbs_pub_bytes,
                )
                logger.info("PKI send → %s chunk=%r pkt_id=0x%08x extra=0x%08x",
                            dest_id, chunk[:40], packet_id, extra_nonce)
                time.sleep(0.2)

        except Exception:
            logger.exception("_send_pki_encrypted falló para %s, fallback sendText", dest_id)
            self.send_message(dest_id, text)

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
            36: "NODE_STATUS_APP",
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
                        # Capturar clave pública del sender desde el raw MeshPacket
                        # (campo public_key del proto, enviado por el firmware del sender en DMs PKC)
                        try:
                            raw_pk = bytes(mp.public_key) if mp.public_key else b""
                            if len(raw_pk) == 32:
                                self._pkt_pub_keys[from_id] = raw_pk
                                logger.info("PKC DM de %s: public_key=%s pki_encrypted=%s",
                                            from_id, raw_pk.hex(), mp.pki_encrypted)
                            else:
                                logger.info("PKC DM de %s: public_key vacía pki_encrypted=%s",
                                            from_id, mp.pki_encrypted)
                        except Exception as _pk_err:
                            logger.debug("No se pudo leer public_key del raw packet: %s", _pk_err)
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
    NEW_NODE_NOTIFY_TIMEOUT = 45     # seg máx de espera por el traceroute antes de avisar sin ruta

    def _label_for_node(self, node_id: str) -> str:
        """Nombre corto para mostrar un nodo en una ruta (short_name > long_name > ID)."""
        if node_id == self._own_node_id:
            return "BBS"
        try:
            nodes = getattr(self.interface, "nodes", {}) or {}
            info = nodes.get(node_id, {}) or {}
            user = info.get("user", {}) if isinstance(info.get("user"), dict) else {}
            short = user.get("shortName") or info.get("shortName")
            long_ = user.get("longName") or info.get("longName")
            return short or long_ or node_id
        except Exception:
            return node_id

    def _flush_stale_new_node_notifies(self, now: float):
        """Si un traceroute no respondió a tiempo, avisar igual (sin ruta) para no perder el aviso."""
        if not self._pending_new_node_notify:
            return
        stale = [nid for nid, data in self._pending_new_node_notify.items()
                 if now - data["queued_at"] >= self.NEW_NODE_NOTIFY_TIMEOUT]
        for nid in stale:
            data = self._pending_new_node_notify.pop(nid)
            logger.info("new node %s: timeout esperando traceroute, aviso sin ruta", nid)
            bbs = getattr(self, "bbs", None)
            if bbs and hasattr(bbs, "notify_new_node"):
                try:
                    bbs.notify_new_node(
                        nid, data["short_name"], data["long_name"],
                        hops=data["hops"], snr=data["snr"],
                        heard_by=data["heard_by"],
                        heard_by_short=data["heard_by_short"],
                        heard_by_long=data["heard_by_long"],
                    )
                except Exception:
                    logger.exception("notify_new_node (timeout fallback) failed for %s", nid)

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

        self._flush_stale_new_node_notifies(now)

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

                if now - self._traceroute_tick_last >= 1.0:
                    self._traceroute_tick_last = now
                    try:
                        self._tick_traceroute(now)
                    except Exception:
                        logger.exception("_tick_traceroute() failed")

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
        Returns True only if every chunk was handed to the Meshtastic SDK.
        Existing callers may ignore the return value.
        """
        if not self.interface:
            logger.warning("Cannot send channel message: Meshtastic interface is disconnected")
            return False

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
            return True

        except Exception as e:
            logger.error(f"Failed to send channel message on channelIndex={channel_index}: {e}")
            return False

    def send_channel_waypoint(
        self,
        *,
        waypoint_id,
        name,
        description,
        latitude,
        longitude,
        expire,
        channel_index=0,
        icon=ord("🔥"),
    ):
        """Send/update a Meshtastic waypoint on a channel and report success."""
        if not self.interface:
            logger.warning("Cannot send waypoint: Meshtastic interface is disconnected")
            return False

        try:
            self.interface.sendWaypoint(
                name=name,
                description=description,
                icon=icon,
                expire=int(expire),
                waypoint_id=int(waypoint_id),
                latitude=float(latitude),
                longitude=float(longitude),
                destinationId="^all",
                wantAck=False,
                channelIndex=channel_index,
            )
            logger.info(
                "Sent waypoint id=%s on channelIndex=%s at %.5f,%.5f",
                waypoint_id, channel_index, latitude, longitude
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send waypoint id={waypoint_id} on channelIndex={channel_index}: {e}")
            return False

if __name__ == "__main__":
    interface = Interface()
    interface.run()

