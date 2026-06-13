export interface MeshNode {
  node_id: string;
  short_name: string | null;
  long_name: string | null;
  last_seen_ts: number | null;
  last_seen_mins_ago: number | null;
  hops_from_bbs: number | null;
  snr_from_bbs: number | null;
  packets_total: number;
  errors_total: number;
  neighbor_count: number;
  lat: number | null;
  lon: number | null;
  altitude: number | null;
  position_ts: number | null;
  channel_util_avg_24h?: number | null;
  battery_level?: number | null;
  voltage?: number | null;
}

export interface Neighbor {
  node_id: string;
  short_name: string | null;
  long_name: string | null;
  snr: number | null;
  snr_reverse: number | null;
  link_stability: number | null;
  link_age_mins: number | null;
  last_seen_mins_ago: number | null;
}

export interface TreeNode extends MeshNode {
  snr_to_parent?: number | null;
  snr_from_parent?: number | null;
  link_stability?: number | null;
  link_age_mins?: number | null;
  children: TreeNode[];
  _truncated?: boolean;
}

export interface GraphFreshness {
  total_links: number;
  oldest_link_mins: number | null;
  newest_link_mins: number | null;
  warning?: string;
}

export interface MeshEdge {
  reporter: string;
  neighbor: string;
  snr: number | null;
  snr_reverse: number | null;
  times_seen: number;
  age_mins: number | null;
}

export interface GraphResponse {
  nodes: MeshNode[];
  edges: MeshEdge[];
  node_count: number;
  edge_count: number;
  graph_freshness: GraphFreshness;
}

export interface NodesResponse {
  nodes: MeshNode[];
  count: number;
}

export interface NeighborsResponse {
  node: string;
  neighbors: Neighbor[];
  count: number;
}

export interface TreeResponse {
  root: string;
  graph_freshness: GraphFreshness;
  tree: TreeNode;
}

export interface TrackNode {
  node_id: string;
  short_name: string | null;
  long_name: string | null;
  pts: number;
  first_ts: number;
  last_ts: number;
}

export interface TrackPoint {
  lat: number;
  lon: number;
  altitude: number | null;
  speed: number | null;
  heading: number | null;
  ts: number;
  relay_node: string | null;
  rx_snr: number | null;
  hop_count: number | null;
  relay_distance_m: number | null;
  relay_short_name: string | null;
  relay_long_name: string | null;
}

export interface TraceHop {
  node_id: string;
  short_name: string | null;
  long_name: string | null;
}

export interface TracePathResponse {
  path: TraceHop[] | null;
  ts: number | null;
}

export interface TrafficTypeEntry {
  count: number;
  pct: number;
}

export interface PrivateChannelEntry {
  name: string;
  count: number;
}

export interface TrafficStats {
  period: string;
  since_ts: number;
  total: number;
  public: {
    total: number;
    pct_of_total: number;
    by_type: Record<string, TrafficTypeEntry>;
  };
  other_mesh: {
    total: number;
    pct_of_total: number;
    by_type: Record<string, TrafficTypeEntry>;
    by_channel: PrivateChannelEntry[];
  };
  private_encrypted: {
    total: number;
    pct_of_total: number;
  };
}

export interface EvolutionPoint {
  label: string;
  public: number;
  other_mesh: number;
  private_encrypted: number;
}

export interface TrafficEvolutionResponse {
  points: EvolutionPoint[];
}

export interface StatsNodeEntry {
  node_id: string;
  name: string;
  count: number;
}

export interface NodeDistanceResponse {
  node_id: string;
  estimated_distance_km: number;
  estimated_distance_m: number;
  avg_snr: number;
  snr_samples: number;
  model: {
    reference_nodes: number;
    r2: number;
    a: number;
    b: number;
    note: string;
  };
}

export interface StatsNodesResponse {
  public: StatsNodeEntry[];
  other_mesh: StatsNodeEntry[];
  private_encrypted: StatsNodeEntry[];
}

export interface NodeEvent {
  ts: number;
  date: string;
  time: string;
  mins_ago: number;
  node_id: string;
  name: string;
  short_name: string | null;
  long_name: string | null;
  event_type: string;
  hops: number | null;
  snr: number | null;
  heard_by: string | null;
  heard_by_name: string | null;
}

export interface NodeEventsResponse {
  events: NodeEvent[];
  period: string;
  since_ts: number;
}

export interface NodeEnvMetrics {
  temperature:         number | null;
  relative_humidity:   number | null;
  barometric_pressure: number | null;
  gas_resistance:      number | null;
  voltage:             number | null;
  current:             number | null;
  iaq:                 number | null;
  mins_ago:            number | null;
}

export interface LocalityNode {
  node_id:            string;
  name:               string;
  last_seen_mins_ago: number | null;
  env:                NodeEnvMetrics | null;
}

export interface Locality {
  partido: string;
  nodes:   LocalityNode[];
}

export interface LocalitiesResponse {
  localities: Locality[];
}

export interface ActivityAlert {
  alert_type: "gap" | "battery_critical" | "battery_drop";
  node_id: string;
  name: string;
  gap_start: string;
  gap_end: string;
  gap_start_ts: number;
  gap_end_ts: number;
  duration_h: number;
  severity: number;
  battery_level?: number;
  drop?: number;
}

export interface ActivityAlertsResponse {
  alerts: ActivityAlert[];
  since_ts: number;
  checked_at: number;
}

export interface EnergyReading {
  ts: number;
  b: number;   // battery_level 0-100
  v: number | null;  // voltage
}

export interface NodeEnergyData {
  node_id: string;
  name: string;
  readings: EnergyReading[];
  latest_battery: number;
  max_battery: number;
  drop: number;
}

export interface EnergyDayResponse {
  date: string;
  day_start: number;
  day_end: number;
  nodes: NodeEnergyData[];
}

export interface ActivityHeatmapNode {
  node_id: string;
  name: string;
  slots: boolean[];  // 96 booleans: slot 0 = 00:00-00:15, slot 95 = 23:45-24:00
}

export interface ActivityHeatmapResponse {
  date: string;
  nodes: ActivityHeatmapNode[];
}

export interface TrackDatesResponse {
  dates: string[];
}

export interface TrackNodesResponse {
  nodes: TrackNode[];
}

export interface TrackPointsResponse {
  points: TrackPoint[];
}

export interface VisitRow {
  period: string;
  ip: string;
  visits: number;
  ua: string;
  first_ts: number;
  last_ts: number;
}

