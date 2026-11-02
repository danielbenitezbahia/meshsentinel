import { useState, useEffect, useCallback } from "react";
import {
  ResponsiveContainer,
  PieChart, Pie, Cell,
  BarChart, Bar,
  LineChart, Line,
  XAxis, YAxis, CartesianGrid,
  Tooltip, Legend,
} from "recharts";
import { fetchTrafficStats, fetchTrafficEvolution, fetchStatsNodes, fetchChannelUtil, fetchRoleStats } from "../api";
import { todayAR, addDays, formatDateLabel } from "../dateUtils";
import type { TrafficStats, EvolutionPoint, StatsNodeEntry, ChannelUtilNodeSeries, RoleStats, RoleStatEntry } from "../types";

type Period = "day" | "week" | "month";

const PERIOD_LABELS: Record<Period, string> = { day: "Día", week: "Semana", month: "Mes" };

const C_PUBLIC  = "#42a5f5";
const C_ENC     = "#ef5350";

const TYPE_COLOR: Record<string, string> = {
  TEXT_MESSAGE_APP:   "#ffa726",
  POSITION_APP:       "#26c6da",
  TELEMETRY_APP:      "#ab47bc",
  TRACEROUTE_APP:     "#66bb6a",
  NEIGHBORINFO_APP:   "#78909c",
  NODEINFO_APP:       "#ff7043",
  OTHER:              "#546e7a",
};

const TYPE_LABEL: Record<string, string> = {
  TEXT_MESSAGE_APP:   "Texto",
  POSITION_APP:       "Posición",
  TELEMETRY_APP:      "Telemetría",
  TRACEROUTE_APP:     "Traceroute",
  NEIGHBORINFO_APP:   "Vecinos",
  NODEINFO_APP:       "Info nodo",
  OTHER:              "Otros",
};

const NODE_COLORS = [
  "#42a5f5", "#ffa726", "#ab47bc", "#66bb6a",
  "#ef5350", "#26c6da", "#ff7043", "#78909c",
];

// Orden fijo (no por cantidad) para que la asignación de color sea siempre
// la misma aunque cambien los números -- paleta validada contra CVD con
// scripts/validate_palette.js del skill de dataviz (--mode dark --surface
// #0d2137, que es el fondo real de .stats-chart-card).
const ROLE_ORDER = ["CLIENT", "CLIENT_MUTE", "ROUTER", "ROUTER_LATE", "REPEATER", "OTHER", "UNKNOWN"];

const ROLE_LABEL: Record<string, string> = {
  CLIENT:      "Client",
  CLIENT_MUTE: "Client Mute",
  ROUTER:      "Router",
  ROUTER_LATE: "Router Late",
  REPEATER:    "Repeater",
  OTHER:       "Otros roles",
  UNKNOWN:     "Sin dato",
};

const ROLE_COLOR: Record<string, string> = {
  CLIENT:      "#3987e5",
  CLIENT_MUTE: "#d95926",
  ROUTER:      "#199e70",
  ROUTER_LATE: "#c98500",
  REPEATER:    "#d55181",
  OTHER:       "#008300",
  UNKNOWN:     "#546e7a",
};

const TOOLTIP_STYLE = {
  background: "#0d2137",
  border: "1px solid #1e3a5f",
  fontSize: 12,
  color: "#cfd8dc",
};

// Recharts pinta cada ítem del tooltip con el color de su propia barra/serie
// en vez de heredar el color de TOOLTIP_STYLE -- con colores oscuros (ej. los
// del gráfico de roles, pensados para el fondo de la card, no para texto)
// el texto queda ilegible sobre el fondo igual de oscuro del tooltip.
const TOOLTIP_ITEM_STYLE = { color: "#cfd8dc" };

function ChannelUtilTooltip({ active, payload, label }: any) {
  if (!active || !payload || payload.length === 0) return null;
  const sorted = [...payload]
    .filter((p: any) => p.value != null)
    .sort((a: any, b: any) => (b.value ?? 0) - (a.value ?? 0));
  if (sorted.length === 0) return null;
  return (
    <div
      style={{
        ...TOOLTIP_STYLE,
        padding: "8px 10px",
        borderRadius: 6,
        maxHeight: 280,
        overflowY: "auto",
        // Ancla el tooltip por su borde inferior a la posición del cursor,
        // así crece hacia arriba en vez de hacia abajo: con rankings largos,
        // creciendo para abajo el final de la lista quedaba fuera de la
        // pantalla en vez de simplemente scrollear dentro del tooltip.
        transform: "translateY(calc(-100% - 12px))",
      }}
    >
      <div style={{ color: "#90a4ae", marginBottom: 4 }}>{label}</div>
      {sorted.map((p: any) => (
        <div key={p.dataKey} style={{ display: "flex", alignItems: "center", gap: 6, whiteSpace: "nowrap" }}>
          <span style={{ width: 8, height: 8, borderRadius: "50%", background: p.color, flexShrink: 0 }} />
          <span style={{ flex: 1 }}>{p.name}</span>
          <span>{p.value != null ? `${p.value}%` : "—"}</span>
        </div>
      ))}
    </div>
  );
}

function RankColumn({ title, data, color }: { title: string; data: StatsNodeEntry[]; color: string }) {
  const max = data[0]?.count ?? 1;
  return (
    <div className="rank-col">
      <div className="rank-col-title" style={{ color }}>{title}</div>
      {data.length === 0 && <div className="rank-empty">Sin datos</div>}
      {data.map((n, i) => (
        <div key={n.node_id} className="rank-item">
          <span className="rank-num">{i + 1}</span>
          <div className="rank-info">
            <span className="rank-name" title={n.node_id}>{n.name}</span>
            <div className="rank-bar-row">
              <div className="rank-bar-wrap">
                <div className="rank-bar" style={{ width: `${(n.count / max) * 100}%`, background: color }} />
              </div>
              <span className="rank-count">{n.count.toLocaleString("es-AR")}</span>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function MetricCard({ label, value, color }: { label: string; value: string | number; color?: string }) {
  return (
    <div className="stats-card">
      <div className="stats-card-value" style={color ? { color } : undefined}>
        {typeof value === "number" ? value.toLocaleString("es-AR") : value}
      </div>
      <div className="stats-card-label">{label}</div>
    </div>
  );
}

function RoleNodePanel({
  roleKey, entry, onClose,
}: { roleKey: string; entry: RoleStatEntry; onClose: () => void }) {
  const showRole = roleKey === "OTHER"; // único bucket que mezcla roles distintos
  return (
    <div className="role-node-panel">
      <div className="role-node-panel-header">
        <span className="role-node-panel-title">
          {ROLE_LABEL[roleKey] ?? roleKey} · {entry.count} {entry.count === 1 ? "nodo" : "nodos"}
        </span>
        <button className="role-node-panel-close" onClick={onClose} aria-label="Cerrar">×</button>
      </div>
      <div className="role-node-list">
        {entry.nodes.length === 0 && <div className="rank-empty">Sin datos</div>}
        {entry.nodes.map((n) => (
          <div key={n.node_id} className="role-node-item">
            <span className="role-node-item-name">
              {n.short_name && <span className="role-node-item-short">{n.short_name}</span>}
              {n.short_name && n.long_name ? " · " : ""}
              {n.long_name ?? (!n.short_name ? n.node_id : "")}
            </span>
            {showRole && <span className="role-node-item-role">{n.role ?? "?"}</span>}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function StatsView() {
  const [period, setPeriod] = useState<Period>("day");
  const [stats, setStats]         = useState<TrafficStats | null>(null);
  const [evolution, setEvolution] = useState<EvolutionPoint[]>([]);
  const [nodes, setNodes]         = useState<{ public: StatsNodeEntry[]; private_encrypted: StatsNodeEntry[] } | null>(null);
  const [chanUtil, setChanUtil]   = useState<{ labels: string[]; nodes: ChannelUtilNodeSeries[] } | null>(null);
  const [chanUtilDate, setChanUtilDate] = useState<string>(todayAR);
  const [roleStats, setRoleStats] = useState<RoleStats | null>(null);
  const [selectedRole, setSelectedRole] = useState<string | null>(null);
  const [loading, setLoading]     = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([
      fetchTrafficStats(period),
      fetchTrafficEvolution(period),
      fetchStatsNodes(period),
    ])
      .then(([s, e, n]) => {
        setStats(s);
        setEvolution(e.points);
        setNodes(n);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [period]);

  useEffect(() => { load(); }, [load]);

  // Utilización del canal: fecha propia, independiente del resto de las cards.
  // Solo tiene sentido navegar día a día en el tab "Día" (hora por hora);
  // en Semana/Mes cada punto ya es un día, así que se ignora chanUtilDate.
  const loadChanUtil = useCallback(() => {
    fetchChannelUtil(period, period === "day" ? chanUtilDate : undefined)
      .then((cu) => setChanUtil({ labels: cu.labels, nodes: cu.nodes }))
      .catch(() => setChanUtil({ labels: [], nodes: [] }));
  }, [period, chanUtilDate]);

  useEffect(() => { loadChanUtil(); }, [loadChanUtil]);

  // Snapshot de roles: no depende de period, es "ahora mismo", no una serie histórica.
  const loadRoleStats = useCallback(() => {
    fetchRoleStats().then(setRoleStats).catch(() => setRoleStats(null));
  }, []);

  useEffect(() => { loadRoleStats(); }, [loadRoleStats]);

  const today = todayAR();
  const goChanUtilDay = (delta: number) => {
    const next = addDays(chanUtilDate, delta);
    if (next <= today) setChanUtilDate(next);
  };

  // Donut data
  const donutData = stats ? [
    { name: "Público",          value: stats.public.total,             color: C_PUBLIC },
    { name: "Encriptado",       value: stats.private_encrypted.total,  color: C_ENC    },
  ] : [];

  // Public type horizontal bars
  const publicBars = stats
    ? Object.entries(stats.public.by_type).map(([k, v]) => ({
        name:  TYPE_LABEL[k] ?? k,
        count: v.count,
        pct:   v.pct,
        color: TYPE_COLOR[k] ?? "#546e7a",
      }))
    : [];

  // Orden fijo (ROLE_ORDER), no por cantidad -- sólo se listan los roles que
  // de verdad aparecen (by_role no trae claves en 0).
  const roleBars = roleStats
    ? ROLE_ORDER
        .filter((k) => roleStats.by_role[k])
        .map((k) => ({
          key:   k,
          name:  ROLE_LABEL[k],
          count: roleStats.by_role[k].count,
          pct:   roleStats.by_role[k].pct,
          color: ROLE_COLOR[k],
        }))
    : [];

  const selectedRoleEntry: RoleStatEntry | null =
    selectedRole && roleStats ? roleStats.by_role[selectedRole] ?? null : null;

  const evolTitle = period === "day" ? "por hora (hoy)"
                  : period === "week" ? "últimos 7 días"
                  : "últimos 30 días";

  const chanUtilTitle = period !== "day"
    ? evolTitle
    : chanUtilDate === today ? "por hora (hoy)" : `por hora · ${formatDateLabel(chanUtilDate)}`;

  // Build recharts-friendly wide-format points for channel utilization
  const chanUtilPoints = chanUtil
    ? chanUtil.labels.map((label, i) => {
        const pt: Record<string, number | null | string> = { label };
        for (const n of chanUtil.nodes) pt[n.name] = n.util[i] ?? null;
        return pt;
      })
    : [];

  return (
    <div className="stats-view">
      {/* Header */}
      <div className="stats-header">
        <span className="stats-title">Estadísticas</span>
        <div className="stats-period-tabs">
          {(Object.keys(PERIOD_LABELS) as Period[]).map((p) => (
            <button
              key={p}
              className={`tab ${period === p ? "tab-active" : ""}`}
              onClick={() => setPeriod(p)}
            >
              {PERIOD_LABELS[p]}
            </button>
          ))}
        </div>
        <button className="btn-refresh" onClick={() => { load(); loadChanUtil(); loadRoleStats(); }} disabled={loading}>
          {loading ? "…" : "↺"}
        </button>
      </div>

      {/* Cards */}
      <div className="stats-cards">
        <MetricCard label="Total paquetes"  value={stats?.total ?? "—"} />
        <MetricCard label="Público"         value={stats ? `${stats.public.pct_of_total}%` : "—"}        color={C_PUBLIC} />
        <MetricCard label="Encriptado"      value={stats ? `${stats.private_encrypted.pct_of_total}%` : "—"} color={C_ENC} />
      </div>

      {/* Row 1: Donut + Tipos públicos */}
      <div className="stats-row">
        <div className="stats-chart-card">
          <div className="chart-title">Distribución de tráfico</div>
          <ResponsiveContainer width="100%" height={260}>
            <PieChart>
              <Pie
                data={donutData}
                cx="50%" cy="50%"
                innerRadius={65} outerRadius={105}
                dataKey="value" paddingAngle={2}
              >
                {donutData.map((e, i) => <Cell key={i} fill={e.color} />)}
              </Pie>
              <Tooltip
                formatter={(v) => (v as number).toLocaleString("es-AR")}
                contentStyle={TOOLTIP_STYLE}
                itemStyle={TOOLTIP_ITEM_STYLE}
              />
              <Legend wrapperStyle={{ fontSize: 12, color: "#b0bec5" }} />
            </PieChart>
          </ResponsiveContainer>
        </div>

        <div className="stats-chart-card">
          <div className="chart-title">Canal público por tipo</div>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart layout="vertical" data={publicBars} margin={{ left: 4, right: 28, top: 4 }}>
              <XAxis type="number" tick={{ fill: "#607d8b", fontSize: 11 }} />
              <YAxis type="category" dataKey="name" width={88} tick={{ fill: "#b0bec5", fontSize: 11 }} />
              <CartesianGrid strokeDasharray="3 3" stroke="#1e3a5f" horizontal={false} />
              <Tooltip
                formatter={(v, _, p: any) => [`${(v as number).toLocaleString("es-AR")} (${p.payload.pct}%)`, "Paquetes"]}
                contentStyle={TOOLTIP_STYLE}
                itemStyle={TOOLTIP_ITEM_STYLE}
              />
              <Bar dataKey="count" radius={[0, 4, 4, 0]}>
                {publicBars.map((e, i) => <Cell key={i} fill={e.color} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Evolución */}
      <div className="stats-chart-card stats-chart-full">
        <div className="chart-title">Evolución · {evolTitle}</div>
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={evolution} margin={{ right: 16, top: 4 }}>
            <XAxis dataKey="label" tick={{ fill: "#607d8b", fontSize: 10 }} interval="preserveStartEnd" />
            <YAxis tick={{ fill: "#607d8b", fontSize: 11 }} />
            <CartesianGrid strokeDasharray="3 3" stroke="#1e3a5f" />
            <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={{ color: "#90a4ae" }} />
            <Legend wrapperStyle={{ fontSize: 12, color: "#b0bec5" }} />
            <Bar dataKey="public"            name="Público"        stackId="a" fill={C_PUBLIC} />
            <Bar dataKey="private_encrypted" name="Encriptado"     stackId="a" fill={C_ENC} radius={[3, 3, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Roles de los nodos */}
      {roleStats && roleBars.length > 0 && (
        <div className="stats-chart-card stats-chart-half">
          <div className="chart-title">
            Roles de los nodos (activos 24h) · {roleStats.total_active_nodes}
          </div>
          <ResponsiveContainer width="100%" height={Math.max(140, roleBars.length * 40)}>
            <BarChart layout="vertical" data={roleBars} margin={{ left: 4, right: 28, top: 4 }}>
              <XAxis type="number" tick={{ fill: "#607d8b", fontSize: 11 }} />
              <YAxis type="category" dataKey="name" width={100} tick={{ fill: "#b0bec5", fontSize: 11 }} />
              <CartesianGrid strokeDasharray="3 3" stroke="#1e3a5f" horizontal={false} />
              <Tooltip
                formatter={(v, _, p: any) => [`${(v as number).toLocaleString("es-AR")} (${p.payload.pct}%)`, "Nodos"]}
                contentStyle={TOOLTIP_STYLE}
                itemStyle={TOOLTIP_ITEM_STYLE}
              />
              <Bar
                dataKey="count"
                radius={[0, 4, 4, 0]}
                cursor="pointer"
                onClick={(data: any) => setSelectedRole((prev) => (prev === data.key ? null : data.key))}
              >
                {roleBars.map((e, i) => <Cell key={i} fill={e.color} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          {selectedRoleEntry && selectedRole && (
            <RoleNodePanel
              roleKey={selectedRole}
              entry={selectedRoleEntry}
              onClose={() => setSelectedRole(null)}
            />
          )}
        </div>
      )}

      {/* Ranking de nodos */}
      {nodes && (
        <div className="stats-chart-card stats-chart-full">
          <div className="chart-title">Top nodos por categoría</div>
          <div className="rank-grid">
            <RankColumn title="Público"    data={nodes.public}            color={C_PUBLIC} />
            <RankColumn title="Encriptado" data={nodes.private_encrypted} color={C_ENC}    />
          </div>
        </div>
      )}

      {/* Utilización del canal RF */}
      <div className="stats-chart-card stats-chart-full">
        <div className="chart-title-row">
          <div className="chart-title">Utilización del canal · {chanUtilTitle}</div>
          {period === "day" && (
            <div className="heatmap-nav">
              <button className="hm-nav-btn" onClick={() => goChanUtilDay(-1)}>←</button>
              <input
                type="date"
                className="hm-date-input"
                value={chanUtilDate}
                max={today}
                onChange={(e) => { if (e.target.value) setChanUtilDate(e.target.value); }}
              />
              <button className="hm-nav-btn" onClick={() => goChanUtilDay(1)} disabled={chanUtilDate >= today}>→</button>
            </div>
          )}
        </div>
        {chanUtil && chanUtil.nodes.length === 0 ? (
          <div className="rank-empty" style={{ padding: "2rem 0", textAlign: "center" }}>Sin datos de telemetría</div>
        ) : (
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={chanUtilPoints} margin={{ right: 16, top: 4 }}>
              <XAxis dataKey="label" tick={{ fill: "#607d8b", fontSize: 10 }} interval="preserveStartEnd" />
              <YAxis domain={[0, 100]} tickFormatter={(v) => `${v}%`} tick={{ fill: "#607d8b", fontSize: 11 }} width={42} />
              <CartesianGrid strokeDasharray="3 3" stroke="#1e3a5f" />
              <Tooltip
                content={<ChannelUtilTooltip />}
                allowEscapeViewBox={{ x: true, y: true }}
                wrapperStyle={{ zIndex: 100 }}
              />
              <Legend wrapperStyle={{ fontSize: 12, color: "#b0bec5" }} />
              {chanUtil?.nodes.map((n, i) => (
                <Line
                  key={n.node_id}
                  type="monotone"
                  dataKey={n.name}
                  stroke={NODE_COLORS[i % NODE_COLORS.length]}
                  dot={false}
                  strokeWidth={2}
                  connectNulls
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
