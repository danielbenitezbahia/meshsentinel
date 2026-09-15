import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
ART = ZoneInfo("America/Argentina/Buenos_Aires")

ACTION_TEXT = "TEXT"
ACTION_WAYPOINT_CREATE = "WAYPOINT_CREATE"
ACTION_WAYPOINT_UPDATE = "WAYPOINT_UPDATE"


def _satellite_display(value: str) -> str:
    return {"N20": "NOAA-20", "N21": "NOAA-21"}.get(value, value)


def _format_art(iso_value: str) -> str:
    dt = datetime.fromisoformat(iso_value)
    return dt.astimezone(ART).strftime("%H:%M ART")


def build_text_payload(event: dict, previous_level: int) -> dict:
    satellites = sorted(_satellite_display(s) for s in event["satellites"])
    level = event["current_level"]
    if level >= 3:
        detail = "Detectado por " + " + ".join(satellites)
    elif level == 2:
        detail = f"Detección reiterada por {satellites[0]} · {event['pass_count']} pasadas"
    else:
        if event["max_pixels_in_observation"] >= 2:
            detail = f"{satellites[0]} · {event['max_pixels_in_observation']} anomalías próximas"
        else:
            detail = f"Anomalía de alta confianza · {satellites[0]}"

    prefix = "🔥 ACTUALIZACIÓN" if previous_level > 0 else "🔥 FOCO TÉRMICO"
    place = " / ".join(sorted(event["partidos"]))
    message = (
        f"{prefix} | {place}\n"
        f"{detail}\n"
        f"{_format_art(event['last_seen'])}\n"
        "📍 Waypoint Meshtastic\n"
        f"🌐 https://maps.google.com/?q={event['latest_latitude']:.5f},{event['latest_longitude']:.5f}"
    )
    return {"message": message}


def build_waypoint_payload(event: dict, ttl_hours: int) -> dict:
    last_seen = datetime.fromisoformat(event["last_seen"])
    expire = int(last_seen.timestamp() + ttl_hours * 3600)
    place = " / ".join(sorted(event["partidos"]))
    satellites = ", ".join(sorted(_satellite_display(s) for s in event["satellites"]))
    description = f"Última detección {_format_art(event['last_seen'])} · {satellites}"
    return {
        "waypoint_id": event["waypoint_id"],
        "name": f"Foco térmico - {place}"[:30],
        "description": description[:100],
        "latitude": event["latest_latitude"],
        "longitude": event["latest_longitude"],
        "expire": expire,
        "icon": ord("🔥"),
    }


def process_pending_actions(iface, service) -> int:
    sent = 0
    for action in service.get_pending_outbox():
        payload = json.loads(action["payload_json"])
        try:
            if action["action_type"] == ACTION_TEXT:
                ok = iface.send_channel_message(
                    payload["message"],
                    channel_index=action["channel_index"],
                )
            elif action["action_type"] in (ACTION_WAYPOINT_CREATE, ACTION_WAYPOINT_UPDATE):
                ok = iface.send_channel_waypoint(
                    waypoint_id=payload["waypoint_id"],
                    name=payload["name"],
                    description=payload["description"],
                    latitude=payload["latitude"],
                    longitude=payload["longitude"],
                    expire=payload["expire"],
                    icon=payload.get("icon", "🔥"),
                    channel_index=action["channel_index"],
                )
            else:
                service.mark_outbox_failed(action["id"], f"unknown action type {action['action_type']}")
                continue

            if ok:
                service.mark_outbox_sent(action["id"])
                sent += 1
            else:
                service.mark_outbox_failed(action["id"], "Meshtastic send returned false")
        except Exception as exc:
            logger.exception("FIRMS outbox delivery failed: id=%s", action["id"])
            service.mark_outbox_failed(action["id"], str(exc))
    return sent
