import { useState, useEffect } from "react";
import type { MeshNode } from "../types";
import type { NodeDistanceResponse } from "../types";
import { fetchNodeDistance } from "../api";

interface Props {
  node: MeshNode | null;
  onClose: () => void;
}

function row(label: string, value: React.ReactNode) {
  return (
    <div className="np-row" key={label}>
      <span className="np-label">{label}</span>
      <span className="np-value">{value ?? "—"}</span>
    </div>
  );
}

export default function NodePanel({ node, onClose }: Props) {
  const [distance, setDistance] = useState<NodeDistanceResponse | null>(null);
  const [distLoading, setDistLoading] = useState(false);

  useEffect(() => {
    if (!node) return;
    setDistance(null);
    setDistLoading(true);
    fetchNodeDistance(node.node_id)
      .then(setDistance)
      .catch(() => setDistance(null))
      .finally(() => setDistLoading(false));
  }, [node?.node_id]);

  if (!node) return null;

  const lastSeen =
    node.last_seen_mins_ago != null
      ? node.last_seen_mins_ago < 2
        ? "hace instantes"
        : `hace ${node.last_seen_mins_ago} min`
      : "desconocido";

  const snrLabel = node.snr_from_bbs != null ? `${node.snr_from_bbs} dB` : null;

  const distLabel = distLoading
    ? "calculando…"
    : distance
    ? `~${distance.estimated_distance_km} km (r²: ${distance.model.r2}, ${distance.snr_samples} muestras)`
    : null;

  return (
    <div className="node-panel">
      <div className="np-header">
        <div>
          <div className="np-name">{node.long_name || node.short_name || node.node_id}</div>
          <div className="np-id">{node.node_id}</div>
        </div>
        <button className="np-close" onClick={onClose}>✕</button>
      </div>

      <div className="np-body">
        {node.short_name && row("Nombre corto", node.short_name)}
        {row("Último visto", lastSeen)}
        {row("Hops desde BBS", node.hops_from_bbs)}
        {row("SNR desde BBS", snrLabel)}
        {distLabel && row("Distancia est.", distLabel)}
        {node.channel_util_avg_24h != null && row("Chan util 24h", `${node.channel_util_avg_24h}%`)}
        {node.battery_level != null && row("Batería", `${node.battery_level}% (${node.voltage}V)`)}
        {row("Paquetes totales", node.packets_total)}
        {row("Errores", node.errors_total)}
        {row("Vecinos conocidos", node.neighbor_count)}
      </div>
    </div>
  );
}
