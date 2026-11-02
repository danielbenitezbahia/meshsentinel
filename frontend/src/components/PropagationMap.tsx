import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { TropocastLocation } from "../types";
import { useMapTheme } from "../useMapTheme";
import MapThemeToggle from "./MapThemeToggle";

interface Props {
  locations: TropocastLocation[];
  offsetHour: number | null;
}

const DEFAULT_CENTER: [number, number] = [-38.72, -62.27];

// Escala real de Tropocast (radio.scale de la respuesta de la API), misma
// que usa tropocast_service.py del lado del BBS -- no inventada acá.
const LEVEL_SCALE: [string, number][] = [
  ["normal", 0],
  ["slightly_enhanced", 37],
  ["enhanced", 65],
  ["strongly_enhanced", 80],
  ["very_strongly_enhanced", 88],
];

// Paleta secuencial YlOrRd (ColorBrewer), un solo recorrido de tono cada
// vez más intenso según el score -- no es un semáforo de "peligro", folder
// más alto = mejor condición de propagación.
const LEVEL_COLOR: Record<string, string> = {
  normal: "#ffeda0",
  slightly_enhanced: "#feb24c",
  enhanced: "#fd8d3c",
  strongly_enhanced: "#f03b20",
  very_strongly_enhanced: "#bd0026",
};

const LEVEL_LABEL: Record<string, string> = {
  normal: "Normales",
  slightly_enhanced: "Ligeramente elevadas",
  enhanced: "Elevadas",
  strongly_enhanced: "Muy elevadas",
  very_strongly_enhanced: "Excepcionalmente elevadas",
};

function levelAtScore(score: number): string {
  let level = LEVEL_SCALE[0][0];
  for (const [code, min] of LEVEL_SCALE) if (score >= min) level = code;
  return level;
}

export default function PropagationMap({ locations, offsetHour }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
  const fittedRef = useRef(false);
  const [theme] = useMapTheme();

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    mapRef.current = L.map(containerRef.current, { center: DEFAULT_CENTER, zoom: 8 });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      maxZoom: 19,
    }).addTo(mapRef.current);
    layerRef.current = L.layerGroup().addTo(mapRef.current);
    return () => {
      mapRef.current?.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const layer = layerRef.current;
    if (!map || !layer) return;

    layer.clearLayers();
    if (locations.length === 0) return;

    // La ubicación de radio más grande es la vista "general" (700km,
    // centrada en Tres Picos): se dibuja solo como contorno de referencia,
    // sin relleno, para no tapar los sectores más chicos que sí importan
    // para ver la evolución local.
    const general = locations.reduce((a, b) => (a.radius_km > b.radius_km ? a : b));
    const sectors = locations.filter((l) => l.name !== general.name);

    L.circle([general.lat, general.lon], {
      radius: general.radius_km * 1000,
      color: "#546e7a",
      weight: 1,
      dashArray: "4 6",
      fill: false,
    }).addTo(layer);

    for (const loc of sectors) {
      const frame = offsetHour == null
        ? loc.frames[0]
        : loc.frames.find((f) => f.offset_hour === offsetHour);
      if (!frame) continue;

      const level = levelAtScore(frame.score);
      const color = LEVEL_COLOR[level];
      const lines = [
        `<b>${loc.name}</b>`,
        `${LEVEL_LABEL[level]} (${frame.score}/100)`,
        `Cobertura: ${frame.coverage_percent.toFixed(1)}%`,
        frame.local_time ? frame.local_time : frame.valid_at,
      ];

      L.circle([loc.lat, loc.lon], {
        radius: loc.radius_km * 1000,
        color: "#fff",
        weight: 1.5,
        fillColor: color,
        fillOpacity: 0.55,
      })
        .bindTooltip(lines.join("<br>"), { direction: "top" })
        .addTo(layer);
    }

    if (!fittedRef.current && sectors.length > 0) {
      fittedRef.current = true;
      const bounds = L.latLngBounds(sectors.map((l) => [l.lat, l.lon] as [number, number]));
      map.fitBounds(bounds, { padding: [40, 40] });
    }
  }, [locations, offsetHour]);

  return (
    <div
      className={theme === "dark" ? "map-root-dark" : undefined}
      style={{ width: "100%", height: "100%", position: "relative" }}
    >
      <div ref={containerRef} style={{ width: "100%", height: "100%" }} />
      <MapThemeToggle />
      {locations.length === 0 && (
        <div style={{
          position: "absolute", top: "50%", left: "50%",
          transform: "translate(-50%, -50%)",
          color: "#546e7a", fontSize: 13, textAlign: "center", pointerEvents: "none",
        }}>
          Sin pronóstico guardado todavía
        </div>
      )}
      {locations.length > 0 && (
        <div className="propagation-legend">
          <div className="propagation-legend-title">Potencial de propagación</div>
          <div className="propagation-legend-bar" />
          <div className="propagation-legend-labels">
            <span>Mín</span>
            <span>Máx</span>
          </div>
        </div>
      )}
    </div>
  );
}
