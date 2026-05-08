import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { MeshNode, MeshEdge } from "../types";

interface Props {
  nodes: MeshNode[];
  edges: MeshEdge[];
  selectedId: string | null;
  onSelectNode: (id: string) => void;
}

function snrColor(snr: number | null | undefined): string {
  if (snr == null) return "#546e7a";
  if (snr >= 8) return "#00e676";
  if (snr >= 4) return "#ffeb3b";
  if (snr >= 0) return "#ff9800";
  return "#f44336";
}

function edgeWeight(snr: number | null | undefined): number {
  if (snr == null) return 1.5;
  if (snr >= 8) return 3.5;
  if (snr >= 4) return 2.5;
  return 1.5;
}

function nodeColor(node: MeshNode): string {
  if (node.last_seen_mins_ago == null) return "#37474f";
  if (node.last_seen_mins_ago < 30) return "#1976d2";
  if (node.last_seen_mins_ago < 120) return "#0d47a1";
  return "#263238";
}

function nodeBorder(node: MeshNode): string {
  if (node.last_seen_mins_ago == null) return "#546e7a";
  if (node.last_seen_mins_ago < 30) return "#90caf9";
  if (node.last_seen_mins_ago < 120) return "#42a5f5";
  return "#455a64";
}

export default function MapView({ nodes, edges, selectedId, onSelectNode }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const markersRef = useRef<Map<string, L.CircleMarker>>(new Map());
  // all edge lines, keyed by "reporterId||neighborId" (sorted)
  const edgeLinesRef = useRef<Map<string, L.Polyline>>(new Map());

  useEffect(() => {
    if (!containerRef.current) return;

    const nodesWithPos = nodes.filter((n) => n.lat != null && n.lon != null);
    if (nodesWithPos.length === 0) return;

    if (!mapRef.current) {
      const center = nodesWithPos.reduce(
        (acc, n) => [acc[0] + n.lat! / nodesWithPos.length, acc[1] + n.lon! / nodesWithPos.length],
        [0, 0]
      ) as [number, number];

      mapRef.current = L.map(containerRef.current, { center, zoom: 11 });

      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
        maxZoom: 19,
      }).addTo(mapRef.current);
    }

    const map = mapRef.current;

    markersRef.current.forEach((m) => m.remove());
    markersRef.current.clear();
    edgeLinesRef.current.forEach((l) => l.remove());
    edgeLinesRef.current.clear();

    // nodeMap includes ALL nodes (even without GPS) so we can look up names
    const nodeMap = new Map(nodes.map((n) => [n.node_id, n]));
    // posMap: only nodes with GPS for drawing lines
    const posMap = new Map(nodesWithPos.map((n) => [n.node_id, n]));

    // Draw all edges between nodes that both have GPS
    const seenEdges = new Set<string>();
    for (const edge of edges) {
      const a = posMap.get(edge.reporter);
      const b = posMap.get(edge.neighbor);
      if (!a || !b) continue;
      const key = [edge.reporter, edge.neighbor].sort().join("||");
      if (seenEdges.has(key)) continue;
      seenEdges.add(key);

      const snr = edge.snr ?? edge.snr_reverse;
      const nameA = nodeMap.get(edge.reporter)?.short_name || edge.reporter;
      const nameB = nodeMap.get(edge.neighbor)?.short_name || edge.neighbor;

      const line = L.polyline([[a.lat!, a.lon!], [b.lat!, b.lon!]], {
        color: snrColor(snr),
        weight: edgeWeight(snr),
        opacity: 0.7,
      });
      line.bindTooltip(
        `${nameA} ↔ ${nameB}${snr != null ? `<br>SNR: ${snr} dB` : ""}`,
        { direction: "center", sticky: true }
      );
      line.addTo(map);
      edgeLinesRef.current.set(key, line);
    }

    // Draw nodes
    for (const node of nodesWithPos) {
      const label = node.short_name || node.node_id;
      const lastSeen = node.last_seen_mins_ago != null
        ? node.last_seen_mins_ago < 2 ? "hace instantes" : `hace ${node.last_seen_mins_ago} min`
        : "desconocido";

      const marker = L.circleMarker([node.lat!, node.lon!], {
        radius: 7,
        fillColor: nodeColor(node),
        color: nodeBorder(node),
        weight: 2,
        fillOpacity: 0.9,
      });

      marker.bindTooltip(
        `<b>${label}</b>${node.long_name ? `<br><span style="color:#90a4ae">${node.long_name}</span>` : ""}<br>` +
        `${node.hops_from_bbs != null ? node.hops_from_bbs + " hops" : ""}` +
        `${node.snr_from_bbs != null ? " · " + node.snr_from_bbs + " dB" : ""}<br>` +
        `Visto: ${lastSeen}`,
        { direction: "top", offset: [0, -8] }
      );

      marker.on("click", () => onSelectNode(node.node_id));
      marker.addTo(map);
      markersRef.current.set(node.node_id, marker);

      // Label permanente
      const icon = L.divIcon({
        className: "",
        html: `<div style="color:#cfd8dc;font-size:9px;font-family:monospace;
          text-shadow:0 0 3px #000,0 0 3px #000;
          white-space:nowrap;pointer-events:none;
          margin-top:10px;margin-left:-20px;">${label}</div>`,
        iconSize: [40, 12],
        iconAnchor: [20, 0],
      });
      L.marker([node.lat!, node.lon!], { icon, interactive: false, zIndexOffset: -100 }).addTo(map);
    }
  }, [nodes, edges]);

  // Al seleccionar un nodo: resaltar marcador y destacar sus arcos
  useEffect(() => {
    const nodeMap = new Map(nodes.map((n) => [n.node_id, n]));

    markersRef.current.forEach((marker, id) => {
      const node = nodeMap.get(id);
      if (!node) return;
      if (id === selectedId) {
        marker.setStyle({ radius: 11, color: "#f48fb1", fillColor: "#e91e63", weight: 3 } as any);
        if (node.lat != null) mapRef.current?.panTo([node.lat!, node.lon!]);
      } else {
        marker.setStyle({ radius: 7, fillColor: nodeColor(node), color: nodeBorder(node), weight: 2 } as any);
      }
    });

    // Destacar arcos conectados al nodo seleccionado, opacar el resto
    edgeLinesRef.current.forEach((line, key) => {
      const [a, b] = key.split("||");
      const isConnected = selectedId && (a === selectedId || b === selectedId);
      if (!selectedId) {
        line.setStyle({ opacity: 0.7, weight: edgeWeight(undefined) });
      } else if (isConnected) {
        // Buscar SNR del edge para restaurar color correcto
        line.setStyle({ opacity: 1, weight: 4 });
        line.bringToFront();
      } else {
        line.setStyle({ opacity: 0.15 });
      }
    });
  }, [selectedId, nodes]);

  useEffect(() => {
    return () => { mapRef.current?.remove(); mapRef.current = null; };
  }, []);

  const withPos = nodes.filter((n) => n.lat != null).length;
  const withoutPos = nodes.length - withPos;

  return (
    <div style={{ width: "100%", height: "100%", position: "relative" }}>
      <div ref={containerRef} style={{ width: "100%", height: "100%" }} />

      <div style={{
        position: "absolute", bottom: 30, left: 10, zIndex: 1000,
        background: "rgba(13,33,55,0.85)", color: "#607d8b",
        padding: "4px 8px", borderRadius: 4, fontSize: 11,
        border: "1px solid #1e3a5f",
      }}>
        {withPos} con GPS · {withoutPos} sin posición
      </div>

      <div style={{
        position: "absolute", top: 10, right: 10, zIndex: 1000,
        background: "rgba(13,33,55,0.9)", color: "#607d8b",
        padding: "6px 10px", borderRadius: 4, fontSize: 10,
        border: "1px solid #1e3a5f", lineHeight: "1.8",
      }}>
        <div style={{ color: "#546e7a", fontWeight: 600, marginBottom: 2 }}>SNR link</div>
        {[["#00e676", "≥ 8 dB"], ["#ffeb3b", "4–8 dB"], ["#ff9800", "0–4 dB"], ["#f44336", "< 0 dB"]].map(([c, l]) => (
          <div key={l} style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <div style={{ width: 16, height: 3, background: c, borderRadius: 2 }} />
            <span>{l}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
