import { useState, useEffect, useCallback, useRef, lazy, Suspense } from "react";
import { createPortal } from "react-dom";
import { Routes, Route, NavLink, Navigate } from "react-router-dom";
import { fetchGraph } from "./api";
import type { MeshNode, MeshEdge, GraphFreshness } from "./types";
import MapView from "./components/MapView";
import NodePanel from "./components/NodePanel";
import TrackView from "./components/TrackView";
import StatsView from "./components/StatsView";
import MeshWarsView from "./components/MeshWarsView";
import ActivityView from "./components/ActivityView";
import { useFloatingTogglePosition } from "./useFloatingTogglePosition";
import { isHiddenFromMap, computeSuspectNodeIds } from "./mapVisuals";
import "./App.css";

// MapLibre GL pesa bastante (WebGL) — carga diferida para no inflar el
// bundle de las pestañas que no lo necesitan.
const Mesh3DView = lazy(() => import("./components/Mesh3DView"));

function MeshTab() {
  const [view3D, setView3D] = useState(false);

  if (view3D) {
    return (
      <Suspense fallback={<div className="app-loading">Cargando mapa 3D…</div>}>
        <Mesh3DView onSwitchTo2D={() => setView3D(false)} />
      </Suspense>
    );
  }
  return <MeshView onSwitchTo3D={() => setView3D(true)} />;
}

function MeshView({ onSwitchTo3D }: { onSwitchTo3D: () => void }) {
  const [nodes, setNodes] = useState<MeshNode[]>([]);
  const [edges, setEdges] = useState<MeshEdge[]>([]);
  const [freshness, setFreshness] = useState<GraphFreshness | null>(null);
  const [selectedId, setSelectedId] = useState<string>("");
  const [selectedNode, setSelectedNode] = useState<MeshNode | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const graphAreaRef = useRef<HTMLDivElement>(null);
  const togglePos = useFloatingTogglePosition(graphAreaRef);

  const loadGraph = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchGraph();
      setNodes(data.nodes);
      setEdges(data.edges);
      setFreshness(data.graph_freshness);
    } catch (e) {
      setError(`Error cargando datos: ${e}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadGraph(); }, [loadGraph]);

  const suspectIds = computeSuspectNodeIds(nodes, edges);
  const filtered = nodes.filter((n) => {
    if (isHiddenFromMap(n.short_name, n.long_name) || suspectIds.has(n.node_id)) return false;
    const q = search.toLowerCase();
    return (
      n.node_id.includes(q) ||
      (n.short_name ?? "").toLowerCase().includes(q) ||
      (n.long_name ?? "").toLowerCase().includes(q)
    );
  });

  const closeSidebar = () => setSidebarOpen(false);
  const selectNode = (id: string) => {
    setSelectedId(id);
    setSelectedNode(nodes.find(n => n.node_id === id) ?? null);
    closeSidebar();
  };

  return (
    <div className="app">
      {/* Backdrop mobile */}
      {sidebarOpen && <div className="sidebar-backdrop" onClick={closeSidebar} />}

      <aside className={`sidebar${sidebarOpen ? " sidebar-open" : ""}`}>
        <div className="sidebar-header">
          <div className="sidebar-sub">{nodes.length} nodos · {edges.length} links</div>
        </div>
        <input
          className="search"
          placeholder="Buscar nodo..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <div className="node-list">
          {filtered.map((n) => {
            const fresh = n.last_seen_mins_ago != null && n.last_seen_mins_ago < 60;
            const active = n.node_id === selectedId;
            return (
              <div
                key={n.node_id}
                className={`node-item ${active ? "active" : ""}`}
                onClick={() => selectNode(n.node_id)}
              >
                <span className={`dot ${fresh ? "fresh" : "stale"}`} />
                <div className="node-item-info">
                  <span className="node-item-name">{n.short_name || n.node_id}</span>
                  <span className="node-item-detail">
                    {n.hops_from_bbs != null ? `${n.hops_from_bbs} hops` : ""}
                    {n.snr_from_bbs != null ? ` · ${n.snr_from_bbs}dB` : ""}
                  </span>
                </div>
                <span className="node-item-pkts">{n.neighbor_count}v</span>
              </div>
            );
          })}
        </div>
      </aside>

      <main className="main">
        <div className="topbar">
          <button className="btn-sidebar-toggle" onClick={() => setSidebarOpen(o => !o)}>☰</button>
          <div className="topbar-left">
            {freshness && freshness.total_links > 0 ? (
              <span className="freshness">
                {freshness.total_links} links · más reciente {freshness.newest_link_mins ?? "?"}min
                {freshness.warning && (
                  <span className="freshness-warn"> ⚠ {freshness.warning}</span>
                )}
              </span>
            ) : (
              <span className="freshness freshness-warn">
                Sin datos de neighborinfo aún — esperá ~15 min tras reiniciar el BBS
              </span>
            )}
          </div>
          <button className="btn-refresh" onClick={loadGraph} disabled={loading}>
            {loading ? "Cargando…" : "↺ Actualizar"}
          </button>
        </div>
        {error && <div className="error-bar">{error}</div>}
        <div className="graph-area" ref={graphAreaRef}>
          <MapView
            nodes={nodes}
            edges={edges}
            selectedId={selectedId}
            onSelectNode={(id) => selectNode(id)}
          />
        </div>
        {createPortal(
          <button
            className="btn-view-toggle"
            style={{ top: togglePos.top, right: togglePos.right }}
            onClick={onSwitchTo3D}
          >
            🧊 Ver en 3D
          </button>,
          document.body
        )}
        {selectedNode && (
          <NodePanel node={selectedNode} onClose={() => { setSelectedId(""); setSelectedNode(null); }} />
        )}
      </main>
    </div>
  );
}

export default function App() {
  return (
    <div className="root-layout">
      <nav className="tabbar">
        <span className="tabbar-brand">Sentinel BBS</span>
        <NavLink className={({ isActive }) => `tab${isActive ? " tab-active" : ""}`} to="/mesh">Mesh</NavLink>
        <NavLink className={({ isActive }) => `tab${isActive ? " tab-active" : ""}`} to="/trayectorias">Trayectorias</NavLink>
        <NavLink className={({ isActive }) => `tab${isActive ? " tab-active" : ""}`} to="/estadisticas">Estadísticas</NavLink>
        <NavLink className={({ isActive }) => `tab${isActive ? " tab-active" : ""}`} to="/actividad">Actividad</NavLink>
        <NavLink className={({ isActive }) => `tab${isActive ? " tab-active" : ""}`} to="/meshwars">MeshWars</NavLink>
      </nav>

      <Routes>
        <Route path="/" element={<Navigate to="/mesh" replace />} />
        <Route path="/mesh" element={<MeshTab />} />
        <Route path="/mesh3d" element={<Navigate to="/mesh" replace />} />
        <Route path="/trayectorias" element={<TrackView />} />
        <Route path="/estadisticas" element={<StatsView />} />
        <Route path="/actividad" element={<ActivityView />} />
        <Route path="/meshwars" element={<MeshWarsView />} />
        <Route path="*" element={<Navigate to="/mesh" replace />} />
      </Routes>
    </div>
  );
}
