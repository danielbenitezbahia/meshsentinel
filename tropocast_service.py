import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

from tropocast_client import TropocastClient, TropocastError, TropocastRateLimited, TropocastUnauthorized

logger = logging.getLogger(__name__)

DB_PATH = "tropocast.sqlite"
RETENTION_DAYS = 30

# Escala real de Tropocast (campo radio.scale de la respuesta de la API),
# no inventada -- se reusa tal cual para clasificar.
LEVEL_SCALE = [
    ("normal", 0),
    ("slightly_enhanced", 37),
    ("enhanced", 65),
    ("strongly_enhanced", 80),
    ("very_strongly_enhanced", 88),
]
LEVEL_LABELS_ES = {
    "normal": "Normales",
    "slightly_enhanced": "Ligeramente elevadas",
    "enhanced": "Elevadas",
    "strongly_enhanced": "Muy elevadas",
    "very_strongly_enhanced": "Excepcionalmente elevadas",
}

# Umbrales propios (no vienen de Tropocast), documentados acá para poder
# calibrarlos después con observaciones reales de la mesh:
CORREDOR_MIN_LEVEL = "enhanced"              # categoría 3: posible corredor
APERTURA_PROBABLE_MIN_LEVEL = "strongly_enhanced"  # categoría 4: apertura probable
APERTURA_PROBABLE_MIN_COVERAGE_PCT = 15.0


@dataclass(frozen=True)
class Location:
    name: str
    lat: float
    lon: float
    radius_km: float


# Tres Picos (vista general) + corredores de ejemplo del pedido original.
LOCATIONS = [
    Location("General (700km)", -38.1580, -61.9524, 700),
    Location("Bahía Blanca", -38.7196, -62.2724, 100),
    Location("Viedma / C. de Patagones", -40.8135, -62.9967, 100),
    Location("Tres Arroyos", -38.3739, -60.2767, 100),
    Location("Monte Hermoso", -38.9842, -61.2906, 80),
    Location("Mar del Plata", -37.9975, -57.5428, 100),
]


def level_at_score(score: int) -> str:
    level = LEVEL_SCALE[0][0]
    for code, minimum in LEVEL_SCALE:
        if score >= minimum:
            level = code
    return level


def level_label(level: str) -> str:
    return LEVEL_LABELS_ES.get(level, level)


def level_rank(level: str) -> int:
    for i, (code, _minimum) in enumerate(LEVEL_SCALE):
        if code == level:
            return i
    return 0


class TropocastService:
    def __init__(
        self,
        client: Optional[TropocastClient] = None,
        db_path: str = DB_PATH,
        locations: Optional[list] = None,
    ):
        self.client = client or TropocastClient()
        self.db_path = db_path
        self.locations = locations if locations is not None else LOCATIONS
        self.init_db()

    @property
    def enabled(self) -> bool:
        return self.client.enabled

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS tropocast_forecasts (
                  id              INTEGER PRIMARY KEY AUTOINCREMENT,
                  location_name   TEXT    NOT NULL,
                  lat             REAL    NOT NULL,
                  lon             REAL    NOT NULL,
                  radius_km       REAL    NOT NULL,
                  source          TEXT    NOT NULL,
                  model_run       TEXT    NOT NULL,
                  fetched_at      INTEGER NOT NULL,
                  data_status     TEXT    NOT NULL,
                  summary_level   TEXT,
                  summary_message TEXT,
                  raw_response    TEXT    NOT NULL,
                  UNIQUE(location_name, model_run)
                );
                CREATE INDEX IF NOT EXISTS idx_tropocast_loc_fetched
                    ON tropocast_forecasts(location_name, fetched_at DESC);
            """)

    # ------------------------------------------------------------------
    # Refresh -- llamado solo desde el scheduler (bbs_system.tick), nunca
    # desde una consulta de usuario.
    # ------------------------------------------------------------------

    def _latest_known_run(self, location_name: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT model_run FROM tropocast_forecasts WHERE location_name = ? "
                "ORDER BY fetched_at DESC LIMIT 1",
                (location_name,),
            ).fetchone()
        return row["model_run"] if row else None

    def _save(self, location: Location, response: dict) -> None:
        loc_info = response.get("location") or {}
        summary = response.get("summary") or {}
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO tropocast_forecasts
                   (location_name, lat, lon, radius_km, source, model_run, fetched_at,
                    data_status, summary_level, summary_message, raw_response)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    location.name, location.lat, location.lon, location.radius_km,
                    loc_info.get("source", "gdps"), loc_info.get("run", ""),
                    int(time.time()), response.get("data_status", "no_data"),
                    summary.get("level"), summary.get("message"),
                    json.dumps(response),
                ),
            )

    def refresh(self) -> dict:
        """Actualiza el pronóstico de cada ubicación configurada. Que una
        falle no frena a las demás. Devuelve un resumen para loguear, ej.
        {"Bahía Blanca": "ok", "Tres Arroyos": "sin_cambios", ...}."""
        result = {}
        for location in self.locations:
            try:
                result[location.name] = self._refresh_one(location)
            except Exception:
                logger.exception("Tropocast: error actualizando %s", location.name)
                result[location.name] = "error"
        return result

    def _refresh_one(self, location: Location) -> str:
        known_run = self._latest_known_run(location.name)

        # Chequeo barato (sin auth, ~1s) antes de gastar la consulta
        # completa (~6s). Si no está disponible para esta fuente/área,
        # seguimos igual sin él -- el dedup de abajo por UNIQUE sigue
        # protegiendo contra guardar un duplicado.
        try:
            meta = self.client.fetch_metadata(source="gdps", area="world")
            published_run = (meta.get("publication") or {}).get("published_run")
            if published_run and known_run and published_run == known_run:
                return "sin_cambios"
        except Exception:
            logger.debug("Tropocast: metadata check no disponible, se sigue sin él", exc_info=True)

        try:
            response = self.client.fetch_forecast(location.lat, location.lon, location.radius_km)
        except TropocastRateLimited as exc:
            logger.warning("Tropocast: rate limited (retry_after=%s) para %s", exc.retry_after, location.name)
            return "error"
        except TropocastUnauthorized:
            logger.error("Tropocast: TROPOCAST_API_KEY inválida o no configurada")
            return "error"
        except TropocastError as exc:
            logger.warning("Tropocast: error %s para %s", exc.status_code, location.name)
            return "error"

        new_run = (response.get("location") or {}).get("run", "")
        if new_run and new_run == known_run:
            return "sin_cambios"

        self._save(location, response)
        return "ok"

    def prune_old_data(self, days: int = RETENTION_DAYS) -> int:
        cutoff = int(time.time()) - days * 86400
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM tropocast_forecasts WHERE fetched_at < ?", (cutoff,))
            return cur.rowcount

    # ------------------------------------------------------------------
    # Consultas -- usadas por modules/propagacion.py. Solo leen lo que ya
    # está guardado, nunca disparan una llamada de red.
    # ------------------------------------------------------------------

    def _latest_for(self, location_name: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tropocast_forecasts WHERE location_name = ? "
                "ORDER BY fetched_at DESC LIMIT 1",
                (location_name,),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["raw"] = json.loads(d["raw_response"])
        return d

    def sector_list(self) -> list:
        return [loc.name for loc in self.locations]

    def all_latest_frames(self) -> list:
        """Última corrida guardada por ubicación, con sus frames (hora,
        score, cobertura) -- para el mapa de evolución de la webapp
        (ver /api/alerts/tropocast en api.py). Nunca dispara una consulta
        de red, solo lee lo que el scheduler ya guardó."""
        result = []
        for loc in self.locations:
            data = self._latest_for(loc.name)
            if data is None:
                continue
            frames = [
                {
                    "offset_hour": f.get("offset_hour"),
                    "valid_at": f.get("valid_at"),
                    "local_time": (f.get("local_time") or {}).get("text"),
                    "score": f.get("score"),
                    "coverage_percent": f.get("coverage_percent"),
                }
                for f in data["raw"].get("frames", [])
            ]
            result.append({
                "name": loc.name,
                "lat": loc.lat,
                "lon": loc.lon,
                "radius_km": loc.radius_km,
                "model_run": data["model_run"],
                "fetched_at": data["fetched_at"],
                "data_status": data["data_status"],
                "frames": frames,
            })
        return result

    def _unavailable_message(self, location_name: Optional[str]) -> str:
        if not self.enabled:
            return "📡 Pronóstico troposférico no disponible (sin configurar)."
        suffix = f" para {location_name}" if location_name else ""
        return f"📡 Todavía no hay pronóstico guardado{suffix}. Esperá al próximo ciclo de actualización."

    def general_summary(self, location_name: Optional[str] = None) -> str:
        location_name = location_name or self.locations[0].name
        data = self._latest_for(location_name)
        if data is None:
            return self._unavailable_message(location_name)
        summary = data["raw"].get("summary") or {}
        lines = [f"📡 {location_name}"]
        if summary.get("message"):
            lines.append(summary["message"])
        if summary.get("details"):
            lines.append(summary["details"])
        return "\n".join(lines)

    def sector_forecast(self, location_name: str) -> str:
        return self.general_summary(location_name)

    def favorable_windows(self, location_name: Optional[str] = None, min_level: str = CORREDOR_MIN_LEVEL) -> str:
        location_name = location_name or self.locations[0].name
        data = self._latest_for(location_name)
        if data is None:
            return self._unavailable_message(location_name)

        min_rank = level_rank(min_level)
        windows = []
        for frame in data["raw"].get("frames", []):
            level = level_at_score(frame.get("score", 0))
            if level_rank(level) >= min_rank:
                when = (frame.get("local_time") or {}).get("text") or frame.get("valid_at", "?")
                windows.append(f"{when}: {level_label(level)} ({frame.get('score')}/100)")

        if not windows:
            return f"⭐ {location_name}: sin ventanas de {level_label(min_level)}+ en las próximas 72h."
        header = f"⭐ Ventanas {level_label(min_level)}+ ({location_name})"
        return "\n".join([header] + windows[:6])

    def corridor_candidates(self) -> str:
        """Heurística de co-ocurrencia entre sectores vecinos configurados
        (no el sector 'General', que cubre todo el radio). No hay grilla
        espacial real expuesta por la API, así que esto NO es una ruta
        punto-a-punto verificada -- ver limitaciones en el plan."""
        sector_names = [loc.name for loc in self.locations[1:]]
        frames_by_location = {}
        for name in sector_names:
            data = self._latest_for(name)
            if data is None:
                continue
            frames_by_location[name] = {f["valid_at"]: f for f in data["raw"].get("frames", [])}

        min_rank = level_rank(CORREDOR_MIN_LEVEL)
        found = []
        names = list(frames_by_location.keys())
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a_frames = frames_by_location[names[i]]
                b_frames = frames_by_location[names[j]]
                for valid_at in sorted(set(a_frames) & set(b_frames)):
                    fa, fb = a_frames[valid_at], b_frames[valid_at]
                    if (level_rank(level_at_score(fa["score"])) >= min_rank
                            and level_rank(level_at_score(fb["score"])) >= min_rank):
                        when = (fa.get("local_time") or {}).get("text") or valid_at
                        found.append(f"{names[i]} <-> {names[j]} · {when} ({fa['score']}/{fb['score']})")
                        break  # un hallazgo por par alcanza

        if not found:
            return ("🛣 Sin corredores candidatos en las próximas 72h.\n"
                    "(Estimación por coincidencia entre sectores, no una ruta verificada.)")
        header = "🛣 Posibles corredores (no son enlaces garantizados):"
        return "\n".join([header] + found[:5])

    def freshness(self) -> str:
        if not self.enabled:
            return self._unavailable_message(None)
        now = int(time.time())
        lines = ["🕒 Vigencia por sector"]
        any_data = False
        for loc in self.locations:
            data = self._latest_for(loc.name)
            if data is None:
                lines.append(f"{loc.name}: sin datos")
                continue
            any_data = True
            age_min = round((now - data["fetched_at"]) / 60)
            age_label = f"hace {age_min} min" if age_min < 60 else f"hace {age_min // 60}h"
            lines.append(f"{loc.name}: {age_label} · {data['data_status']}")
        if not any_data:
            return self._unavailable_message(None)
        return "\n".join(lines)
