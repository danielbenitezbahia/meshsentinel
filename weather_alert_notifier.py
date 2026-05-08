import time
from typing import Optional, Tuple, Dict

from weather_alert_service import (
    init_db,
    get_subscribed_nodes,
    get_unsent_matching_alerts_for_node,
    register_alert_dispatch,
)


ONLINE_WINDOW_SECONDS = 30 * 60   # 30 min
DELIVERY_INTERVAL_SECONDS = 60    # cada 60s


def now_ts() -> int:
    return int(time.time())


def normalize_node_id(node_id: str) -> str:
    node_id = node_id.strip()
    if not node_id.startswith("!"):
        node_id = "!" + node_id
    return node_id.lower()


def is_online(iface, node_id: str) -> bool:
    """
    iface: instancia de tu clase Interface
    lee los nodos desde iface.interface.nodes
    """
    serial_iface = getattr(iface, "interface", None)
    if not serial_iface:
        return False

    nodes = getattr(serial_iface, "nodes", None)
    if not nodes:
        return False

    node_id = normalize_node_id(node_id)
    node = nodes.get(node_id)
    if not node:
        return False

    last = node.get("lastHeard") or node.get("lastHeardTimestamp") or node.get("last_heard")
    if not isinstance(last, (int, float)):
        return False

    return (now_ts() - int(last)) <= ONLINE_WINDOW_SECONDS


def extract_node_position(node: dict) -> Optional[Tuple[float, float]]:
    """
    Intenta extraer (lat, lon) desde distintas estructuras comunes de Meshtastic.
    """
    if not node:
        return None

    # Caso 1: position directo
    position = node.get("position")
    if isinstance(position, dict):
        lat = position.get("latitude")
        lon = position.get("longitude")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            return float(lat), float(lon)

    # Caso 2: user.position
    user = node.get("user")
    if isinstance(user, dict):
        position = user.get("position")
        if isinstance(position, dict):
            lat = position.get("latitude")
            lon = position.get("longitude")
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                return float(lat), float(lon)

    # Caso 3: directos
    lat = node.get("latitude")
    lon = node.get("longitude")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        return float(lat), float(lon)

    return None


def get_node_position(iface, node_id: str) -> Optional[Tuple[float, float]]:
    serial_iface = getattr(iface, "interface", None)
    if not serial_iface:
        return None

    nodes = getattr(serial_iface, "nodes", None)
    if not nodes:
        return None

    node_id = normalize_node_id(node_id)
    node = nodes.get(node_id)
    if not node:
        return None

    return extract_node_position(node)


def format_weather_alert_message(alert: Dict) -> str:
    """
    Formato inicial simple.
    Después lo refinamos.
    """
    title = alert.get("title", "Alerta")
    severity = alert.get("severity", "")
    certainty = alert.get("certainty", "")
    expires = alert.get("expires", "")

    parts = [f"ALERTA {title}"]
    if severity:
        parts.append(f"sev:{severity}")
    if certainty:
        parts.append(f"cert:{certainty}")
    if expires:
        parts.append(f"exp:{expires}")

    return " | ".join(parts)


def send_text_message(iface, to_node_id: str, text: str) -> bool:
    """
    Usa el wrapper send_message() de tu clase Interface.
    """
    try:
        iface.send_message(to_node_id, text)
        return True
    except Exception as exc:
        print(f"[WARN] Error enviando alerta a {to_node_id}: {exc}")
        return False


def process_node_alerts(iface, node_id: str) -> int:
    """
    Procesa alertas para un nodo suscripto.
    Devuelve cuántas alertas envió.
    """
    node_id = normalize_node_id(node_id)

    if not is_online(iface, node_id):
        print(f"[INFO] Nodo {node_id} no está online")
        return 0

    position = get_node_position(iface, node_id)
    if position is None:
        print(f"[INFO] Nodo {node_id} online pero sin posición")
        return 0

    lat, lon = position
    print(f"[INFO] Nodo {node_id} posición lat={lat} lon={lon}")

    matching_alerts = get_unsent_matching_alerts_for_node(
        node_id=node_id,
        node_lat=lat,
        node_lon=lon,
    )

    print(f"[INFO] Nodo {node_id} tiene {len(matching_alerts)} alertas pendientes")

    sent_count = 0

    for alert in matching_alerts:
        msg = format_weather_alert_message(alert)

        ok = send_text_message(iface, node_id, msg)
        if ok:
            register_alert_dispatch(
                node_id=node_id,
                source_xml_url=alert["source_xml_url"],
                status="sent",
            )
            sent_count += 1

    return sent_count


def process_all_subscribed_nodes(iface) -> int:
    """
    Recorre todos los nodos suscriptos y envía alertas pendientes.
    """
    subscribed_nodes = get_subscribed_nodes()
    print(f"[INFO] Nodos suscriptos a weather alerts: {subscribed_nodes}")

    total_sent = 0

    for node_id in subscribed_nodes:
        try:
            sent = process_node_alerts(iface, node_id)
            total_sent += sent
            if sent > 0:
                print(f"[OK] Enviadas {sent} alertas a {node_id}")
        except Exception as exc:
            print(f"[WARN] Error procesando nodo {node_id}: {exc}")

    return total_sent


if __name__ == "__main__":
    init_db()
    print("[OK] Weather alert notifier initialized")