import { useState, useEffect, useCallback, useRef } from "react";
import { fetchActivityHeatmap, fetchActivityAlerts, fetchLocalities } from "../api";
import type { ActivityHeatmapNode, ActivityAlert, Locality, NodeEnvMetrics } from "../types";

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

// ── Heatmap row ──────────────────────────────────────────────────────────────
function NodeRow({ node, today }: { node: ActivityHeatmapNode; today: boolean }) {
  const hasAnyActivity = node.slots.some(Boolean);
  return (
    <div className={`heatmap-row ${!hasAnyActivity ? "heatmap-row-silent" : ""}`}>
      <div className="heatmap-name" title={`${node.name} (${node.node_id})`}>
        {node.name}
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
  return (
    <div className="alert-card">
      <div className="alert-card-top">
        <span className="alert-node-name" title={alert.node_id}>{alert.name}</span>
        <span className="alert-duration">{formatDuration(alert.duration_h)}</span>
      </div>
      <div className="alert-time-range">
        {alert.gap_start} <span className="alert-arrow">→</span> {alert.gap_end}
      </div>
      <div className="alert-label">Posible caída de batería</div>
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

  // Carga inicial y refresh de 15 min para todo
  useEffect(() => {
    const refresh = () => {
      loadHeatmap(todayAR());
      loadAlerts();
      loadLocalities();
    };
    refresh();
    heatTimerRef.current = setInterval(refresh, REFRESH_MS);
    return () => { if (heatTimerRef.current) clearInterval(heatTimerRef.current); };
  }, [loadHeatmap, loadAlerts, loadLocalities]);

  // Alerts también cada 5 min
  useEffect(() => {
    alertsTimerRef.current = setInterval(loadAlerts, ALERTS_EXTRA_MS);
    return () => { if (alertsTimerRef.current) clearInterval(alertsTimerRef.current); };
  }, [loadAlerts]);

  // Recarga heatmap cuando cambia la fecha (manual)
  useEffect(() => { loadHeatmap(date); }, [date, loadHeatmap]);

  const goDay = (delta: number) => {
    const next = addDays(date, delta);
    if (next <= today) setDate(next);
  };

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
                <div className="heatmap-name heatmap-name-header">Nodo</div>
                <div className="heatmap-slots heatmap-hour-headers">
                  {HOURS.map(h => (
                    <div key={h} className="heatmap-hour-group heatmap-hour-label">
                      {String(h).padStart(2, "0")}
                    </div>
                  ))}
                </div>
              </div>
              <div className="heatmap-body">
                {nodes.map(node => (
                  <NodeRow key={node.node_id} node={node} today={date === today} />
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
