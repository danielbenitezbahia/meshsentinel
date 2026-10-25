import { useState, useEffect, useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import {
  Map as MapLibreMap,
  Marker,
  Popup,
  NavigationControl,
  AttributionControl,
  setWorkerUrl,
} from "maplibre-gl";
import type { StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// Vite no resuelve solo el worker interno de MapLibre en el build de
// producción (import.meta.url apunta al chunk final, no a node_modules) —
// hay que pedirle a Vite que lo empaquete aparte y pasarle la URL a mano.
// Sin esto, el mapa queda inicializado pero nunca carga tiles/terreno.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { fetchGraph, setNodeAltitudeOverride } from "../api";
import type { MeshNode, MeshEdge } from "../types";
import { hashNodeColor, isRecentEnoughForMap } from "../mapVisuals";
import { FloatingNodesLayer, type FloatingNode, type FloatingEdge } from "../mesh3dFloatingLayer";
import { useFloatingTogglePosition } from "../useFloatingTogglePosition";

setWorkerUrl(maplibreWorkerUrl);

// Tiles de elevación de AWS Open Data (formato Terrarium/Mapzen), gratis y
// sin API key. MapLibre los soporta nativo via encoding:"terrarium".
const TERRAIN_TILES = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png";
const TERRAIN_SOURCE_ID = "terrain-dem";

// OJO: con terreno exagerado, queryTerrainElevation() devuelve la altura
// MULTIPLICADA por este factor (está documentado así en MapLibre) — no es
// solo un efecto visual. Para cualquier altura que se muestre o se use como
// dato (relieve) hay que dividir por esto. El único lugar donde SÍ conviene
// usar el valor crudo (sin dividir) es el anclaje del poste al piso en
// FloatingNodesLayer, porque ahí necesita coincidir con dónde se dibuja la
// superficie 3D del terreno (que sí está exagerada).
const TERRAIN_EXAGGERATION = 1.4;

type AltSource = "relief" | "reported";

// El pin con el nombre (Marker de MapLibre) sigue apoyado en el piso del
// relieve — eso es una limitación de los Marker, siempre fue así y sigue
// siendo donde se hace click para seleccionar/editar un nodo. Lo que SÍ
// flota de verdad en el espacio 3D es la esfera + poste de FloatingNodesLayer
// (capa custom con three.js), que va desde el piso hasta la altura efectiva
// elegida (relieve/GPS/manual). La esfera en sí no es clickeable todavía,
// solo el pin de abajo.
function effectiveAltitude(
  node: MeshNode,
  map: MapLibreMap | null,
  source: AltSource
): { value: number | null; label: string } {
  if (node.altitude_override != null) {
    return { value: node.altitude_override, label: `${Math.round(node.altitude_override)}m · manual` };
  }
  if (source === "reported") {
    return node.altitude != null
      ? { value: node.altitude, label: `${Math.round(node.altitude)}m · GPS` }
      : { value: null, label: "sin altura GPS" };
  }
  if (map && node.lat != null && node.lon != null) {
    try {
      const raw = map.queryTerrainElevation([node.lon, node.lat]);
      if (raw != null) {
        const real = raw / TERRAIN_EXAGGERATION;
        return { value: real, label: `${Math.round(real)}m · relieve` };
      }
    } catch {
      // terreno todavía no cargado
    }
  }
  return { value: null, label: "relieve…" };
}

function buildStyle(): StyleSpecification {
  return {
    version: 8,
    sources: {
      osm: {
        type: "raster",
        tiles: [
          "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
          "https://b.tile.openstreetmap.org/{z}/{x}/{y}.png",
          "https://c.tile.openstreetmap.org/{z}/{x}/{y}.png",
        ],
        tileSize: 256,
        attribution: "© OpenStreetMap contributors",
      },
      [TERRAIN_SOURCE_ID]: {
        type: "raster-dem",
        tiles: [TERRAIN_TILES],
        tileSize: 256,
        encoding: "terrarium",
        maxzoom: 14,
      },
    },
    layers: [
      { id: "osm", type: "raster", source: "osm" },
      {
        id: "hillshade",
        type: "hillshade",
        source: TERRAIN_SOURCE_ID,
        paint: { "hillshade-exaggeration": 0.5 },
      },
      // Las líneas de conexión entre nodos ya NO son una layer de MapLibre:
      // ahora se dibujan como cilindros rectos en la capa three.js
      // (FloatingNodesLayer.setEdges), para que vayan en línea recta en el
      // espacio 3D entre la altura efectiva de un nodo y la del otro, en vez
      // de quedar "pegadas" al relieve del terreno en el medio como pasaba
      // con una línea 2D sobre terreno exagerado.
    ],
  };
}

type EdgeDraw = FloatingEdge & { label: string };

// Posición 3D efectiva de un nodo (lng/lat + la altura que efectivamente se
// está usando para flotarlo — relieve/GPS/manual, ya resuelta) — se arma a
// partir de los FloatingNode ya calculados en draw(), así los extremos de
// cada línea coinciden EXACTO con dónde está la esfera de cada nodo.
type FloatPos = { lng: number; lat: number; elevation: number };

function buildFloatingEdges(
  edges: MeshEdge[],
  nodeById: Map<string, MeshNode>,
  floatPosById: Map<string, FloatPos>,
  isolateMode: boolean,
  pinnedNodes: Set<string>
): EdgeDraw[] {
  const seen = new Set<string>();
  const result: EdgeDraw[] = [];

  for (const e of edges) {
    const a = nodeById.get(e.reporter);
    const b = nodeById.get(e.neighbor);
    const aPos = floatPosById.get(e.reporter);
    const bPos = floatPosById.get(e.neighbor);
    if (!a || !b || !aPos || !bPos) continue;

    const key = [e.reporter, e.neighbor].sort().join("||");
    if (seen.has(key)) continue;
    seen.add(key);

    const aPinned = pinnedNodes.has(e.reporter);
    const bPinned = pinnedNodes.has(e.neighbor);
    if (isolateMode && !aPinned && !bPinned) continue;

    const snr = e.snr ?? e.snr_reverse;
    const color = snr == null ? "#37474f" : snr >= 8 ? "#00e676" : snr >= 4 ? "#ffeb3b" : snr >= 0 ? "#ff9800" : "#f44336";

    // Ancla = el nodo clickeado (prioriza "a"/reporter si ambos extremos
    // están prendidos). El texto siempre nombra al OTRO nodo. Fuera del modo
    // aislar no hay un nodo "clickeado" de referencia, así que el hover
    // muestra los dos nombres.
    const aName = a.long_name || a.short_name || a.node_id;
    const bName = b.long_name || b.short_name || b.node_id;
    const anchor = isolateMode ? (aPinned ? a : b) : null;
    const label = anchor ? (anchor === a ? bName : aName) : `${aName} ↔ ${bName}`;

    result.push({
      id: key,
      fromLng: aPos.lng,
      fromLat: aPos.lat,
      fromElevation: aPos.elevation,
      toLng: bPos.lng,
      toLat: bPos.lat,
      toElevation: bPos.elevation,
      color,
      label,
    });
  }

  return result;
}

// Distancia máxima en píxeles entre el mouse y una línea (ya proyectada a
// pantalla) para contar como "hover" — ver el handler de mousemove en el
// efecto de init del mapa.
const EDGE_HOVER_PX = 10;

/** Distancia de un punto a un segmento, en 2D (pantalla). */
function distToSegment(px: number, py: number, ax: number, ay: number, bx: number, by: number): number {
  const dx = bx - ax;
  const dy = by - ay;
  const lenSq = dx * dx + dy * dy;
  let t = lenSq > 1e-9 ? ((px - ax) * dx + (py - ay) * dy) / lenSq : 0;
  t = Math.max(0, Math.min(1, t));
  const cx = ax + t * dx;
  const cy = ay + t * dy;
  return Math.hypot(px - cx, py - cy);
}

// node.long_name/short_name los define el dueño de cada nodo de la mesh —
// no son texto de confianza. Antes de este cambio el marker solo mostraba
// short_name (4 caracteres, formato fijo del firmware); ahora que el label
// principal es long_name (texto libre, más largo) hay que escaparlo antes
// de meterlo en innerHTML para no abrir un XSS si alguien le pone HTML al
// nombre de su nodo.
function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function makeMarkerEl(node: MeshNode, altLabel: string, selected: boolean): HTMLDivElement {
  const el = document.createElement("div");
  const bg = hashNodeColor(node.node_id);
  const label = escapeHtml(node.long_name || node.short_name || node.node_id.slice(-4));
  const border = selected ? "2px solid #ffffff" : "1.5px solid rgba(255,255,255,0.28)";
  const shadow = selected
    ? "0 0 0 2.5px #e91e63, 0 3px 12px rgba(0,0,0,0.75)"
    : "0 2px 7px rgba(0,0,0,0.55)";

  el.style.cssText = "display:inline-flex;flex-direction:column;align-items:center;cursor:pointer;";
  el.innerHTML = `<div style="
      background:${bg};color:#fff;padding:3px 9px;border-radius:6px;
      font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
      font-size:12px;font-weight:700;white-space:nowrap;letter-spacing:0.3px;
      border:${border};box-shadow:${shadow};line-height:1.4;
    ">${label}</div>
    <div style="
      background:rgba(13,33,55,0.85);color:#90a4ae;font-size:9px;
      padding:1px 5px;border-radius:3px;margin-top:2px;white-space:nowrap;
    ">${altLabel}</div>
    <div style="
      width:7px;height:7px;background:#fff;border:2px solid ${bg};
      border-radius:50%;margin-top:2px;flex-shrink:0;
    "></div>`;
  return el;
}

export default function Mesh3DView({ onSwitchTo2D }: { onSwitchTo2D: () => void }) {
  const [nodes, setNodes] = useState<MeshNode[]>([]);
  const [edges, setEdges] = useState<MeshEdge[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [selectedId, setSelectedId] = useState<string>("");
  const [altSource, setAltSource] = useState<AltSource>("relief");

  // Modo "aislar conexiones": con esto activo, las líneas arrancan todas
  // apagadas; tocar un nodo prende las suyas (nombradas con el vecino al que
  // van) y se van acumulando — cada nodo tocado queda prendido hasta que se
  // vuelve a apretar el toggle, que resetea todo y vuelve a mostrar todas
  // las conexiones como al principio.
  const [isolateMode, setIsolateMode] = useState(false);
  const [pinnedNodes, setPinnedNodes] = useState<Set<string>>(new Set());

  // Backdoor: click derecho en un nodo abre el editor de altura manual.
  const [editingNode, setEditingNode] = useState<MeshNode | null>(null);
  const [editAltitude, setEditAltitude] = useState("");
  const [editSecret, setEditSecret] = useState(""); // en memoria nomás, no se persiste
  const [editError, setEditError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const togglePos = useFloatingTogglePosition(containerRef);
  const mapRef = useRef<MapLibreMap | null>(null);
  const markersRef = useRef<Map<string, Marker>>(new Map());
  const terrainReadyRef = useRef(false);
  const floatingLayerRef = useRef<FloatingNodesLayer | null>(null);
  const originSetRef = useRef(false);
  const edgeHoverPopupRef = useRef<Popup | null>(null);
  // El handler "idle" PENDIENTE actualmente registrado (si hay uno), para
  // poder sacarlo con map.off() antes de registrar uno nuevo en el próximo
  // efecto — ver el efecto de dibujado más abajo. Sin esto, cada cambio de
  // estado apilaba un once("idle", draw) más, y uno viejo (con
  // isolateMode/pinnedNodes desactualizados) podía terminar disparando
  // DESPUÉS de uno nuevo y pisar los datos correctos con datos viejos.
  const pendingIdleHandlerRef = useRef<(() => void) | null>(null);
  // Edges dibujados en el último draw(), para que el handler de mousemove
  // (registrado UNA sola vez en el efecto de init del mapa) siempre lea la
  // data más reciente sin tener que re-registrarse en cada cambio de estado.
  const edgesForHoverRef = useRef<EdgeDraw[]>([]);

  const loadGraph = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchGraph();
      setNodes(data.nodes);
      setEdges(data.edges);
    } catch (e) {
      setError(`Error cargando datos: ${e}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadGraph(); }, [loadGraph]);

  // Init del mapa, una sola vez.
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = new MapLibreMap({
      container: containerRef.current,
      style: buildStyle(),
      center: [-62.27, -38.72], // Bahía Blanca, se recentra solo al tener nodos
      zoom: 9,
      pitch: 55,
      maxPitch: 85,
    });
    map.addControl(new NavigationControl({ visualizePitch: true }), "top-right");
    map.addControl(new AttributionControl({ compact: true }));

    map.on("load", () => {
      map.setTerrain({ source: TERRAIN_SOURCE_ID, exaggeration: 1.4 });
      terrainReadyRef.current = true;

      const floatingLayer = new FloatingNodesLayer();
      floatingLayerRef.current = floatingLayer;
      map.addLayer(floatingLayer);

      // Tooltip al pasar el mouse por una línea de conexión. Las líneas ya
      // no son una layer de MapLibre (ahora son cilindros three.js, ver
      // FloatingNodesLayer.setEdges) así que no hay query de features nativo
      // — en cambio, proyectamos los dos extremos de cada edge a píxeles de
      // pantalla (mismo método que usa el render 3D, vía projectToScreen) y
      // medimos la distancia del mouse al segmento 2D resultante. Registrado
      // UNA sola vez acá (no en el efecto de datos) y leyendo
      // edgesForHoverRef.current, así siempre ve la data más reciente sin
      // reabrir el problema de listeners viejos pisando a los nuevos.
      map.on("mousemove", (ev) => {
        const layer = floatingLayerRef.current;
        if (!layer) return;
        const { x: mx, y: my } = ev.point;
        let closestDist = Infinity;
        let closestLabel: string | null = null;

        for (const edge of edgesForHoverRef.current) {
          const p1 = layer.projectToScreen(edge.fromLng, edge.fromLat, edge.fromElevation);
          const p2 = layer.projectToScreen(edge.toLng, edge.toLat, edge.toElevation);
          if (!p1 || !p2) continue;
          const d = distToSegment(mx, my, p1.x, p1.y, p2.x, p2.y);
          if (d < closestDist) {
            closestDist = d;
            closestLabel = edge.label;
          }
        }

        if (closestLabel && closestDist <= EDGE_HOVER_PX) {
          map.getCanvas().style.cursor = "pointer";
          if (!edgeHoverPopupRef.current) {
            edgeHoverPopupRef.current = new Popup({
              closeButton: false,
              closeOnClick: false,
              offset: 10,
              className: "edge-hover-popup",
            });
          }
          edgeHoverPopupRef.current.setLngLat(map.unproject(ev.point)).setText(closestLabel).addTo(map);
        } else {
          map.getCanvas().style.cursor = "";
          edgeHoverPopupRef.current?.remove();
        }
      });
    });

    mapRef.current = map;
    return () => {
      edgeHoverPopupRef.current?.remove();
      edgeHoverPopupRef.current = null;
      map.remove(); // dispara onRemove() de FloatingNodesLayer, que libera sus recursos de three.js
      mapRef.current = null;
      floatingLayerRef.current = null;
      originSetRef.current = false;
    };
  }, []);

  const selectNode = useCallback((id: string) => {
    setSelectedId(id);
    setSidebarOpen(false);
    if (isolateMode) {
      setPinnedNodes((prev) => (prev.has(id) ? prev : new Set(prev).add(id)));
    }
    const node = nodes.find((n) => n.node_id === id);
    const map = mapRef.current;
    if (node?.lat != null && node?.lon != null && map) {
      map.flyTo({ center: [node.lon, node.lat], zoom: Math.max(map.getZoom(), 13), duration: 600 });
    }
  }, [nodes, isolateMode]);

  const toggleIsolateMode = useCallback(() => {
    setIsolateMode((prev) => !prev);
    setPinnedNodes(new Set());
  }, []);

  const openEditor = useCallback((node: MeshNode) => {
    setEditingNode(node);
    setEditAltitude(node.altitude_override != null ? String(Math.round(node.altitude_override)) : "");
    setEditError(null);
  }, []);

  // Dibuja/actualiza markers y el layer de edges cada vez que cambian los
  // datos o la fuente de altura elegida.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const draw = () => {
      const nodesWithPos = nodes.filter(
        (n) => n.lat != null && n.lon != null && isRecentEnoughForMap(n.last_seen_mins_ago)
      );

      if (nodesWithPos.length > 0 && markersRef.current.size === 0) {
        const avgLon = nodesWithPos.reduce((s, n) => s + n.lon!, 0) / nodesWithPos.length;
        const avgLat = nodesWithPos.reduce((s, n) => s + n.lat!, 0) / nodesWithPos.length;
        map.jumpTo({ center: [avgLon, avgLat] });
      }

      // Origen local para FloatingNodesLayer: cualquier punto cercano a los
      // nodos sirve, solo se usa una vez para evitar imprecisión de punto
      // flotante lejos del (0,0,0) de la escena three.js.
      if (!originSetRef.current && nodesWithPos.length > 0 && floatingLayerRef.current) {
        const first = nodesWithPos[0];
        const groundElev = map.queryTerrainElevation([first.lon!, first.lat!]) ?? 0;
        floatingLayerRef.current.setOrigin(first.lon!, first.lat!, groundElev);
        originSetRef.current = true;
      }

      const nodeById = new Map(nodesWithPos.map((n) => [n.node_id, n]));
      const seenIds = new Set<string>();
      const floatingNodes: FloatingNode[] = [];

      for (const node of nodesWithPos) {
        seenIds.add(node.node_id);
        const { value: floatElevation, label } = effectiveAltitude(node, map, altSource);
        const el = makeMarkerEl(node, label, node.node_id === selectedId);
        el.addEventListener("click", () => selectNode(node.node_id));
        el.addEventListener("contextmenu", (ev) => { ev.preventDefault(); openEditor(node); });

        const existing = markersRef.current.get(node.node_id);
        if (existing) existing.remove();

        const marker = new Marker({ element: el, anchor: "bottom" })
          .setLngLat([node.lon!, node.lat!])
          .addTo(map);
        markersRef.current.set(node.node_id, marker);

        // groundElevation se deja CRUDO (exagerado): tiene que coincidir con
        // dónde el propio MapLibre dibuja la superficie 3D del terreno, que
        // está exagerada por el mismo factor. floatElevation, en cambio, sale
        // de effectiveAltitude() en metros reales (relieve ya viene dividido
        // más arriba; GPS/manual ya son reales de por sí) — para ubicarlo en
        // la escena 3D hay que volver a aplicarle la exageración, así queda
        // expresado en la misma unidad "visual" que groundElevation. Sin este
        // paso, un nodo con altura GPS/manual cercana al nivel del piso
        // terminaría flotando por debajo de la superficie del terreno.
        const groundElevation = map.queryTerrainElevation([node.lon!, node.lat!]) ?? 0;
        const floatElevationTrue = floatElevation ?? groundElevation / TERRAIN_EXAGGERATION;
        floatingNodes.push({
          id: node.node_id,
          lng: node.lon!,
          lat: node.lat!,
          groundElevation,
          floatElevation: floatElevationTrue * TERRAIN_EXAGGERATION,
          color: hashNodeColor(node.node_id),
          selected: node.node_id === selectedId,
        });
      }

      for (const [id, marker] of markersRef.current.entries()) {
        if (!seenIds.has(id)) {
          marker.remove();
          markersRef.current.delete(id);
        }
      }

      floatingLayerRef.current?.setNodes(floatingNodes);

      // floatPosById sale de los floatingNodes YA calculados arriba (misma
      // altura efectiva que usa la esfera de cada nodo), así cada línea
      // arranca/termina exacto donde está la esfera del otro extremo.
      const floatPosById = new Map(floatingNodes.map((n) => [n.id, { lng: n.lng, lat: n.lat, elevation: n.floatElevation }]));
      const floatingEdges = buildFloatingEdges(edges, nodeById, floatPosById, isolateMode, pinnedNodes);
      floatingLayerRef.current?.setEdges(floatingEdges);
      edgesForHoverRef.current = floatingEdges;
    };

    // OJO: antes acá también se chequeaba map.isStyleLoaded() — pero esa
    // función puede devolver false más adelante (no solo antes del load
    // inicial) cada vez que el estilo tiene tiles nuevos para cargar, p.ej.
    // justo después de un flyTo a una zona del mapa todavía no visitada. Si
    // eso pasa, caía en la rama else y quedaba esperando un evento "load"
    // que ya disparó una sola vez al principio y nunca vuelve a disparar —
    // el draw() de esa actualización se perdía hasta que el próximo "idle"
    // cualquiera lo rescatara (podían pasar varios segundos: eso era el bug
    // de "las conexiones no se activan al clickear un nodo"). terrainReadyRef
    // ya garantiza por sí solo que el mapa cargó al menos una vez.
    if (terrainReadyRef.current) {
      draw();
    } else {
      map.once("load", draw);
    }

    // Reintento vía "idle": saca cualquier handler pendiente de un efecto
    // anterior antes de registrar uno nuevo (en vez de apilar un once("idle")
    // más en cada cambio de estado) — necesario para que los tiles de
    // terreno que terminan de cargar tarde actualicen los labels de
    // "relieve…", sin reabrir el bug de datos viejos pisando datos nuevos.
    //
    // A propósito es UN SOLO once() por efecto, no un listener persistente:
    // draw() → floatingLayerRef.setNodes() → map.triggerRepaint() puede
    // disparar un "idle" más una vez que ese repaint termina. Con once(),
    // esa segunda vuelta consume el listener y listo. Un listener
    // persistente (map.on en vez de once) convierte eso en un loop infinito
    // de repintado — exactamente el bug que advierte el comentario en
    // mesh3dFloatingLayer.ts render(), solo que un nivel más arriba — y así
    // se manifestó la primera vez que se probó esto: markers
    // destruyéndose/recreándose cada ~500ms para siempre.
    if (pendingIdleHandlerRef.current) {
      map.off("idle", pendingIdleHandlerRef.current);
    }
    const idleHandler = () => {
      pendingIdleHandlerRef.current = null;
      draw();
    };
    pendingIdleHandlerRef.current = idleHandler;
    map.once("idle", idleHandler);
  }, [nodes, edges, altSource, selectedId, selectNode, openEditor, isolateMode, pinnedNodes]);

  const filtered = nodes.filter((n) => {
    const q = search.toLowerCase();
    return (
      n.node_id.includes(q) ||
      (n.short_name ?? "").toLowerCase().includes(q) ||
      (n.long_name ?? "").toLowerCase().includes(q)
    );
  });

  const closeEditor = () => { setEditingNode(null); setEditError(null); };

  const saveOverride = async () => {
    if (!editingNode) return;
    if (!editSecret.trim()) { setEditError("Falta la clave"); return; }
    const parsed = editAltitude.trim() === "" ? null : Number(editAltitude);
    if (parsed != null && Number.isNaN(parsed)) { setEditError("Altura inválida"); return; }

    setSaving(true);
    setEditError(null);
    try {
      await setNodeAltitudeOverride(editingNode.node_id, parsed, editSecret.trim());
      setNodes((prev) => prev.map((n) =>
        n.node_id === editingNode.node_id ? { ...n, altitude_override: parsed } : n
      ));
      closeEditor();
    } catch (e) {
      setEditError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="app">
      {sidebarOpen && <div className="sidebar-backdrop" onClick={() => setSidebarOpen(false)} />}

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
            return (
              <div
                key={n.node_id}
                className={`node-item ${n.node_id === selectedId ? "active" : ""}`}
                onClick={() => selectNode(n.node_id)}
              >
                <span className={`dot ${fresh ? "fresh" : "stale"}`} />
                <div className="node-item-info">
                  <span className="node-item-name">{n.long_name || n.short_name || n.node_id}</span>
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
          <button className="btn-sidebar-toggle" onClick={() => setSidebarOpen((o) => !o)}>☰</button>
          <div className="topbar-left">
            <div className="alt-source-toggle">
              <button
                className={`alt-toggle-btn ${altSource === "relief" ? "active" : ""}`}
                onClick={() => setAltSource("relief")}
              >
                Relieve
              </button>
              <button
                className={`alt-toggle-btn ${altSource === "reported" ? "active" : ""}`}
                onClick={() => setAltSource("reported")}
              >
                Reportada (GPS)
              </button>
            </div>
            <div className="alt-source-toggle">
              <button
                className={`alt-toggle-btn ${isolateMode ? "active" : ""}`}
                onClick={toggleIsolateMode}
                title={isolateMode ? "Tocá nodos para prender sus conexiones" : "Mostrando todas las conexiones"}
              >
                {isolateMode ? "Conexiones: por nodo" : "Conexiones: todas"}
              </button>
            </div>
          </div>
          <button className="btn-refresh" onClick={loadGraph} disabled={loading}>
            {loading ? "Cargando…" : "↺ Actualizar"}
          </button>
        </div>
        {error && <div className="error-bar">{error}</div>}
        <div className="graph-area">
          <div ref={containerRef} style={{ width: "100%", height: "100%" }} />
        </div>
      </main>
      {createPortal(
        <button
          className="btn-view-toggle"
          style={{ top: togglePos.top, right: togglePos.right }}
          onClick={onSwitchTo2D}
        >
          🗺 Ver en 2D
        </button>,
        document.body
      )}

      {editingNode && (
        <div className="mesh3d-editor-backdrop" onClick={closeEditor}>
          <div className="mesh3d-editor" onClick={(e) => e.stopPropagation()}>
            <div className="mesh3d-editor-title">
              Altura manual · {editingNode.long_name || editingNode.short_name || editingNode.node_id}
            </div>
            <label className="mesh3d-editor-label">Altura en metros (vacío = borrar override)</label>
            <input
              className="mesh3d-editor-input"
              type="number"
              value={editAltitude}
              onChange={(e) => setEditAltitude(e.target.value)}
              placeholder="ej. 180"
            />
            <label className="mesh3d-editor-label">Clave</label>
            <input
              className="mesh3d-editor-input"
              type="password"
              value={editSecret}
              onChange={(e) => setEditSecret(e.target.value)}
            />
            {editError && <div className="mesh3d-editor-error">{editError}</div>}
            <div className="mesh3d-editor-actions">
              <button onClick={closeEditor} disabled={saving}>Cancelar</button>
              <button onClick={saveOverride} disabled={saving} className="primary">
                {saving ? "Guardando…" : "Guardar"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
