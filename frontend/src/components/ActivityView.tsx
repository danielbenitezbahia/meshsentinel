import { useState, useEffect, useCallback, useRef } from "react";
import { fetchActivityHeatmap, fetchActivityAlerts, fetchLocalities, fetchEnergyDay } from "../api";
import type { ActivityHeatmapNode, ActivityAlert, Locality, NodeEnvMetrics, NodeEnergyData, EnergyReading } from "../types";

const HOURS = Array.from({ length: 24 }, (_, i) => i);
const REFRESH_MS      = 15 * 60 * 1000;  // 15 min — refresca todo
const ALERTS_EXTRA_MS =  5 * 60 * 1000;  // alerts también cada 5 min

function todayAR(): string {
  const ar = new Date(Date.now() - 3 * 60 * 60 * 1000);
  return ar.toISOString().slice(0, 10);
}

function addDays(date: string, delta: number): string {
  const d = new Date(date + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() + delta);
  return d.toISOString().slice(0, 10);
}

function formatDateLabel(date: string): string {
  const [y, m, d] = date.split("-");
  return `${d}/${m}/${y}`;
}

function slotLabel(slot: number): string {
  const h = String(Math.floor(slot / 2)).padStart(2, "0");
  const m = slot % 2 === 0 ? "00" : "30";
  return `${h}:${m}`;
}

function formatDuration(h: number): string {
  const totalMin = Math.round(h * 60);
  const hh = Math.floor(totalMin / 60);
  const mm = totalMin % 60;
  return mm === 0 ? `${hh}h` : `${hh}h ${mm}m`;
}

// ── Battery sparkline ─────────────────────────────────────────────────────────
function BatterySparkline({ energy, dayStart, dayEnd }: {
  energy: NodeEnergyData;
  dayStart: number;
  dayEnd: number;
}) {
  const W = 58, H = 16;
  const { readings, latest_battery, drop } = energy;
  if (!readings.length) return null;

  const span = dayEnd - dayStart || 1;
  const pts = readings.map((r: EnergyReading) => {
    const x = ((r.ts - dayStart) / span) * W;
    const y = H - (r.b / 100) * H;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");

  const color = latest_battery < 20 ? "#ef4444"
              : drop >= 20          ? "#f97316"
              : latest_battery < 40 ? "#facc15"
              :                       "#4ade80";

  return (
    <div className="hm-battery-wrap">
      <svg width={W} height={H} className="hm-sparkline">
        <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
      </svg>
      <span className="hm-battery-pct" style={{ color }}>{latest_battery}%</span>
    </div>
  );
}

// ── Heatmap row ──────────────────────────────────────────────────────────────
function NodeRow({ node, today, energy, dayStart, dayEnd }: {
  node: ActivityHeatmapNode;
  today: boolean;
  energy?: NodeEnergyData;
  dayStart: number;
  dayEnd: number;
}) {
  const hasAnyActivity = node.slots.some(Boolean);
  return (
    <div className={`heatmap-row ${!hasAnyActivity ? "heatmap-row-silent" : ""}`}>
      <div className="heatmap-name" title={`${node.name} (${node.node_id})`}>
        <span className="hm-node-label">{node.name}</span>
        {energy && energy.readings.length > 0 && (
          <BatterySparkline energy={energy} dayStart={dayStart} dayEnd={dayEnd} />
        )}
      </div>
      <div className="heatmap-slots">
        {HOURS.map(h => (
          <div key={h} className="heatmap-hour-group">
            {[0, 1].map(q => {
              const slotIdx  = h * 2 + q;
              const active   = node.slots[slotIdx];
              const nowSlot  = (new Date().getUTCHours() - 3 + 24) % 24 * 2 + (new Date().getUTCMinutes() >= 30 ? 1 : 0);
              const isFuture = today && slotIdx > nowSlot;
              return (
                <div
                  key={q}
                  className={`heatmap-slot ${active ? "hm-active" : isFuture ? "hm-future" : "hm-inactive"}`}
                  title={`${slotLabel(slotIdx)} – ${slotLabel(Math.min(slotIdx + 1, 47))}`}
                />
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Alert card ───────────────────────────────────────────────────────────────
function AlertCard({ alert }: { alert: ActivityAlert }) {
  const isBatteryCritical = alert.alert_type === "battery_critical";
  const isBatteryDrop     = alert.alert_type === "battery_drop";
  const isBattery         = isBatteryCritical || isBatteryDrop;

  const label = isBatteryCritical ? "Batería crítica"
              : isBatteryDrop     ? `Caída de batería  −${alert.drop}%`
              :                     "Posible caída de batería";

  const cardClass = `alert-card ${isBatteryCritical ? "alert-card-critical" : isBatteryDrop ? "alert-card-battery" : ""}`;

  return (
    <div className={cardClass}>
      <div className="alert-card-top">
        <span className="alert-node-name" title={alert.node_id}>{alert.name}</span>
        {isBattery && alert.battery_level != null
          ? <span className="alert-battery-pct" style={{ color: isBatteryCritical ? "#ef4444" : "#f97316" }}>{alert.battery_level}%</span>
          : <span className="alert-duration">{formatDuration(alert.duration_h)}</span>
        }
      </div>
      <div className="alert-time-range">
        {alert.gap_start} <span className="alert-arrow">→</span> {alert.gap_end}
      </div>
      <div className="alert-label">{label}</div>
    </div>
  );
}

// ── Env metric row ───────────────────────────────────────────────────────────
function EnvRow({ label, value, unit }: { label: string; value: number | null; unit: string }) {
  if (value == null) return null;
  return (
    <div className="loc-env-row">
      <span className="loc-env-label">{label}</span>
      <span className="loc-env-value">{typeof value === "number" ? value.toFixed(1) : value}{unit}</span>
    </div>
  );
}

function LocalityNodeCard({ node }: { node: import("../types").LocalityNode }) {
  const env: NodeEnvMetrics | null = node.env;
  const hasEnv = env && Object.values(env).some((v, i) => i < 7 && v != null);
  return (
    <div className="loc-node-card">
      <div className="loc-node-header">
        <span className="loc-node-name">{node.name}</span>
        {node.last_seen_mins_ago != null && (
          <span className="loc-node-ago">{node.last_seen_mins_ago}m</span>
        )}
      </div>
      {hasEnv && env && (
        <div className="loc-env-grid">
          <EnvRow label="Temp"   value={env.temperature}         unit="°C" />
          <EnvRow label="Hum"    value={env.relative_humidity}   unit="%" />
          <EnvRow label="Pres"   value={env.barometric_pressure} unit=" hPa" />
          <EnvRow label="Volt"   value={env.voltage}             unit=" V" />
          <EnvRow label="IAQ"    value={env.iaq}                 unit="" />
        </div>
      )}
    </div>
  );
}

// ── Localities panel ─────────────────────────────────────────────────────────
function LocalitiesPanel({ localities, loading }: { localities: Locality[]; loading: boolean }) {
  return (
    <div className="localities-panel">
      <div className="alerts-panel-header">
        <span className="alerts-panel-title">Localidades</span>
        {loading && <span className="alerts-spinner">↻</span>}
      </div>

      {!loading && localities.length === 0 && (
        <div className="alerts-empty">
          <div className="alerts-empty-icon">📍</div>
          <div>Sin nodos con coordenadas asignadas</div>
          <div className="alerts-empty-sub">Los nodos se asignan a un partido cuando envían posición GPS</div>
        </div>
      )}

      <div className="localities-list">
        {localities.map(loc => (
          <div key={loc.partido} className="locality-group">
            <div className="locality-name">
              {loc.partido}
              <span className="locality-count">{loc.nodes.length}</span>
            </div>
            {loc.nodes.map(n => (
              <LocalityNodeCard key={n.node_id} node={n} />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Main view ────────────────────────────────────────────────────────────────
export default function ActivityView() {
  const [date, setDate]           = useState<string>(todayAR);
  const [nodes, setNodes]         = useState<ActivityHeatmapNode[]>([]);
  const [loadingHeat, setLoadingHeat]   = useState(false);
  const [errorHeat, setErrorHeat]       = useState<string | null>(null);

  const [alerts, setAlerts]             = useState<ActivityAlert[]>([]);
  const [loadingAlerts, setLoadingAlerts] = useState(false);
  const [alertsCheckedAt, setAlertsCheckedAt] = useState<number | null>(null);

  const [localities, setLocalities]     = useState<Locality[]>([]);
  const [loadingLoc, setLoadingLoc]     = useState(false);

  const [energyMap, setEnergyMap]       = useState<Map<string, NodeEnergyData>>(new Map());
  const [dayBounds, setDayBounds]       = useState<{ start: number; end: number } | null>(null);
  const [nodeSearch, setNodeSearch]     = useState("");

  const today = todayAR();
  const heatTimerRef   = useRef<ReturnType<typeof setInterval> | null>(null);
  const alertsTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadHeatmap = useCallback(async (d: string) => {
    setLoadingHeat(true);
    setErrorHeat(null);
    try {
      const data = await fetchActivityHeatmap(d);
      setNodes(data.nodes);
    } catch (e) {
      setErrorHeat(`Error: ${e}`);
    } finally {
      setLoadingHeat(false);
    }
  }, []);

  const loadAlerts = useCallback(async () => {
    setLoadingAlerts(true);
    try {
      const data = await fetchActivityAlerts();
      setAlerts(data.alerts);
      setAlertsCheckedAt(data.checked_at);
    } catch { /* silently keep last data */ }
    finally { setLoadingAlerts(false); }
  }, []);

  const loadLocalities = useCallback(async () => {
    setLoadingLoc(true);
    try {
      const data = await fetchLocalities();
      setLocalities(data.localities);
    } catch { /* silently keep last data */ }
    finally { setLoadingLoc(false); }
  }, []);

  const loadEnergy = useCallback(async (d: string) => {
    try {
      const data = await fetchEnergyDay(d);
      const map = new Map<string, NodeEnergyData>();
      data.nodes.forEach(n => map.set(n.node_id, n));
      setEnergyMap(map);
      setDayBounds({ start: data.day_start, end: data.day_end });
    } catch { /* silently keep last data */ }
  }, []);

  // Carga inicial y refresh de 15 min para todo
  useEffect(() => {
    const refresh = () => {
      const d = todayAR();
      loadHeatmap(d);
      loadAlerts();
      loadLocalities();
      loadEnergy(d);
    };
    refresh();
    heatTimerRef.current = setInterval(refresh, REFRESH_MS);
    return () => { if (heatTimerRef.current) clearInterval(heatTimerRef.current); };
  }, [loadHeatmap, loadAlerts, loadLocalities, loadEnergy]);

  // Alerts también cada 5 min
  useEffect(() => {
    alertsTimerRef.current = setInterval(loadAlerts, ALERTS_EXTRA_MS);
    return () => { if (alertsTimerRef.current) clearInterval(alertsTimerRef.current); };
  }, [loadAlerts]);

  // Recarga heatmap y energía cuando cambia la fecha (manual)
  useEffect(() => {
    loadHeatmap(date);
    loadEnergy(date);
  }, [date, loadHeatmap, loadEnergy]);

  const goDay = (delta: number) => {
    const next = addDays(date, delta);
    if (next <= today) setDate(next);
  };

  const filteredNodes = nodeSearch.trim()
    ? nodes.filter(n => n.name.toLowerCase().includes(nodeSearch.toLowerCase()))
    : nodes;
  const activeCount = nodes.filter(n => n.slots.some(Boolean)).length;
  const checkedLabel = alertsCheckedAt
    ? new Date(alertsCheckedAt * 1000).toLocaleTimeString("es-AR", { hour: "2-digit", minute: "2-digit" })
    : null;

  return (
    <div className="heatmap-view">
      {/* Toolbar */}
      <div className="heatmap-toolbar">
        <span className="stats-title">Actividad</span>
        <div className="heatmap-nav">
          <button className="hm-nav-btn" onClick={() => goDay(-1)}>←</button>
          <input
            type="date"
            className="hm-date-input"
            value={date}
            max={today}
            onChange={e => { if (e.target.value) setDate(e.target.value); }}
          />
          <button className="hm-nav-btn" onClick={() => goDay(1)} disabled={date >= today}>→</button>
        </div>
        <span className="heatmap-summary">
          {formatDateLabel(date)}
          {!loadingHeat && ` · ${activeCount} nodo${activeCount !== 1 ? "s" : ""} activo${activeCount !== 1 ? "s" : ""}`}
        </span>
      </div>

      {/* 3 columnas */}
      <div className="heatmap-main">

        {/* Col 1: Heatmap (50%) */}
        <div className="heatmap-table-wrap">
          {loadingHeat && <div className="stats-loading">Cargando...</div>}
          {errorHeat   && <div className="stats-error">{errorHeat}</div>}
          {!loadingHeat && !errorHeat && (
            <>
              <div className="heatmap-header-row">
                <div className="heatmap-name heatmap-name-header">
                  <span className="hm-header-label">Nodo</span>
                  <input
                    className="hm-search"
                    placeholder="filtrar..."
                    value={nodeSearch}
                    onChange={e => setNodeSearch(e.target.value)}
                  />
                </div>
                <div className="heatmap-slots heatmap-hour-headers">
                  {HOURS.map(h => (
                    <div key={h} className="heatmap-hour-group heatmap-hour-label">
                      {String(h).padStart(2, "0")}
                    </div>
                  ))}
                </div>
              </div>
              <div className="heatmap-body">
                {filteredNodes.map(node => (
                  <NodeRow
                    key={node.node_id}
                    node={node}
                    today={date === today}
                    energy={energyMap.get(node.node_id)}
                    dayStart={dayBounds?.start ?? 0}
                    dayEnd={dayBounds?.end ?? 86400}
                  />
                ))}
              </div>
            </>
          )}
        </div>

        {/* Col 2: Alertas (25%) */}
        <div className="alerts-panel">
          <div className="alerts-panel-header">
            <span className="alerts-panel-title">Análisis · 24h</span>
            {loadingAlerts && <span className="alerts-spinner">↻</span>}
            {checkedLabel && !loadingAlerts && (
              <span className="alerts-checked-at">{checkedLabel}</span>
            )}
          </div>
          {alerts.length === 0 && !loadingAlerts && (
            <div className="alerts-empty">
              <div className="alerts-empty-icon">✓</div>
              <div>Sin caídas detectadas</div>
              <div className="alerts-empty-sub">Se buscan ausencias ≥ 4h con recuperación posterior</div>
            </div>
          )}
          <div className="alerts-list">
            {alerts.map(a => (
              <AlertCard key={`${a.node_id}-${a.gap_start_ts}`} alert={a} />
            ))}
          </div>
        </div>

        {/* Col 3: Localidades (25%) */}
        <LocalitiesPanel localities={localities} loading={loadingLoc} />

      </div>
    </div>
  );
}
