"""Menú principal: lista los cortes programados de EDES ya scrapeados
(ver edes_scraper.py), agrupados por localidad. Lectura pura del último
scrape guardado -- entrar a este menú no dispara un scrape nuevo."""
from datetime import date, datetime

import edes_scraper

menu_name = "EDES Cortes Programados"

SIN_CORTES = "No hay cortes programados de EDES informados por el momento."
PARTIDO_ORDER = ["Bahia Blanca", "Monte Hermoso", "Coronel Dorrego", "Tornquist"]


def _orden_localidad(nombre):
    norm = nombre.strip().upper()
    for i, p in enumerate(PARTIDO_ORDER):
        if p.upper() == norm:
            return (0, i)
    return (1, norm)


def _render():
    hoy = date.today()
    vigentes = [
        o for o in edes_scraper.get_latest_outages()
        if datetime.strptime(o["outage_date"], "%Y-%m-%d").date() >= hoy
    ]

    por_localidad = {}
    for o in vigentes:
        por_localidad.setdefault(o["locality"], []).append(o)

    lines = ["⚡ CORTES PROGRAMADOS EDES", ""]

    if not por_localidad:
        lines.append(SIN_CORTES)
    else:
        for idx, loc in enumerate(sorted(por_localidad.keys(), key=_orden_localidad)):
            if idx > 0:
                lines.append("")
            lines.append(f"📍 {loc}")
            cortes = sorted(por_localidad[loc], key=lambda o: (o["outage_date"], o["time_start"]))
            for o in cortes:
                dia = datetime.strptime(o["outage_date"], "%Y-%m-%d").strftime("%d/%m")
                lines.append("· · · · · · · · · ·")
                lines.append(f"🗓 {dia}, {o['time_start']} a {o['time_end']}")
                lines.append(f"   Calles: {o['streets']}")

    lines.append("")
    ts = edes_scraper.get_latest_scrape_ts()
    if ts is not None:
        lines.append(f"Última actualización: {datetime.fromtimestamp(ts).strftime('%d/%m %H:%M')}")
    lines.append("0. ↩ Volver")

    return "\n".join(lines)


def display_menu():
    return _render()


def process_command(user_id, command, bbs_system):
    if command.strip() == "0" or command.strip().lower() == "cd ..":
        return "__back__"
    return _render()
