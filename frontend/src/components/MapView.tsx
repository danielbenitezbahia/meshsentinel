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

const PALETTE = [
  "#7b1fa2", "#c62828", "#e65100", "#2e7d32",
  "#1565c0", "#00695c", "#4527a0", "#880e4f",
  "#4e342e", "#01579b", "#558b2f", "#f57f17",
  "#37474f", "#6a1520", "#006064", "#827717",
];

function hashNodeColor(nodeId: string): string {
  let h = 0;
  for (let i = 0; i < nodeId.length; i++) {
    h = (h * 31 + nodeId.charCodeAt(i)) >>> 0;
  }
  return PALETTE[h % PALETTE.length];
}

function nodeOpacity(node: MeshNode): number {
  if (node.last_seen_mins_ago == null) return 0.4;
  if (node.last_seen_mins_ago < 30) return 1.0;
  if (node.last_seen_mins_ago < 120) return 0.72;
  return 0.45;
}

function makeLabelIcon(node: MeshNode, selected: boolean): L.DivIcon {
  const label = node.short_name || node.node_id.slice(-4);
  const bg = hashNodeColor(node.node_id);
  const opacity = nodeOpacity(node);
  const border = selected ? "2px solid #ffffff" : "1.5px solid rgba(255,255,255,0.28)";
  const shadow = selected
    ? "0 0 0 2.5px #e91e63, 0 3px 12px rgba(0,0,0,0.75)"
    : "0 2px 7px rgba(0,0,0,0.55)";

  const html = `<div style="
    display:inline-flex;flex-direction:column;align-items:center;
    transform:translate(-50%,-100%);opacity:${opacity};cursor:pointer;
  "><div style="
    background:${bg};color:#fff;padding:3px 9px;border-radius:6px;
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
    font-size:12px;font-weight:700;white-space:nowrap;letter-spacing:0.3px;
    border:${border};box-shadow:${shadow};line-height:1.4;
  ">${label}</div><div style="
    width:7px;height:7px;background:#fff;border:2px solid ${bg};
    border-radius:50%;margin-top:1px;flex-shrink:0;
  "></div></div>`;

  return L.divIcon({ className: "", html, iconSize: [0, 0], iconAnchor: [0, 0] });
}

export default function MapView({ nodes, edges, selectedId, onSelectNode }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const markersRef = useRef<Map<string, L.Marker>>(new Map());

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

    for (const node of nodesWithPos) {
      const label = node.short_name || node.node_id;
      const lastSeen = node.last_seen_mins_ago != null
        ? node.last_seen_mins_ago < 2 ? "hace instantes" : `hace ${node.last_seen_mins_ago} min`
        : "desconocido";

      const marker = L.marker([node.lat!, node.lon!], {
        icon: makeLabelIcon(node, false),
      });

      marker.bindTooltip(
        `<b>${label}</b>${node.long_name ? `<br><span style="color:#90a4ae">${node.long_name}</span>` : ""}<br>` +
        `${node.hops_from_bbs != null ? node.hops_from_bbs + " hops" : ""}` +
        `${node.snr_from_bbs != null ? " · " + node.snr_from_bbs + " dB" : ""}<br>` +
        `Visto: ${lastSeen}`,
        { direction: "top", offset: [0, -40] }
      );

      marker.on("click", () => onSelectNode(node.node_id));
      marker.addTo(map);
      markersRef.current.set(node.node_id, marker);
    }
  }, [nodes, edges]);

  useEffect(() => {
    const nodeMap = new Map(nodes.map((n) => [n.node_id, n]));
    markersRef.current.forEach((marker, id) => {
      const node = nodeMap.get(id);
      if (!node) return;
      marker.setIcon(makeLabelIcon(node, id === selectedId));
    });

    if (selectedId && mapRef.current) {
      const node = nodeMap.get(selectedId);
      if (node?.lat != null && node?.lon != null) {
        mapRef.current.flyTo([node.lat, node.lon], mapRef.current.getZoom(), { duration: 0.6 });
      }
    }
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
    </div>
  );
}
