import type { NodesResponse, NeighborsResponse, TreeResponse, GraphResponse, TrackDatesResponse, TrackNodesResponse, TrackPointsResponse, TracePathResponse, TrafficStats, TrafficEvolutionResponse, StatsNodesResponse, NodeDistanceResponse, NodeEventsResponse, ActivityHeatmapResponse, ActivityAlertsResponse, LocalitiesResponse } from "./types";

export const API_BASE = "";

export async function fetchGraph(): Promise<GraphResponse> {
  const res = await fetch(`${API_BASE}/api/mesh/graph`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchNodes(): Promise<NodesResponse> {
  const res = await fetch(`${API_BASE}/api/mesh/nodes`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchNeighbors(nodeId: string): Promise<NeighborsResponse> {
  const res = await fetch(`${API_BASE}/api/mesh/neighbors/${encodeURIComponent(nodeId)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchTree(nodeId: string): Promise<TreeResponse> {
  const res = await fetch(`${API_BASE}/api/mesh/tree/${encodeURIComponent(nodeId)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchTrackDates(): Promise<TrackDatesResponse> {
  const res = await fetch(`${API_BASE}/api/tracks/dates`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchTrackNodes(date: string): Promise<TrackNodesResponse> {
  const res = await fetch(`${API_BASE}/api/tracks/${date}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchTrackPoints(date: string, nodeId: string): Promise<TrackPointsResponse> {
  const res = await fetch(`${API_BASE}/api/tracks/${date}/${encodeURIComponent(nodeId)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchTrackPath(nodeId: string): Promise<TracePathResponse> {
  const res = await fetch(`${API_BASE}/api/tracks/path/${encodeURIComponent(nodeId)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchTrafficStats(period: string): Promise<TrafficStats> {
  const res = await fetch(`${API_BASE}/api/stats/traffic?period=${period}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchTrafficEvolution(period: string): Promise<TrafficEvolutionResponse> {
  const res = await fetch(`${API_BASE}/api/stats/traffic/evolution?period=${period}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchNodeDistance(nodeId: string): Promise<NodeDistanceResponse> {
  const res = await fetch(`${API_BASE}/api/stats/distance/${encodeURIComponent(nodeId)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchStatsNodes(period: string): Promise<StatsNodesResponse> {
  const res = await fetch(`${API_BASE}/api/stats/nodes?period=${period}&limit=10`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchNodeEvents(period: string): Promise<NodeEventsResponse> {
  const res = await fetch(`${API_BASE}/api/events/nodes?period=${period}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchActivityHeatmap(date: string): Promise<ActivityHeatmapResponse> {
  const res = await fetch(`${API_BASE}/api/activity/heatmap?date=${date}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchActivityAlerts(): Promise<ActivityAlertsResponse> {
  const res = await fetch(`${API_BASE}/api/activity/alerts`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchLocalities(): Promise<LocalitiesResponse> {
  const res = await fetch(`${API_BASE}/api/activity/localities`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
