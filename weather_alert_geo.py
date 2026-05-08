from typing import List, Tuple, Optional


Point = Tuple[float, float]  # (lat, lon)


def parse_polygon(polygon_str: str) -> List[Point]:
    """
    Convierte un string tipo:
        "-38.1,-62.2 -38.3,-62.0 -38.4,-62.4"
    en:
        [(-38.1, -62.2), (-38.3, -62.0), (-38.4, -62.4)]

    Ignora puntos inválidos.
    """
    if not polygon_str:
        return []

    points: List[Point] = []

    for raw_point in polygon_str.strip().split():
        if "," not in raw_point:
            continue

        parts = raw_point.split(",")
        if len(parts) != 2:
            continue

        lat_str, lon_str = parts

        try:
            lat = float(lat_str.strip())
            lon = float(lon_str.strip())
        except ValueError:
            continue

        points.append((lat, lon))

    return points


def is_valid_polygon(points: List[Point]) -> bool:
    """
    Un polígono útil necesita al menos 3 puntos.
    """
    return len(points) >= 3


def point_in_polygon(lat: float, lon: float, polygon: List[Point]) -> bool:
    """
    Algoritmo ray casting.
    Devuelve True si el punto (lat, lon) está dentro del polígono.

    OJO:
    Para geometría plana simple funciona bien.
    Para este caso de alertas geográficas del SMN es suficiente.
    """
    if not is_valid_polygon(polygon):
        return False

    inside = False
    n = len(polygon)

    # Usamos lon como eje X y lat como eje Y
    x = lon
    y = lat

    j = n - 1
    for i in range(n):
        yi, xi = polygon[i]      # (lat, lon)
        yj, xj = polygon[j]

        intersects = ((yi > y) != (yj > y))
        if intersects:
            try:
                x_intersection = (xj - xi) * (y - yi) / (yj - yi) + xi
            except ZeroDivisionError:
                x_intersection = xi

            if x < x_intersection:
                inside = not inside

        j = i

    return inside


def point_in_polygon_from_string(lat: float, lon: float, polygon_str: str) -> bool:
    """
    Helper directo:
    recibe polygon como string del SMN y evalúa el punto.
    """
    polygon = parse_polygon(polygon_str)
    return point_in_polygon(lat, lon, polygon)


def polygon_bounds(polygon: List[Point]) -> Optional[Tuple[float, float, float, float]]:
    """
    Devuelve bounding box como:
        (min_lat, max_lat, min_lon, max_lon)
    o None si el polígono es inválido.
    """
    if not is_valid_polygon(polygon):
        return None

    lats = [p[0] for p in polygon]
    lons = [p[1] for p in polygon]

    return (min(lats), max(lats), min(lons), max(lons))


def point_in_bounds(lat: float, lon: float, bounds: Tuple[float, float, float, float]) -> bool:
    """
    Chequeo rápido contra bounding box.
    """
    min_lat, max_lat, min_lon, max_lon = bounds
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def point_in_polygon_optimized(lat: float, lon: float, polygon: List[Point]) -> bool:
    """
    Primero chequea bounding box, después ray casting.
    Más eficiente para usar muchas veces.
    """
    bounds = polygon_bounds(polygon)
    if bounds is None:
        return False

    if not point_in_bounds(lat, lon, bounds):
        return False

    return point_in_polygon(lat, lon, polygon)


if __name__ == "__main__":
    # prueba simple con un cuadrado
    square = parse_polygon("-1,-1 -1,1 1,1 1,-1")

    print("[TEST] polygon válido:", is_valid_polygon(square))
    print("[TEST] punto (0,0) dentro:", point_in_polygon(0, 0, square))
    print("[TEST] punto (2,2) dentro:", point_in_polygon(2, 2, square))