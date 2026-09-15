import json
import logging
import os
import unicodedata
from typing import Optional

logger = logging.getLogger(__name__)

PARTIDOS_GEOJSON_PATH = "partidos.geojson"

TARGET_PARTIDOS = {
    "adolfo alsina": "Adolfo Alsina",
    "saavedra": "Saavedra",
    "puan": "Puan",
    "tornquist": "Tornquist",
    "coronel de marina leonardo rosales": "Coronel Rosales",
    "coronel dorrego": "Coronel Dorrego",
    "bahia blanca": "Bahia Blanca",
    "villarino": "Villarino",
    "patagones": "Patagones",
    "guamini": "Guamini",
    "coronel suarez": "Coronel Suarez",
    "coronel pringles": "Coronel Pringles",
    "monte hermoso": "Monte Hermoso",
}


def normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFD", value or "")
    return "".join(c for c in value if unicodedata.category(c) != "Mn").lower().strip()


def _point_in_ring(lon: float, lat: float, ring: list) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        intersects = ((yi > lat) != (yj > lat)) and (
            lon < ((xj - xi) * (lat - yi) / ((yj - yi) or 1e-15) + xi)
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def _point_in_polygon(lon: float, lat: float, polygon: list) -> bool:
    if not polygon or not _point_in_ring(lon, lat, polygon[0]):
        return False
    return not any(_point_in_ring(lon, lat, hole) for hole in polygon[1:])


def _contains(geometry: dict, lon: float, lat: float) -> bool:
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if kind == "Polygon":
        return _point_in_polygon(lon, lat, coords)
    if kind == "MultiPolygon":
        return any(_point_in_polygon(lon, lat, polygon) for polygon in coords)
    return False


class FirmsGeoFilter:
    def __init__(self, geojson_path: str = PARTIDOS_GEOJSON_PATH):
        self.geojson_path = geojson_path
        self._partidos: list[tuple[str, dict]] = []
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.geojson_path):
            raise FileNotFoundError(f"FIRMS GeoJSON not found: {self.geojson_path}")
        with open(self.geojson_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        result: list[tuple[str, dict]] = []
        for feature in data.get("features", []):
            props = feature.get("properties") or {}
            province = props.get("provincia") or {}
            if normalize_name(province.get("nombre", "")) != "buenos aires":
                continue
            raw_name = props.get("nombre", "")
            normalized = normalize_name(raw_name)
            display = TARGET_PARTIDOS.get(normalized)
            if display:
                result.append((display, feature.get("geometry") or {}))

        if len(result) != len(TARGET_PARTIDOS):
            loaded = sorted(name for name, _ in result)
            logger.warning("FIRMS: loaded %d/%d target partido polygons: %s", len(result), len(TARGET_PARTIDOS), loaded)
        self._partidos = result

    def locate_point(self, latitude: float, longitude: float) -> Optional[str]:
        for display_name, geometry in self._partidos:
            if _contains(geometry, longitude, latitude):
                return display_name
        return None

    @property
    def loaded_count(self) -> int:
        return len(self._partidos)
