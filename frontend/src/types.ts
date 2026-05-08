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
