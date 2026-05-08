import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { TrackPoint } from "../types";

export interface TrackData {
  nodeId: string;
  color: string;
  points: TrackPoint[];
}

interface Props {
  tracks: TrackData[];
}

const DEFAULT_CENTER: [number, number] = [-38.72, -62.27];

export default function TrackMap({ tracks }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layersRef = useRef<Map<string, L.LayerGroup>>(new Map());

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    mapRef.current = L.map(containerRef.current, { center: DEFAULT_CENTER, zoom: 11 });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      maxZoom: 19,
    }).addTo(mapRef.current);
    return () => {
      mapRef.current?.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const activeIds = new Set(tracks.map((t) => t.nodeId));

    // Remove layers for deselected nodes
    layersRef.current.forEach((layer, id) => {
      if (!activeIds.has(id)) {
        layer.remove();
        layersRef.current.delete(id);
      }
    });

    // Add layers for newly selected nodes
    let newTrackAdded = false;
    for (const track of tracks) {
      if (layersRef.current.has(track.nodeId)) continue;
      if (track.points.length === 0) continue;

      const group = L.layerGroup().addTo(map);
      const latlngs: [number, number][] = track.points.map((p) => [p.lat, p.lon]);

      L.polyline(latlngs, { color: track.color, weight: 3, opacity: 0.85 }).addTo(group);

      for (const p of track.points) {
        const t = new Date(p.ts * 1000).toLocaleTimeString("es-AR", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        });
        const parts: string[] = [`<b>${t}</b>`];
        if (p.altitude != null) parts.push(`Alt: ${p.altitude} m`);
        if (p.speed != null) parts.push(`Vel: ${(p.speed * 3.6).toFixed(1)} km/h`);
        if (p.heading != null) parts.push(`Rum: ${p.heading}°`);

        L.circleMarker([p.lat, p.lon], {
          radius: 5,
          fillColor: track.color,
          color: "#fff",
          weight: 1,
          fillOpacity: 0.9,
        })
          .bindTooltip(parts.join("<br>"), { direction: "top" })
          .addTo(group);
      }

      // Start marker (larger dot)
      const first = track.points[0];
      L.circleMarker([first.lat, first.lon], {
        radius: 8,
        fillColor: track.color,
        color: "#fff",
        weight: 2,
        fillOpacity: 1,
      })
        .bindTooltip("Inicio", { direction: "top" })
        .addTo(group);

      layersRef.current.set(track.nodeId, group);
      newTrackAdded = true;
    }

    // Fit bounds when a new track is added
    if (newTrackAdded) {
      const allPoints: [number, number][] = tracks
        .filter((t) => t.points.length > 0)
        .flatMap((t) => t.points.map((p) => [p.lat, p.lon] as [number, number]));
      if (allPoints.length > 0) {
        map.fitBounds(L.latLngBounds(allPoints), { padding: [40, 40] });
      }
    }
  }, [tracks]);

  return (
    <div style={{ width: "100%", height: "100%", position: "relative" }}>
      <div ref={containerRef} style={{ width: "100%", height: "100%" }} />
      {tracks.length === 0 && (
        <div
          style={{
            position: "absolute",
            top: "50%",
            left: "50%",
            transform: "translate(-50%, -50%)",
            color: "#546e7a",
            fontSize: 14,
            textAlign: "center",
            pointerEvents: "none",
          }}
        >
          Seleccioná un nodo del panel izquierdo
          <br />
          para ver su trayectoria
        </div>
      )}
    </div>
  );
}
