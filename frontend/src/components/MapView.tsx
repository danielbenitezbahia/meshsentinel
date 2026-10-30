import { useEffect, useRef, useState, useCallback } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { MeshNode, MeshEdge } from "../types";
import { hashNodeColor, isRecentEnoughForMap, isHiddenFromMap, computeSuspectNodeIds, snrToColor } from "../mapVisuals";

interface Props {
  nodes: MeshNode[];
  edges: MeshEdge[];
  selectedId: string | null;
  onSelectNode: (id: string) => void;
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
  const linesRef = useRef<L.Polyline[]>([]);

  // ── aislar conexiones (igual que en el mapa 3D) ───────────────────────────
  // Con esto activo, las líneas arrancan apagadas; tocar un nodo prende las
  // suyas y se van acumulando -- cada nodo tocado queda prendido hasta que
  // se vuelve a apretar el toggle, que resetea todo.
  const [isolateMode, setIsolateMode] = useState(false);
  const [pinnedNodes, setPinnedNodes] = useState<Set<string>>(new Set());

  const toggleIsolateMode = useCallback(() => {
    setIsolateMode((prev) => !prev);
    setPinnedNodes(new Set());
  }, []);

  useEffect(() => {
    if (!containerRef.current) return;

    const suspectIds = computeSuspectNodeIds(nodes, edges);
    const nodesWithPos = nodes.filter((n) =>
      n.lat != null && n.lon != null && isRecentEnoughForMap(n.last_seen_mins_ago) &&
      !isHiddenFromMap(n.short_name, n.long_name) && !suspectIds.has(n.node_id)
    );
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

    linesRef.current.forEach((l) => l.remove());
    linesRef.current = [];

    const nodeById = new Map(nodesWithPos.map((n) => [n.node_id, n]));
    const seenEdges = new Set<string>();
    for (const e of edges) {
      const a = nodeById.get(e.reporter);
      const b = nodeById.get(e.neighbor);
      if (!a || !b || a.lat == null || a.lon == null || b.lat == null || b.lon == null) continue;

      const key = [e.reporter, e.neighbor].sort().join("||");
      if (seenEdges.has(key)) continue;
      seenEdges.add(key);

      const aPinned = pinnedNodes.has(e.reporter);
      const bPinned = pinnedNodes.has(e.neighbor);
      if (isolateMode && !aPinned && !bPinned) continue;

      const snr = e.snr ?? e.snr_reverse;
      const aName = a.short_name || a.node_id;
      const bName = b.short_name || b.node_id;

      const line = L.polyline([[a.lat, a.lon], [b.lat, b.lon]], {
        color: snrToColor(snr),
        weight: 2,
        opacity: 0.65,
      });
      line.bindTooltip(
        `<b>${aName} ↔ ${bName}</b>` + (snr != null ? `<br>${snr} dB` : ""),
        { direction: "center", sticky: true, className: "mesh-tooltip" }
      );
      line.addTo(map);
      linesRef.current.push(line);
    }

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

      marker.on("click", () => {
        onSelectNode(node.node_id);
        if (isolateMode) {
          setPinnedNodes((prev) => (prev.has(node.node_id) ? prev : new Set(prev).add(node.node_id)));
        }
      });
      marker.addTo(map);
      markersRef.current.set(node.node_id, marker);
    }
  }, [nodes, edges, isolateMode, pinnedNodes]);

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

  const statusSuspectIds = computeSuspectNodeIds(nodes, edges);
  const withPos = nodes.filter((n) =>
    n.lat != null && isRecentEnoughForMap(n.last_seen_mins_ago) &&
    !isHiddenFromMap(n.short_name, n.long_name) && !statusSuspectIds.has(n.node_id)
  ).length;
  const withoutPos = nodes.length - withPos;

  return (
    <div style={{ width: "100%", height: "100%", position: "relative" }}>
      <div ref={containerRef} style={{ width: "100%", height: "100%" }} />
      <div style={{ position: "absolute", top: 10, left: 60, zIndex: 1000 }}>
        <button
          className={`isolate-toggle-btn ${isolateMode ? "active" : ""}`}
          onClick={toggleIsolateMode}
          title={isolateMode ? "Tocá nodos para prender sus conexiones" : "Mostrando todas las conexiones"}
        >
          {isolateMode ? "Elegir nodos" : "Conexiones: todas"}
        </button>
      </div>
      <div style={{
        position: "absolute", bottom: 30, left: 10, zIndex: 1000,
        background: "rgba(13,33,55,0.85)", color: "#607d8b",
        padding: "4px 8px", borderRadius: 4, fontSize: 11,
        border: "1px solid #1e3a5f",
      }}>
        {withPos} en el mapa · {withoutPos} sin posición o antiguos
      </div>
    </div>
  );
}
