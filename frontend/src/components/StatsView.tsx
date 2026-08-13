import { useState, useEffect, useCallback } from "react";
import {
  ResponsiveContainer,
  PieChart, Pie, Cell,
  BarChart, Bar,
  LineChart, Line,
  XAxis, YAxis, CartesianGrid,
  Tooltip, Legend,
} from "recharts";
import { fetchTrafficStats, fetchTrafficEvolution, fetchStatsNodes, fetchChannelUtil } from "../api";
import type { TrafficStats, EvolutionPoint, StatsNodeEntry, ChannelUtilNodeSeries } from "../types";

type Period = "day" | "week" | "month";

const PERIOD_LABELS: Record<Period, string> = { day: "Día", week: "Semana", month: "Mes" };

const C_PUBLIC  = "#42a5f5";
const C_KNOWN   = "#66bb6a";
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

const TOOLTIP_STYLE = {
  background: "#0d2137",
  border: "1px solid #1e3a5f",
  fontSize: 12,
  color: "#cfd8dc",
};

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

export default function StatsView() {
  const [period, setPeriod] = useState<Period>("day");
  const [stats, setStats]         = useState<TrafficStats | null>(null);
  const [evolution, setEvolution] = useState<EvolutionPoint[]>([]);
  const [nodes, setNodes]         = useState<{ public: StatsNodeEntry[]; other_mesh: StatsNodeEntry[]; private_encrypted: StatsNodeEntry[] } | null>(null);
  const [chanUtil, setChanUtil]   = useState<{ labels: string[]; nodes: ChannelUtilNodeSeries[] } | null>(null);
  const [loading, setLoading]     = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([
      fetchTrafficStats(period),
      fetchTrafficEvolution(period),
      fetchStatsNodes(period),
      fetchChannelUtil(period).catch(() => ({ period, labels: [], nodes: [] })),
    ])
      .then(([s, e, n, cu]) => {
        setStats(s);
        setEvolution(e.points);
        setNodes(n);
        setChanUtil({ labels: cu.labels, nodes: cu.nodes });
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [period]);

  useEffect(() => { load(); }, [load]);

  // Donut data
  const donutData = stats ? [
    { name: "Público",          value: stats.public.total,             color: C_PUBLIC },
    { name: "Otras mesh",   value: stats.other_mesh.total,      color: C_KNOWN  },
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

  const byChannel = stats?.other_mesh.by_channel ?? [];
  const evolTitle = period === "day" ? "por hora (hoy)"
                  : period === "week" ? "últimos 7 días"
                  : "últimos 30 días";

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
        <button className="btn-refresh" onClick={load} disabled={loading}>
          {loading ? "…" : "↺"}
        </button>
      </div>

      {/* Cards */}
      <div className="stats-cards">
        <MetricCard label="Total paquetes"  value={stats?.total ?? "—"} />
        <MetricCard label="Público"         value={stats ? `${stats.public.pct_of_total}%` : "—"}        color={C_PUBLIC} />
        <MetricCard label="Otras mesh"  value={stats ? `${stats.other_mesh.pct_of_total}%` : "—"} color={C_KNOWN}  />
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
            <Bar dataKey="other_mesh"        name="Otras mesh"    stackId="a" fill={C_KNOWN}  />
            <Bar dataKey="private_encrypted" name="Encriptado"     stackId="a" fill={C_ENC} radius={[3, 3, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Otras mesh por canal */}
      {/* Ranking de nodos */}
      {nodes && (
        <div className="stats-chart-card stats-chart-full">
          <div className="chart-title">Top nodos por categoría</div>
          <div className="rank-grid">
            <RankColumn title="Público"    data={nodes.public}            color={C_PUBLIC} />
            <RankColumn title="Otras mesh" data={nodes.other_mesh}        color={C_KNOWN}  />
            <RankColumn title="Encriptado" data={nodes.private_encrypted} color={C_ENC}    />
          </div>
        </div>
      )}

      {byChannel.length > 0 && (
        <div className="stats-chart-card stats-chart-half">
          <div className="chart-title">Otras mesh por canal</div>
          <ResponsiveContainer width="100%" height={Math.max(140, byChannel.length * 40)}>
            <BarChart layout="vertical" data={byChannel} margin={{ left: 4, right: 28, top: 4 }}>
              <XAxis type="number" tick={{ fill: "#607d8b", fontSize: 11 }} />
              <YAxis type="category" dataKey="name" width={110} tick={{ fill: "#b0bec5", fontSize: 11 }} />
              <CartesianGrid strokeDasharray="3 3" stroke="#1e3a5f" horizontal={false} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Bar dataKey="count" name="Paquetes" fill={C_KNOWN} radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Utilización del canal RF */}
      <div className="stats-chart-card stats-chart-full">
        <div className="chart-title">Utilización del canal · {evolTitle}</div>
        {chanUtil && chanUtil.nodes.length === 0 ? (
          <div className="rank-empty" style={{ padding: "2rem 0", textAlign: "center" }}>Sin datos de telemetría</div>
        ) : (
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={chanUtilPoints} margin={{ right: 16, top: 4 }}>
              <XAxis dataKey="label" tick={{ fill: "#607d8b", fontSize: 10 }} interval="preserveStartEnd" />
              <YAxis domain={[0, 100]} tickFormatter={(v) => `${v}%`} tick={{ fill: "#607d8b", fontSize: 11 }} width={42} />
              <CartesianGrid strokeDasharray="3 3" stroke="#1e3a5f" />
              <Tooltip
                formatter={(v) => v != null ? `${v}%` : "—"}
                contentStyle={TOOLTIP_STYLE}
                labelStyle={{ color: "#90a4ae" }}
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
