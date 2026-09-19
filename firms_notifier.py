import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
ART = ZoneInfo("America/Argentina/Buenos_Aires")

ACTION_TEXT = "TEXT"
ACTION_TEXT_BURST = "TEXT_BURST"
ACTION_WAYPOINT_CREATE = "WAYPOINT_CREATE"
ACTION_WAYPOINT_UPDATE = "WAYPOINT_UPDATE"
ACTION_WAYPOINT_DELETE = "WAYPOINT_DELETE"


def _satellite_display(value: str) -> str:
    raw = (value or "").strip()
    upper = raw.upper().replace("_", " ")
    if upper == "N20" or "NOAA-20" in upper or "NOAA 20" in upper:
        return "NOAA-20"
    if upper == "N21" or "NOAA-21" in upper or "NOAA 21" in upper:
        return "NOAA-21"
    if upper in {"SNPP", "NPP"} or "SUOMI" in upper:
        return "Suomi NPP"
    return raw


def _format_art(iso_value: str) -> str:
    dt = datetime.fromisoformat(iso_value)
    return dt.astimezone(ART).strftime("%H:%M ART")


def _is_goes(value: str) -> bool:
    return (value or "").strip().upper().startswith("GOES-")


def build_text_payload(event: dict, previous_level: int, fire_weather: dict | None = None) -> dict:
    satellites = sorted(_satellite_display(s) for s in event["satellites"])
    level = event["current_level"]
    goes_only = bool(satellites) and all(_is_goes(s) for s in satellites)

    if level == 0 and goes_only:
        detail = f"{satellites[0]} · detección satelital sin confirmar"
        prefix = "⚠️ POSIBLE FOCO TÉRMICO"
    elif level >= 3:
        detail = "Detectado por " + " + ".join(satellites)
        if event.get("ever_communicated") and previous_level == 0:
            prefix = "🔥 FOCO TÉRMICO CONFIRMADO"
        else:
            prefix = "🔥 ACTUALIZACIÓN" if event.get("ever_communicated") else "🔥 FOCO TÉRMICO"
    elif level == 2:
        detail = f"Detección reiterada por {satellites[0]} · {event['pass_count']} pasadas"
        if event.get("ever_communicated") and previous_level == 0:
            prefix = "🔥 FOCO TÉRMICO CONFIRMADO"
        else:
            prefix = "🔥 ACTUALIZACIÓN" if event.get("ever_communicated") else "🔥 FOCO TÉRMICO"
    else:
        if event["max_pixels_in_observation"] >= 2:
            detail = f"{satellites[0]} · {event['max_pixels_in_observation']} anomalías próximas"
        else:
            detail = f"Anomalía de alta confianza · {satellites[0]}"
        prefix = "🔥 ACTUALIZACIÓN" if event.get("ever_communicated") else "🔥 FOCO TÉRMICO"

    place = " / ".join(sorted(event["partidos"]))

    weather_line = ""
    if fire_weather:
        direction = fire_weather.get("propagation_to")
        direction_text = f"→{direction}" if direction else ""
        weather_line = (
            f"\n⚠️ Propagación {fire_weather['level']} · SMN "
            f"{fire_weather['wind_speed_kmh']}km/h{direction_text} · "
            f"HR{fire_weather['relative_humidity']}% · {fire_weather['temperature_c']:.0f}°C"
        )

    message = (
        f"{prefix} | {place}\n"
        f"{detail}\n"
        f"{_format_art(event['last_seen'])}"
        f"{weather_line}\n"
        "📍 Waypoint Meshtastic\n"
        f"🌐 https://maps.google.com/?q={event['latest_latitude']:.5f}%2C{event['latest_longitude']:.5f}"
    )
    return {"message": message}



def build_burst_text_payload(events: list[dict]) -> dict:
    """Build one compact radio message for a burst of FIRMS-only events."""
    counts: dict[str, int] = {}
    for event in events:
        place = " / ".join(sorted(event["partidos"])) or "Zona sin identificar"
        counts[place] = counts.get(place, 0) + 1

    places = " · ".join(f"{place}: {count}" for place, count in sorted(counts.items()))
    message = (
        f"🔥 FIRMS | {len(events)} eventos térmicos\n"
        f"{places}\n"
        "📍 Waypoints agregados/actualizados en el mapa"
    )
    return {"message": message}

def build_waypoint_payload(event: dict, ttl_hours: int) -> dict:
    last_seen = datetime.fromisoformat(event["last_seen"])
    expire = int(last_seen.timestamp() + ttl_hours * 3600)
    place = " / ".join(sorted(event["partidos"]))
    satellites = ", ".join(sorted(_satellite_display(s) for s in event["satellites"]))

    possible = (
        event["current_level"] == 0
        and bool(event["satellites"])
        and all(_is_goes(s) for s in event["satellites"])
    )

    if possible:
        name = f"Posible foco - {place}"
        description = f"Sin confirmar · {_format_art(event['last_seen'])} · {satellites}"
        icon = ord("⚠")
    else:
        name = f"Foco térmico - {place}"
        description = f"Última detección {_format_art(event['last_seen'])} · {satellites}"
        icon = ord("🔥")

    return {
        "waypoint_id": event["waypoint_id"],
        "name": name[:30],
        "description": description[:100],
        "latitude": event["latest_latitude"],
        "longitude": event["latest_longitude"],
        "expire": expire,
        "icon": icon,
    }


def process_pending_actions(iface, service) -> int:
    sent = 0
    for action in service.get_pending_outbox():
        payload = json.loads(action["payload_json"])
        try:
            if action["action_type"] in (ACTION_TEXT, ACTION_TEXT_BURST):
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
            elif action["action_type"] == ACTION_WAYPOINT_DELETE:
                ok = iface.send_channel_waypoint_delete(
                    waypoint_id=payload["waypoint_id"],
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
