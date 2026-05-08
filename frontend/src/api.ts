import type { NodesResponse, NeighborsResponse, TreeResponse, GraphResponse, TrackDatesResponse, TrackNodesResponse, TrackPointsResponse } from "./types";

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
