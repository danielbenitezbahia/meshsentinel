import { useState, useEffect, useRef } from "react";
import { fetchTrackNodes, fetchTrackPoints, fetchTrackPath } from "../api";
import type { TrackNode, TrackPoint, TraceHop } from "../types";
import TrackMap, { type TrackData } from "./TrackMap";

const COLORS = [
  "#ef5350", "#42a5f5", "#66bb6a", "#ffa726",
  "#ab47bc", "#26c6da", "#ffee58", "#ec407a",
  "#8d6e63", "#78909c",
];

function todayStr(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function TrackView() {
  const [date, setDate] = useState(todayStr());
  const [trackNodes, setTrackNodes] = useState<TrackNode[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [pointsCache, setPointsCache] = useState<Map<string, TrackPoint[]>>(new Map());
  const colorMapRef = useRef<Map<string, string>>(new Map());
  const colorCounterRef = useRef(0);
  const [colorMap, setColorMap] = useState<Map<string, string>>(new Map());
  const loadingNodesRef = useRef<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [tracePath, setTracePath] = useState<TraceHop[] | null>(null);
  const [tracePathTs, setTracePathTs] = useState<number | null>(null);

  useEffect(() => {
    setSelected(new Set());
    setPointsCache(new Map());
    colorMapRef.current = new Map();
    colorCounterRef.current = 0;
    loadingNodesRef.current = new Set();
    setColorMap(new Map());
    setLoading(true);
    fetchTrackNodes(date)
      .then((d) => setTrackNodes(d.nodes))
      .catch(() => setTrackNodes([]))
      .finally(() => setLoading(false));
  }, [date]);

  useEffect(() => {
    const nodeId = [...selected][0];
    if (!nodeId) { setTracePath(null); setTracePathTs(null); return; }
    fetchTrackPath(nodeId)
      .then((d) => { setTracePath(d.path); setTracePathTs(d.ts); })
      .catch(() => { setTracePath(null); setTracePathTs(null); });
  }, [selected]);

  const toggleNode = async (nodeId: string) => {
    if (selected.has(nodeId)) {
      setSelected(new Set());
      return;
    }

    // Deselect all others before selecting the new one
    setSelected(new Set());

    if (loadingNodesRef.current.has(nodeId)) return;

    // Assign color
    if (!colorMapRef.current.has(nodeId)) {
      const color = COLORS[colorCounterRef.current % COLORS.length];
      colorCounterRef.current++;
      colorMapRef.current.set(nodeId, color);
      setColorMap(new Map(colorMapRef.current));
    }

    // Fetch points if not cached
    if (!pointsCache.has(nodeId)) {
      loadingNodesRef.current.add(nodeId);
      try {
        const data = await fetchTrackPoints(date, nodeId);
        setPointsCache((prev) => new Map(prev).set(nodeId, data.points));
      } catch {
        loadingNodesRef.current.delete(nodeId);
        return;
      }
      loadingNodesRef.current.delete(nodeId);
    }

    setSelected(new Set([nodeId]));
  };

  const tracks: TrackData[] = [...selected]
    .filter((id) => pointsCache.has(id))
    .map((id) => ({
      nodeId: id,
      color: colorMap.get(id) ?? "#fff",
      points: pointsCache.get(id)!,
    }));

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="sidebar-header">
          <div className="sidebar-title">Trayectorias</div>
          <div className="sidebar-sub">{trackNodes.length} nodos · {date}</div>
        </div>

        <input
          type="date"
          className="search"
          value={date}
          max={todayStr()}
          onChange={(e) => setDate(e.target.value)}
        />

        {loading && (
          <div style={{ color: "#607d8b", padding: "8px 12px", fontSize: 12 }}>
            Cargando...
          </div>
        )}

        <div className="node-list">
          {!loading && trackNodes.length === 0 && (
            <div style={{ color: "#607d8b", padding: "8px 12px", fontSize: 12 }}>
              Sin nodos en movimiento este día.
            </div>
          )}
          {trackNodes.map((n) => {
            const active = selected.has(n.node_id);
            const color = colorMap.get(n.node_id) ?? "#546e7a";
            const nombre = n.long_name || n.short_name || n.node_id;
            const lastTime = new Date(n.last_ts * 1000).toLocaleTimeString("es-AR", {
              hour: "2-digit",
              minute: "2-digit",
            });
            return (
              <div
                key={n.node_id}
                className={`node-item ${active ? "active" : ""}`}
                onClick={() => toggleNode(n.node_id)}
              >
                <span
                  className="dot"
                  style={
                    active
                      ? { background: color, boxShadow: `0 0 5px ${color}` }
                      : { background: "#37474f" }
                  }
                />
                <div className="node-item-info">
                  <span className="node-item-name">{nombre}</span>
                  <span className="node-item-detail">
                    {n.pts} pts · últ. {lastTime}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
        {tracePath && tracePath.length > 0 && (
          <div className="route-section">
            <div className="route-header">
              RUTA
              {tracePathTs != null && (
                <span className="route-age">
                  · hace {Math.round((Date.now() / 1000 - tracePathTs) / 60)} min
                </span>
              )}
            </div>
            {tracePath.map((hop, i) => (
              <div key={hop.node_id}>
                <div className="route-hop">
                  <span className={`route-dot${i === 0 ? " route-dot-bbs" : i === tracePath.length - 1 ? " route-dot-target" : ""}`} />
                  <span className="route-hop-name">
                    {hop.long_name || hop.short_name || hop.node_id}
                  </span>
                </div>
                {i < tracePath.length - 1 && <div className="route-arrow">↓</div>}
              </div>
            ))}
          </div>
        )}
      </aside>

      <main className="main">
        <TrackMap tracks={tracks} />
      </main>
    </div>
  );
}
