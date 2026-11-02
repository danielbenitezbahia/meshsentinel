import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { FirmsEvent } from "../types";
import { useMapTheme } from "../useMapTheme";
import MapThemeToggle from "./MapThemeToggle";

interface Props {
  events: FirmsEvent[];
}

const DEFAULT_CENTER: [number, number] = [-38.72, -62.27];

function levelColor(level: number): string {
  return level >= 3 ? "#e53935" : level === 2 ? "#fb8c00" : "#fdd835";
}

export default function FireMap({ events }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
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

    for (const ev of events) {
      const color = levelColor(ev.current_level);
      const lines = [
        `<b>Nivel ${ev.current_level}</b>${ev.has_high_confidence ? " · alta confianza" : ""}`,
        ev.partidos.length ? ev.partidos.join(", ") : null,
        `Satélites: ${ev.satellites.join(", ") || "—"}`,
        `FRP máx: ${ev.max_frp.toFixed(1)}`,
        `Últ. detección: ${new Date(ev.last_seen).toLocaleString("es-AR")}`,
      ].filter(Boolean);

      L.circleMarker([ev.latest_latitude, ev.latest_longitude], {
        radius: ev.has_high_confidence ? 9 : 6,
        fillColor: color,
        color: "#fff",
        weight: 1.5,
        fillOpacity: 0.85,
      })
        .bindTooltip(lines.join("<br>"), { direction: "top" })
        .addTo(layer);
    }

    if (events.length > 0) {
      const pts: [number, number][] = events.map((e) => [e.latest_latitude, e.latest_longitude]);
      map.fitBounds(L.latLngBounds(pts), { padding: [30, 30], maxZoom: 11 });
    }
  }, [events]);

  return (
    <div
      className={theme === "dark" ? "map-root-dark" : undefined}
      style={{ width: "100%", height: "100%", position: "relative" }}
    >
      <div ref={containerRef} style={{ width: "100%", height: "100%" }} />
      <MapThemeToggle />
      {events.length === 0 && (
        <div style={{
          position: "absolute", top: "50%", left: "50%",
          transform: "translate(-50%, -50%)",
          color: "#546e7a", fontSize: 13, textAlign: "center", pointerEvents: "none",
        }}>
          Sin focos activos
        </div>
      )}
    </div>
  );
}
