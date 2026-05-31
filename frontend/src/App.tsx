import { useState, useEffect, useCallback } from "react";
import { fetchGraph } from "./api";
import type { MeshNode, MeshEdge, GraphFreshness } from "./types";
import MapView from "./components/MapView";
import NodePanel from "./components/NodePanel";
import TrackView from "./components/TrackView";
import StatsView from "./components/StatsView";
import MeshWarsView from "./components/MeshWarsView";
import "./App.css";

type View = "mesh" | "tracks" | "stats" | "wars";

export default function App() {
  const [view, setView] = useState<View>("mesh");
  const [nodes, setNodes] = useState<MeshNode[]>([]);
  const [edges, setEdges] = useState<MeshEdge[]>([]);
  const [freshness, setFreshness] = useState<GraphFreshness | null>(null);
  const [selectedId, setSelectedId] = useState<string>("");
  const [selectedNode, setSelectedNode] = useState<MeshNode | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");

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

  useEffect(() => {
    loadGraph();
  }, []);


  const filtered = nodes.filter((n) => {
    const q = search.toLowerCase();
    return (
      n.node_id.includes(q) ||
      (n.short_name ?? "").toLowerCase().includes(q) ||
      (n.long_name ?? "").toLowerCase().includes(q)
    );
  });

  return (
    <div className="root-layout">
      <nav className="tabbar">
        <span className="tabbar-brand">Sentinel BBS</span>
        <button
          className={`tab ${view === "mesh" ? "tab-active" : ""}`}
          onClick={() => setView("mesh")}
        >
          Mesh
        </button>
        <button
          className={`tab ${view === "tracks" ? "tab-active" : ""}`}
          onClick={() => setView("tracks")}
        >
          Trayectorias
        </button>
        <button
          className={`tab ${view === "stats" ? "tab-active" : ""}`}
          onClick={() => setView("stats")}
        >
          Estadísticas
        </button>
        <button
          className={`tab ${view === "wars" ? "tab-active" : ""}`}
          onClick={() => setView("wars")}
        >
          MeshWars
        </button>
      </nav>

      {view === "tracks" ? (
        <TrackView />
      ) : view === "stats" ? (
        <StatsView />
      ) : view === "wars" ? (
        <MeshWarsView />
      ) : (
      <div className="app">
      <aside className="sidebar">
        <div className="sidebar-header">
          <div className="sidebar-sub">
            {nodes.length} nodos · {edges.length} links
          </div>
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
                onClick={() => { setSelectedId(n.node_id); setSelectedNode(n); }}
              >
                <span className={`dot ${fresh ? "fresh" : "stale"}`} />
                <div className="node-item-info">
                  <span className="node-item-name">
                    {n.short_name || n.node_id}
                  </span>
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

        <div className="graph-area">
          <MapView
            nodes={nodes}
            edges={edges}
            selectedId={selectedId}
            onSelectNode={(id) => { setSelectedId(id); setSelectedNode(nodes.find(n => n.node_id === id) ?? null); }}
          />
        </div>

        {selectedNode && (
          <NodePanel node={selectedNode} onClose={() => { setSelectedId(""); setSelectedNode(null); }} />
        )}
      </main>
    </div>
      )}
    </div>
  );
}
