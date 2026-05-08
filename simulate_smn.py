#!/usr/bin/env python3
import sys, os, shutil
os.chdir('/home/daniel/bbs/MeshBoard')
sys.path.insert(0, '/home/daniel/bbs/MeshBoard')

# Copiar DB para no bloquear el servicio
shutil.copy('/home/daniel/bbs/meshsentinel/weather_alerts.sqlite', '/tmp/weather_alerts_sim.sqlite')

import weather_alert_service
weather_alert_service.DB_PATH = '/tmp/weather_alerts_sim.sqlite'
from datetime import datetime, timedelta

PARTIDO_ORDER = ["Bahia Blanca", "Monte Hermoso", "Coronel Dorrego", "Tornquist"]

def severity_to_color(s):
    return {"Moderate": "🟡", "Severe": "🟠", "Extreme": "🔴"}.get((s or "").strip(), "⚪")

def alert_icon(title):
    t = (title or "").lower()
    if "torment" in t: return "⛈"
    if "viento" in t: return "💨"
    if "lluv" in t: return "🌧"
    if "nieve" in t or "nevada" in t: return "🌨"
    if "niebla" in t: return "🌫"
    return "⚠"

def day_abbr_es(dt):
    return ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"][dt.weekday()]

def moment_of_day_es(dt):
    h = dt.hour
    if h < 6: return "madrugada"
    if h < 12: return "mañana"
    if h < 18: return "tarde"
    return "noche"

def format_window(onset_str, expires_str):
    try:
        o = datetime.fromisoformat(onset_str)
        e = datetime.fromisoformat(expires_str)
    except Exception:
        return "vigente"
    d1, p1 = day_abbr_es(o), moment_of_day_es(o)
    d2, p2 = day_abbr_es(e), moment_of_day_es(e)
    if d1 == d2 and p1 == p2: return f"{d1} {p1}"
    if d1 == d2: return f"{d1} {p1}/{p2}"
    return f"{d1} {p1} → {d2} {p2}"

def parse_dt(s):
    try:
        return datetime.fromisoformat(s) if s else None
    except Exception:
        return None

def merge_alerts(alerts):
    GAP = timedelta(hours=3)
    SEV = {"Extreme": 0, "Severe": 1, "Moderate": 2, "": 3}
    groups = {}
    for a in alerts:
        k = ((a.get("title") or "").strip().upper(), (a.get("severity") or "").strip())
        groups.setdefault(k, []).append(a)
    merged = []
    for group in groups.values():
        seen, deduped = set(), []
        for a in group:
            w = (a.get("onset") or "", a.get("expires") or "")
            if w not in seen:
                seen.add(w); deduped.append(a)
        sg = sorted(deduped, key=lambda a: a.get("onset") or "")
        cur = dict(sg[0])
        for nxt in sg[1:]:
            ce = parse_dt(cur.get("expires"))
            no = parse_dt(nxt.get("onset"))
            ne = parse_dt(nxt.get("expires"))
            if ce and no and (no - ce) <= GAP:
                if ne and ne > ce:
                    cur["expires"] = nxt["expires"]
            else:
                merged.append(cur); cur = dict(nxt)
        merged.append(cur)
    merged.sort(key=lambda a: (SEV.get(a.get("severity") or "", 3), a.get("onset") or ""))
    return merged

alerts = weather_alert_service.get_sudoeste_ba_alerts()
by_partido = {}
for alert in alerts:
    for p in (alert.get("affected_partidos") or []):
        by_partido.setdefault(p, []).append(alert)

all_partidos = list(weather_alert_service.TARGET_PARTIDOS.values())
rest = sorted(p for p in all_partidos if p not in PARTIDO_ORDER)
ordered = PARTIDO_ORDER + rest

for i, partido in enumerate(ordered):
    lines = []
    if i == 0:
        lines += ["⚡🚨 ALERTAS SMN 🚨⚡", "── Reporte 18hs · 72hs ──", ""]
    lines.append(f"📍 {partido.upper()}")
    pa = merge_alerts(by_partido.get(partido, []))
    if not pa:
        lines.append("Sin alertas vigentes.")
    else:
        for a in pa:
            lines += [
                "· · · · · · · · · ·",
                f"{severity_to_color(a['severity'])}{alert_icon(a['title'])} {a['title'].upper()}",
                f"🕐 {format_window(a['onset'], a['expires'])}"
            ]
    print(f"\n{'='*35}\nMENSAJE {i+1}:\n{'='*35}")
    print("\n".join(lines))
