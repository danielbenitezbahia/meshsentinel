import { useState, useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { latLngToCell, gridDisk, cellToBoundary, cellToLatLng } from "h3-js";
import { fetchGraph } from "../api";
import type { MeshNode } from "../types";

const ACTIVE_MINS    = 120;
const H3_RES         = 7;
const PROD_SNR_MIN   = 3.0;   // SNR threshold for production centers
const PROD_INTERVAL  = 5;     // every N turns, production centers double troops
const PROD_CAP       = 15;    // max troops after doubling

type Faction = "player" | "ai1" | "ai2" | "ai3";
type Phase =
  | "reinforcement"
  | "action"
  | "attack_target"
  | "attack_source"
  | "attack_confirm"
  | "post_conquest"
  | "move_source"
  | "move_target"
  | "ai_turn"
  | "game_over";

type AIEvent =
  | { type: "faction_start";   faction: Faction }
  | { type: "census_start";    faction: Faction; cellIds: string[] }
  | { type: "reinforce";       cellId: string; troops: number }
  | { type: "attack_announce"; fromId: string; toId: string }
  | { type: "attack_round";    fromId: string; toId: string; fromTroops: number; toTroops: number; atkRoll: number; defRoll: number }
  | { type: "attack_result";   fromId: string; toId: string; won: boolean; fromTroops: number; toTroops: number; newOwner: Faction | null }
  | { type: "done" };

const COLORS: Record<Faction, string> = {
  player: "#1976d2", ai1: "#c62828", ai2: "#2e7d32", ai3: "#f57f17",
};
const NAMES: Record<Faction, string> = {
  player: "AZUL", ai1: "ROJO", ai2: "VERDE", ai3: "AMARILLO",
};
const AI_FACTIONS: Faction[] = ["ai1", "ai2", "ai3"];

interface GameCell {
  h3Index:      string;
  owner:        Faction;
  troops:       number;
  nodeId?:      string;
  nodeName:     string;
  isNodeActive: boolean;
  isSynthetic:  boolean;
  isProduction: boolean;
}

interface Bridge { fromH3: string; toH3: string }

interface GameState {
  cells:              Record<string, GameCell>;
  bridges:            Bridge[];
  islands:            string[][];   // H3-adjacency-only components (for C>)
  phase:              Phase;
  turn:               number;
  reinforcementsLeft: number;
  attackTarget:       string | null;
  attackSource:       string | null;
  moveSource:         string | null;
  postConquestTroops: number;
  winner:             Faction | null;
  currentFaction:     Faction;
  combatMsg:          string;
  aiQueue:            AIEvent[];
  aiMsg:              string;
  log:                string[];
}

// ── geo ───────────────────────────────────────────────────────────────────────

function haversineKm(la: number, lo: number, lb: number, ll: number): number {
  const R = 6371, dLat = (lb - la) * Math.PI / 180, dLon = (ll - lo) * Math.PI / 180;
  const a = Math.sin(dLat / 2) ** 2
    + Math.cos(la * Math.PI / 180) * Math.cos(lb * Math.PI / 180) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

function bearingDeg(la: number, lo: number, lb: number, ll: number): number {
  const dLon = (ll - lo) * Math.PI / 180;
  const φ1 = la * Math.PI / 180, φ2 = lb * Math.PI / 180;
  const x = Math.sin(dLon) * Math.cos(φ2);
  const y = Math.cos(φ1) * Math.sin(φ2) - Math.sin(φ1) * Math.cos(φ2) * Math.cos(dLon);
  return (Math.atan2(x, y) * 180 / Math.PI + 360) % 360;
}

function bearingToDir(b: number): string {
  if (b < 22.5 || b >= 337.5) return "Norte";
  if (b < 67.5)  return "Noreste";
  if (b < 112.5) return "Este";
  if (b < 157.5) return "Sureste";
  if (b < 202.5) return "Sur";
  if (b < 247.5) return "Suroeste";
  if (b < 292.5) return "Oeste";
  return "Noroeste";
}

// ── board ─────────────────────────────────────────────────────────────────────

function findComponents(ids: string[]): string[][] {
  const idSet = new Set(ids), visited = new Set<string>(), result: string[][] = [];
  for (const id of ids) {
    if (visited.has(id)) continue;
    const comp: string[] = [], queue = [id];
    while (queue.length) {
      const cur = queue.pop()!;
      if (visited.has(cur)) continue;
      visited.add(cur); comp.push(cur);
      gridDisk(cur, 1).forEach(nb => {
        if (nb !== cur && idSet.has(nb) && !visited.has(nb)) queue.push(nb);
      });
    }
    result.push(comp);
  }
  return result;
}

function sortCellsGeographically(cellIds: string[]): string[] {
  if (cellIds.length <= 1) return [...cellIds];
  type Pos = { lat: number; lon: number };
  const pos = new Map<string, Pos>(cellIds.map(id => {
    const [lat, lon] = cellToLatLng(id);
    return [id, { lat, lon }];
  }));
  let current = cellIds.reduce((best, id) => pos.get(id)!.lat > pos.get(best)!.lat ? id : best);
  const sorted = [current];
  const rem = new Set(cellIds.filter(id => id !== current));
  while (rem.size > 0) {
    const { lat: cLat, lon: cLon } = pos.get(current)!;
    let nearest = "", nearDist = Infinity;
    for (const id of rem) {
      const { lat, lon } = pos.get(id)!;
      const d = haversineKm(cLat, cLon, lat, lon);
      if (d < nearDist) { nearDist = d; nearest = id; }
    }
    sorted.push(nearest);
    rem.delete(nearest);
    current = nearest;
  }
  return sorted;
}

function buildCells(cellMap: Map<string, MeshNode>): Record<string, GameCell> {
  const occupied = new Set<string>(cellMap.keys());
  const cells: Record<string, GameCell> = {};

  for (const [h3, node] of cellMap) {
    cells[h3] = {
      h3Index:      h3,
      owner:        "player",
      troops:       0,
      nodeId:       node.node_id,
      nodeName:     node.long_name ?? node.short_name ?? node.node_id,
      isNodeActive: (node.last_seen_mins_ago ?? 999) < 30,
      isSynthetic:  false,
      isProduction: (node.snr_from_bbs ?? -999) >= PROD_SNR_MIN,
    };
  }

  const components = findComponents([...cellMap.keys()]);

  // 2-cell islands → add 1 synthetic
  for (const comp of components.filter(c => c.length === 2)) {
    const [idA, idB] = comp;
    const islandSet  = new Set(comp);
    const nbA = gridDisk(idA, 1).filter(nb => nb !== idA && !occupied.has(nb));
    const nbB = gridDisk(idB, 1).filter(nb => nb !== idB && !occupied.has(nb));
    const shared = nbA.filter(nb => nbB.includes(nb));
    const safe   = (l: string[]) => l.filter(nb => !gridDisk(nb, 1).some(nn => nn in cells && !islandSet.has(nn)));
    const cand   = safe(shared)[0] ?? shared[0] ?? safe(nbA)[0] ?? nbA[0] ?? safe(nbB)[0] ?? nbB[0];
    if (!cand) continue;
    const [cLat, cLon] = cellToLatLng(cand);
    const [aLat, aLon] = cellToLatLng(idA);
    const [bLat, bLon] = cellToLatLng(idB);
    const anchorId = haversineKm(cLat, cLon, aLat, aLon) <= haversineKm(cLat, cLon, bLat, bLon) ? idA : idB;
    const name     = (cellMap.get(anchorId)!.long_name ?? cellMap.get(anchorId)!.short_name ?? anchorId);
    occupied.add(cand);
    cells[cand] = {
      h3Index: cand, owner: "player", troops: 0,
      nodeName: `${name} - ${bearingToDir(bearingDeg(aLat, aLon, cLat, cLon))}`,
      isNodeActive: false, isSynthetic: true, isProduction: false,
    };
  }

  // 1-cell islands → add 2 synthetics (compact triangle)
  for (const [nodeId] of components.filter(c => c.length === 1)) {
    const node       = cellMap.get(nodeId)!;
    const anchorName = node.long_name ?? node.short_name ?? node.node_id;
    const [cLat, cLon] = cellToLatLng(nodeId);
    const ring = gridDisk(nodeId, 1)
      .filter(nb => nb !== nodeId)
      .sort((a, b) => {
        const [aLat, aLon] = cellToLatLng(a), [bLat, bLon] = cellToLatLng(b);
        return bearingDeg(cLat, cLon, aLat, aLon) - bearingDeg(cLat, cLon, bLat, bLon);
      });
    let pair: [string, string] | null = null;
    for (let i = 0; i < 6; i++) {
      const a = ring[i], b = ring[(i + 1) % 6];
      if (!occupied.has(a) && !occupied.has(b)) { pair = [a, b]; break; }
    }
    if (!pair) {
      const free = ring.filter(nb => !occupied.has(nb));
      if (free.length >= 2) pair = [free[0], free[1]];
      else if (free.length === 1) pair = [free[0], free[0]];
    }
    if (!pair) continue;
    const usedDirs = new Set<string>();
    for (const nb of pair) {
      const [nLat, nLon] = cellToLatLng(nb);
      let dir = bearingToDir(bearingDeg(cLat, cLon, nLat, nLon));
      if (usedDirs.has(dir)) dir += " 2";
      usedDirs.add(dir);
      occupied.add(nb);
      cells[nb] = {
        h3Index: nb, owner: "player", troops: 0,
        nodeName: `${anchorName} - ${dir}`,
        isNodeActive: false, isSynthetic: true, isProduction: false,
      };
    }
  }
  return cells;
}

function buildMST(cells: Record<string, GameCell>): Bridge[] {
  const ids = Object.keys(cells);
  if (ids.length <= 1) return [];
  const parent: Record<string, string> = Object.fromEntries(ids.map(id => [id, id]));
  function find(x: string): string {
    if (parent[x] !== x) parent[x] = find(parent[x]); return parent[x];
  }
  function union(x: string, y: string): boolean {
    const px = find(x), py = find(y);
    if (px === py) return false; parent[px] = py; return true;
  }
  for (const id of ids)
    for (const nb of gridDisk(id, 1))
      if (nb !== id && nb in cells) union(id, nb);
  const edges = [] as { a: string; b: string; km: number }[];
  for (let i = 0; i < ids.length; i++)
    for (let j = i + 1; j < ids.length; j++) {
      const [la, lo] = cellToLatLng(ids[i]), [lb, ll] = cellToLatLng(ids[j]);
      edges.push({ a: ids[i], b: ids[j], km: haversineKm(la, lo, lb, ll) });
    }
  edges.sort((a, b) => a.km - b.km);
  const bridges: Bridge[] = [];
  for (const { a, b } of edges) if (union(a, b)) bridges.push({ fromH3: a, toH3: b });
  return bridges;
}

function buildSemanticBridges(cells: Record<string, GameCell>, mst: Bridge[]): Bridge[] {
  const has = (name: string, pat: string) => name.toLowerCase().includes(pat.toLowerCase());
  const cerroCells = Object.values(cells).filter(c => has(c.nodeName, "cerro") && !c.isSynthetic);
  if (!cerroCells.length) return [];
  const linked = (a: string, b: string) =>
    mst.some(br => (br.fromH3 === a && br.toH3 === b) || (br.fromH3 === b && br.toH3 === a));
  const targets = [
    Object.values(cells).find(c => has(c.nodeName, "dorrego")),
    Object.values(cells).find(c => has(c.nodeName, "hermoso")),
    Object.values(cells).find(c => has(c.nodeName, "lu9adx")),
  ].filter(Boolean) as GameCell[];
  return targets.flatMap(target => {
    const [tLat, tLon] = cellToLatLng(target.h3Index);
    const closest = cerroCells.reduce((best, c) => {
      const [cLat, cLon] = cellToLatLng(c.h3Index), [bLat, bLon] = cellToLatLng(best.h3Index);
      return haversineKm(tLat, tLon, cLat, cLon) < haversineKm(tLat, tLon, bLat, bLon) ? c : best;
    });
    return linked(closest.h3Index, target.h3Index) ? [] : [{ fromH3: closest.h3Index, toH3: target.h3Index }];
  });
}

// ── game helpers ──────────────────────────────────────────────────────────────

function cellNeighbors(h3: string, cells: Record<string, GameCell>, bridges: Bridge[]): string[] {
  const h3nb = gridDisk(h3, 1).filter(id => id !== h3 && id in cells);
  const brnb = bridges.filter(b => b.fromH3 === h3 || b.toH3 === h3).map(b => b.fromH3 === h3 ? b.toH3 : b.fromH3);
  return [...new Set([...h3nb, ...brnb])];
}

function countControlledIslands(islands: string[][], cells: Record<string, GameCell>, faction: Faction): number {
  return islands.filter(isl => isl.every(id => cells[id]?.owner === faction)).length;
}

function calcReinf(cells: Record<string, GameCell>, islands: string[][], faction: Faction): number {
  const owned      = Object.values(cells).filter(c => c.owner === faction);
  const base       = Math.max(3, Math.floor(owned.length / 3));
  const activeBon  = owned.filter(c => c.isNodeActive).length;
  const islandBon  = countControlledIslands(islands, cells, faction) * 2;
  return base + activeBon + islandBon;
}

function checkWinner(cells: Record<string, GameCell>): Faction | null {
  const all = Object.values(cells);
  if (!all.length) return null;
  for (const f of ["player", "ai1", "ai2", "ai3"] as Faction[])
    if (all.every(c => c.owner === f)) return f;
  return null;
}

/** Pre-computes every round without applying to state. Returns the full sequence of troop counts. */

function applyProduction(cells: Record<string, GameCell>): Record<string, GameCell> {
  const next = { ...cells };
  for (const [id, cell] of Object.entries(cells))
    if (cell.isProduction)
      next[id] = { ...cell, troops: Math.min(PROD_CAP, cell.troops * 2) };
  return next;
}


// ── AI event queue ────────────────────────────────────────────────────────────

function planAITurns(
  cells:   Record<string, GameCell>,
  bridges: Bridge[],
  islands: string[][],
  newTurn: number,
): { events: AIEvent[]; finalCells: Record<string, GameCell>; winner: Faction | null; reinf: number } {
  const events: AIEvent[] = [];
  let c = { ...cells };

  for (const faction of AI_FACTIONS) {
    events.push({ type: "faction_start", faction });

    // Census
    const ownedIds = Object.values(c).filter(x => x.owner === faction).map(x => x.h3Index);
    events.push({ type: "census_start", faction, cellIds: sortCellsGeographically(ownedIds) });

    // Reinforcements — one event per troop
    const owned  = Object.values(c).filter(x => x.owner === faction);
    const border = owned.filter(x => cellNeighbors(x.h3Index, c, bridges).some(id => c[id].owner !== faction));
    const pool   = border.length > 0 ? border : owned;
    let reinf    = calcReinf(c, islands, faction);
    while (reinf-- > 0 && pool.length > 0) {
      const t = pool[Math.floor(Math.random() * pool.length)];
      c = { ...c, [t.h3Index]: { ...c[t.h3Index], troops: c[t.h3Index].troops + 1 } };
      events.push({ type: "reinforce", cellId: t.h3Index, troops: c[t.h3Index].troops });
    }

    // Attacks (up to 3)
    for (let i = 0; i < 3; i++) {
      const opts = Object.values(c)
        .filter(x => x.owner === faction && x.troops >= 2)
        .flatMap(x => cellNeighbors(x.h3Index, c, bridges)
          .filter(id => c[id].owner !== faction)
          .map(id => ({ from: x.h3Index, to: id, adv: x.troops - c[id].troops })))
        .sort((a, b) => b.adv - a.adv);
      if (!opts.length || opts[0].adv < 0) break;

      const { from, to } = opts[0];
      events.push({ type: "attack_announce", fromId: from, toId: to });

      let atk = c[from].troops, def = c[to].troops;
      while (atk > 1 && def > 0) {
        const atkRoll = Math.floor(Math.random() * 6) + 1;
        const defRoll = Math.floor(Math.random() * 6) + 1;
        if (atkRoll > defRoll) def--; else atk--;
        events.push({ type: "attack_round", fromId: from, toId: to, fromTroops: atk, toTroops: def, atkRoll, defRoll });
      }

      const won = def === 0;
      c = won
        ? { ...c, [from]: { ...c[from], troops: 1 }, [to]: { ...c[to], owner: faction, troops: Math.max(1, atk - 1) } }
        : { ...c, [from]: { ...c[from], troops: atk } };
      events.push({ type: "attack_result", fromId: from, toId: to, won, fromTroops: c[from].troops, toTroops: c[to].troops, newOwner: won ? faction : null });
    }
  }

  const finalCells = newTurn % PROD_INTERVAL === 0 ? applyProduction(c) : c;
  const winner     = checkWinner(finalCells);
  const reinf      = calcReinf(finalCells, islands, "player");
  events.push({ type: "done" });

  return { events, finalCells, winner, reinf };
}

// ── sound ─────────────────────────────────────────────────────────────────────

const audioCtx = { current: null as AudioContext | null };
function getCtx(): AudioContext {
  if (!audioCtx.current) audioCtx.current = new AudioContext();
  return audioCtx.current;
}

function playTone(freq: number, duration: number, type: OscillatorType = "square", vol = 0.15) {
  try {
    const ctx  = getCtx();
    const osc  = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);
    osc.type = type; osc.frequency.setValueAtTime(freq, ctx.currentTime);
    gain.gain.setValueAtTime(vol, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duration);
    osc.start(ctx.currentTime); osc.stop(ctx.currentTime + duration);
  } catch { /* ignore */ }
}

const SFX = {
  select:   () => playTone(440, 0.08, "square", 0.1),
  attack:   () => { playTone(200, 0.15, "sawtooth", 0.2); setTimeout(() => playTone(150, 0.2, "sawtooth", 0.2), 100); },
  win:      () => { [523, 659, 784, 1047].forEach((f, i) => setTimeout(() => playTone(f, 0.2, "square", 0.12), i * 120)); },
  lose:     () => { [300, 250, 200].forEach((f, i) => setTimeout(() => playTone(f, 0.2, "sawtooth", 0.15), i * 100)); },
  conquest: () => { [392, 494, 587, 784].forEach((f, i) => setTimeout(() => playTone(f, 0.15, "square", 0.13), i * 80)); },
  pass:     () => playTone(330, 0.1, "sine", 0.08),
  move:     () => { playTone(550, 0.08, "sine", 0.1); setTimeout(() => playTone(660, 0.08, "sine", 0.1), 90); },
};

function troopMarkerHtml(troops: number, isProduction: boolean): string {
  return `<div style="background:rgba(0,0,0,0.6);color:#fff;font-size:13px;font-weight:700;font-family:monospace;width:26px;height:26px;border-radius:50%;display:flex;align-items:center;justify-content:center;border:2px solid rgba(255,255,255,0.55);pointer-events:none;transform:translate(-50%,-50%);">${troops}${isProduction ? '<span style="color:#ffd700;font-size:8px;position:absolute;top:-2px;right:-2px">★</span>' : ""}</div>`;
}

function censusDuration(n: number): number {
  const step = Math.min(120, 1800 / Math.max(1, n));
  return Math.ceil((n - 1) * step + 300 + 400);
}

function runCensusFlash(
  cellIds: string[],
  polygons: Map<string, L.Polygon>,
  map: L.Map | null,
): void {
  if (!cellIds.length) return;
  const STEP     = Math.min(120, 1800 / cellIds.length);
  const BLINK_ON = 60;
  const BLINKS   = 3;

  cellIds.forEach((id, i) => {
    const poly = polygons.get(id);
    if (!poly) return;

    const saved = {
      fillColor:   (poly.options as any).fillColor  as string,
      fillOpacity: (poly.options as any).fillOpacity as number,
      color:       (poly.options as any).color       as string,
      weight:      (poly.options as any).weight      as number,
    };

    if (i % 4 === 0 && map) {
      setTimeout(() => {
        const [lat, lon] = cellToLatLng(id);
        map.panTo([lat, lon], { animate: true, duration: 0.15 });
      }, Math.floor(i * STEP));
    }

    for (let b = 0; b < BLINKS; b++) {
      const t0 = Math.floor(i * STEP) + b * BLINK_ON * 2;
      setTimeout(() => {
        poly.setStyle({ fillColor: "#ffffff", fillOpacity: 0.92, color: "#ffffff", weight: 2.5 });
        playTone(880 + b * 110, 0.045, "sine", 0.11);
      }, t0);
      setTimeout(() => {
        poly.setStyle(saved);
      }, t0 + BLINK_ON);
    }
  });
}

// ── init ──────────────────────────────────────────────────────────────────────

function initGame(nodes: MeshNode[]): GameState {
  const active = nodes.filter(n =>
    n.lat != null && n.lon != null && (n.last_seen_mins_ago ?? 999) <= ACTIVE_MINS
  );
  if (!active.length) return {
    cells: {}, bridges: [], islands: [], phase: "action", turn: 1,
    reinforcementsLeft: 0, attackTarget: null, attackSource: null, moveSource: null, postConquestTroops: 0,
    winner: null, currentFaction: "player", combatMsg: "", aiQueue: [], aiMsg: "", log: ["Sin nodos activos con GPS."],
  };

  const cellMap = new Map<string, MeshNode>();
  for (const node of active) {
    const cid  = latLngToCell(node.lat!, node.lon!, H3_RES);
    const prev = cellMap.get(cid);
    if (!prev || (node.snr_from_bbs ?? -999) > (prev.snr_from_bbs ?? -999)) cellMap.set(cid, node);
  }

  const cells    = buildCells(cellMap);
  const shuffled = Object.keys(cells).sort(() => Math.random() - 0.5);
  const perF     = Math.ceil(shuffled.length / 4);
  const order: Faction[] = ["player", "ai1", "ai2", "ai3"];
  shuffled.forEach((id, i) => {
    cells[id].owner  = order[Math.min(Math.floor(i / perF), 3)];
    cells[id].troops = Math.floor(Math.random() * 3) + 2;
  });

  const mst     = buildMST(cells);
  const bridges = [...mst, ...buildSemanticBridges(cells, mst)];
  const islands = findComponents(Object.keys(cells));
  const reinf   = calcReinf(cells, islands, "player");

  return {
    cells, bridges, islands,
    phase: "reinforcement", turn: 1, reinforcementsLeft: reinf,
    attackTarget: null, attackSource: null, moveSource: null, postConquestTroops: 0,
    winner: null, currentFaction: "player", combatMsg: "", aiQueue: [], aiMsg: "",
    log: [`Turno 1 — +${reinf} tropas · ${Object.keys(cells).length} territorios`],
  };
}

// ── helpers for rendering ─────────────────────────────────────────────────────

function statusText(gs: GameState): [string, string] {
  switch (gs.phase) {
    case "reinforcement":  return [`COLOCÁ TROPAS (${gs.reinforcementsLeft} RESTANTES)`, "HACÉ CLIC EN TUS TERRITORIOS"];
    case "action":         return ["TU TURNO", "ELEGÍ UNA ACCIÓN"];
    case "attack_target":  return ["¿A QUÉ TERRITORIO ATACAR?", "SELECCIONÁ CON CLIC"];
    case "attack_source":  return [`ATACAR ${gs.cells[gs.attackTarget!]?.nodeName ?? "?"}`, "¿DESDE DÓNDE? SELECCIONÁ"];
    case "attack_confirm": return [
      `⚔  ${gs.cells[gs.attackSource!]?.troops ?? "?"} vs ${gs.cells[gs.attackTarget!]?.troops ?? "?"}`,
      "ATACAR O RETIRARSE",
    ];
    case "post_conquest": {
      const max = (gs.cells[gs.attackSource!]?.troops ?? 2) - 1;
      return [`MOVER TROPAS: ${gs.postConquestTroops}`, `MÍN: 1  MÁX: ${max}`];
    }
    case "move_source":    return ["¿DESDE QUÉ TERRITORIO MOVER?", "SELECCIONÁ CON CLIC"];
    case "move_target":    return [`MOVER DESDE ${gs.cells[gs.moveSource!]?.nodeName ?? "?"}`, "¿HACIA DÓNDE? SELECCIONÁ"];
    case "ai_turn":        return [gs.aiMsg || "IA jugando...", ""];
    case "game_over":      return ["FIN DE PARTIDA", gs.winner === "player" ? "¡VICTORIA!" : `GANÓ ${NAMES[gs.winner!]}`];
    default:               return ["", ""];
  }
}

// ── component ─────────────────────────────────────────────────────────────────

export default function MeshWarsView() {
  const [gs, setGs]            = useState<GameState | null>(null);
  const [nodesReady, setReady] = useState(false);
  const nodesRef               = useRef<MeshNode[]>([]);
  const mapRef                 = useRef<L.Map | null>(null);
  const containerRef           = useRef<HTMLDivElement>(null);
  const polygonsRef            = useRef<Map<string, L.Polygon>>(new Map());
  const troopMarkersRef        = useRef<Map<string, L.Marker>>(new Map());
  const bridgeLinesRef         = useRef<L.Polyline[]>([]);
  const gsRef                  = useRef<GameState | null>(null);
  const fittedRef              = useRef(false);
  const aiTimerRef             = useRef<ReturnType<typeof setTimeout> | null>(null);
  const aiFinalRef             = useRef<{ cells: Record<string, GameCell>; turn: number; reinf: number; winner: Faction | null } | null>(null);
  const lastCensusTurnRef      = useRef(-1);
  gsRef.current = gs;

  // AI animation loop — fires each time the queue shrinks
  useEffect(() => {
    if (gs?.phase !== "ai_turn" || !gs.aiQueue.length) return;
    if (aiTimerRef.current) clearTimeout(aiTimerRef.current);

    const event = gs.aiQueue[0];

    // Immediate side effects (sound + map pan)
    if (event.type === "attack_round") SFX.attack();
    if (event.type === "attack_result") event.won ? SFX.conquest() : SFX.lose();
    if (event.type === "reinforce") {
      if (mapRef.current) {
        const [lat, lon] = cellToLatLng(event.cellId);
        mapRef.current.flyTo([lat, lon], mapRef.current.getZoom(), { animate: true, duration: 0.75 });
      }
      // Wait for flyTo to finish + small pause, then show the reinforcement
      const cellId = event.cellId;
      const newTroops = event.troops;
      setTimeout(() => {
        playTone(660, 0.06, "sine", 0.08);
        const cell = gsRef.current?.cells[cellId];
        const marker = troopMarkersRef.current.get(cellId);
        if (marker && cell)
          marker.setIcon(L.divIcon({ className: "", html: troopMarkerHtml(newTroops, cell.isProduction), iconSize: [0, 0], iconAnchor: [0, 0] }));
      }, 950); // 750ms flyTo + 200ms pause
    }
    if (event.type === "census_start" && mapRef.current)
      runCensusFlash(event.cellIds, polygonsRef.current, mapRef.current);
    if (event.type === "attack_announce" && mapRef.current) {
      const [lat, lon] = cellToLatLng(event.fromId);
      mapRef.current.flyTo([lat, lon], mapRef.current.getZoom(), { animate: true, duration: 0.35 });
    }

    let delay = 400;
    if (event.type === "faction_start")        delay = 1200;
    else if (event.type === "census_start")    delay = censusDuration(event.cellIds.length);
    else if (event.type === "reinforce")       delay = 1400;
    else if (event.type === "attack_announce") delay = 1100;
    else if (event.type === "attack_result")   delay = 700;

    aiTimerRef.current = setTimeout(() => {
      setGs(prev => {
        if (!prev || prev.phase !== "ai_turn" || !prev.aiQueue.length) return prev;
        const [evt, ...rest] = prev.aiQueue;
        const base = { ...prev, aiQueue: rest };

        switch (evt.type) {
          case "faction_start":
            return { ...base, currentFaction: evt.faction, aiMsg: `${NAMES[evt.faction]} está jugando...`, combatMsg: "", attackSource: null, attackTarget: null };

          case "census_start":
            return { ...base, aiMsg: `${NAMES[evt.faction]}: ${evt.cellIds.length} territorios`, combatMsg: "" };

          case "reinforce": {
            const cell = prev.cells[evt.cellId];
            return { ...base, cells: { ...prev.cells, [evt.cellId]: { ...cell, troops: evt.troops } }, aiMsg: `${NAMES[cell.owner]}: refuerza ${cell.nodeName}` };
          }

          case "attack_announce": {
            const atker = prev.cells[evt.fromId], defer = prev.cells[evt.toId];
            return { ...base, attackSource: evt.fromId, attackTarget: evt.toId, aiMsg: `${NAMES[atker.owner]}: ataca ${defer.nodeName}`, combatMsg: `${atker.troops} vs ${defer.troops}` };
          }

          case "attack_round":
            return {
              ...base,
              cells: { ...prev.cells, [evt.fromId]: { ...prev.cells[evt.fromId], troops: evt.fromTroops }, [evt.toId]: { ...prev.cells[evt.toId], troops: evt.toTroops } },
              combatMsg: `Ataque: ${evt.atkRoll}  Defensa: ${evt.defRoll}`,
            };

          case "attack_result": {
            const nc = { ...prev.cells };
            if (evt.won && evt.newOwner) {
              nc[evt.fromId] = { ...prev.cells[evt.fromId], troops: evt.fromTroops };
              nc[evt.toId]   = { ...prev.cells[evt.toId], owner: evt.newOwner, troops: evt.toTroops };
            } else {
              nc[evt.fromId] = { ...prev.cells[evt.fromId], troops: evt.fromTroops };
            }
            return { ...base, cells: nc, attackSource: null, attackTarget: null, combatMsg: evt.won ? "¡CONQUISTA!" : "Ataque fallido" };
          }

          case "done": {
            const f = aiFinalRef.current!;
            return {
              ...base, cells: f.cells, phase: f.winner ? "game_over" : "reinforcement",
              turn: f.turn, reinforcementsLeft: f.reinf, winner: f.winner,
              currentFaction: f.winner ?? "player",
              aiQueue: [], aiMsg: "", attackSource: null, attackTarget: null,
              combatMsg: f.winner ? `¡${NAMES[f.winner]} dominó la Mesh!` : "",
              log: [`TURNO ${f.turn} — +${f.reinf} TROPAS`, ...prev.log.slice(0, 9)],
            };
          }

          default: return base;
        }
      });
    }, delay);

    return () => { if (aiTimerRef.current) clearTimeout(aiTimerRef.current); };
  }, [gs?.phase, gs?.aiQueue?.length]);

  useEffect(() => {
    fetchGraph().then(d => { nodesRef.current = d.nodes; setReady(true); });
  }, []);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    mapRef.current = L.map(containerRef.current, { center: [-38.72, -62.26], zoom: 12 });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap", maxZoom: 19,
    }).addTo(mapRef.current);
    return () => { mapRef.current?.remove(); mapRef.current = null; };
  }, []);

  useEffect(() => {
    if (!mapRef.current || !gs) return;
    const map = mapRef.current;

    polygonsRef.current.forEach(p => p.remove()); polygonsRef.current.clear();
    troopMarkersRef.current.forEach(m => m.remove()); troopMarkersRef.current.clear();
    bridgeLinesRef.current.forEach(l => l.remove()); bridgeLinesRef.current = [];

    // Compute highlight sets
    const validTargets  = new Set<string>();
    const validSources  = new Set<string>();

    if (gs.phase === "attack_target") {
      Object.values(gs.cells).forEach(c => {
        if (c.owner !== "player") return;
        if (c.troops < 2) return;
        cellNeighbors(c.h3Index, gs.cells, gs.bridges).forEach(nb => {
          if (gs.cells[nb]?.owner !== "player") validTargets.add(nb);
        });
      });
    }
    if (gs.phase === "attack_source" && gs.attackTarget) {
      cellNeighbors(gs.attackTarget, gs.cells, gs.bridges).forEach(nb => {
        if (gs.cells[nb]?.owner === "player" && gs.cells[nb].troops >= 2) validSources.add(nb);
      });
    }
    if (gs.phase === "move_source") {
      Object.values(gs.cells).forEach(c => { if (c.owner === "player" && c.troops > 1) validSources.add(c.h3Index); });
    }
    if (gs.phase === "move_target" && gs.moveSource) {
      cellNeighbors(gs.moveSource, gs.cells, gs.bridges).forEach(nb => {
        if (gs.cells[nb]?.owner === "player" && nb !== gs.moveSource) validSources.add(nb);
      });
    }

    for (const cell of Object.values(gs.cells)) {
      const bnd      = cellToBoundary(cell.h3Index).map(([la, lo]) => [la, lo] as [number, number]);
      const color    = COLORS[cell.owner];
      const isAtk    = gs.attackTarget === cell.h3Index;
      const isSrc    = gs.attackSource === cell.h3Index || gs.moveSource === cell.h3Index;
      const isVT     = validTargets.has(cell.h3Index);
      const isVS     = validSources.has(cell.h3Index);

      const fillOp   = isAtk ? 0.85 : isSrc ? 0.85 : isVT ? 0.6 : isVS ? 0.65 : cell.isSynthetic ? 0.28 : 0.45;
      const border   = isAtk ? "#ffeb3b" : isSrc ? "#ffffff" : isVT ? "#ffeb3b" : isVS ? "#ffffff" : color;
      const bWeight  = isAtk || isSrc || isVT || isVS ? 2.5 : cell.isSynthetic ? 1 : 1.5;
      const dashArr  = cell.isSynthetic ? "4 3" : undefined;

      const poly = L.polygon(bnd, { fillColor: color, fillOpacity: fillOp, color: border, weight: bWeight, dashArray: dashArr });

      poly.bindTooltip(
        `<b style="color:${color}">${NAMES[cell.owner]}</b> · ${cell.troops} tropas<br>` +
        `${cell.isProduction ? "★" : cell.isSynthetic ? "◈" : "📡"} ${cell.nodeName}` +
        (cell.isNodeActive ? ` <span style="color:#4caf50">●</span>` : ""),
        { direction: "top", sticky: false, offset: [0, -12], className: "mesh-tooltip" },
      );

      const cellId = cell.h3Index;
      poly.on("click", () => {
        const state = gsRef.current;
        if (!state || state.phase === "game_over" || state.phase === "action") return;
        const cur = state.cells[cellId];

        if (state.phase === "reinforcement") {
          if (cur.owner !== "player" || state.reinforcementsLeft <= 0) return;
          playTone(660, 0.07, "sine", 0.12);
          setGs(prev => {
            if (!prev) return prev;
            const left = prev.reinforcementsLeft - 1;
            return {
              ...prev,
              cells: { ...prev.cells, [cellId]: { ...prev.cells[cellId], troops: prev.cells[cellId].troops + 1 } },
              reinforcementsLeft: left,
              phase: left === 0 ? "action" : "reinforcement",
              combatMsg: left === 0 ? "¡LISTO PARA ATACAR!" : "",
            };
          });
          return;
        }

        if (state.phase === "attack_target") {
          if (cur.owner === "player") return;
          const reachable = cellNeighbors(cellId, state.cells, state.bridges)
            .some(id => state.cells[id]?.owner === "player" && state.cells[id].troops >= 2);
          if (!reachable) return;
          SFX.select();
          setGs(prev => prev ? { ...prev, attackTarget: cellId, phase: "attack_source", combatMsg: "" } : prev);
          return;
        }

        if (state.phase === "attack_source") {
          if (cur.owner !== "player" || cur.troops < 2) return;
          if (!cellNeighbors(state.attackTarget!, state.cells, state.bridges).includes(cellId)) return;
          SFX.select();
          setGs(prev => prev ? {
            ...prev, attackSource: cellId, phase: "attack_confirm",
            combatMsg: `${prev.cells[cellId].troops} CONTRA ${prev.cells[prev.attackTarget!].troops}`,
          } : prev);
          return;
        }

        if (state.phase === "move_source") {
          if (cur.owner !== "player" || cur.troops <= 1) return;
          setGs(prev => prev ? { ...prev, moveSource: cellId, phase: "move_target", combatMsg: "" } : prev);
          return;
        }

        if (state.phase === "move_target") {
          if (cur.owner !== "player" || cellId === state.moveSource) return;
          if (!cellNeighbors(state.moveSource!, state.cells, state.bridges).includes(cellId)) return;
          SFX.move();
          setGs(prev => {
            if (!prev?.moveSource) return prev;
            const src = prev.cells[prev.moveSource], dst = prev.cells[cellId];
            const moving = src.troops - 1;
            return {
              ...prev,
              cells: {
                ...prev.cells,
                [prev.moveSource]: { ...src, troops: 1 },
                [cellId]: { ...dst, troops: dst.troops + moving },
              },
              moveSource: null, phase: "action",
              combatMsg: `MOVIÓ ${moving} TROPAS`,
            };
          });
          return;
        }
      });

      poly.addTo(map);
      polygonsRef.current.set(cell.h3Index, poly);

      // Troop count marker
      const [cLat, cLon] = cellToLatLng(cell.h3Index);
      const tm = L.marker([cLat, cLon], {
        icon: L.divIcon({ className: "", html: troopMarkerHtml(cell.troops, cell.isProduction), iconSize: [0, 0], iconAnchor: [0, 0] }),
        interactive: false, zIndexOffset: 500,
      });
      tm.addTo(map);
      troopMarkersRef.current.set(cell.h3Index, tm);
    }

    // Bridges
    for (const bridge of gs.bridges) {
      const [la, lo] = cellToLatLng(bridge.fromH3), [lb, ll] = cellToLatLng(bridge.toH3);
      const lit = gs.attackSource === bridge.fromH3 || gs.attackSource === bridge.toH3
               || gs.attackTarget === bridge.fromH3 || gs.attackTarget === bridge.toH3
               || gs.moveSource   === bridge.fromH3 || gs.moveSource   === bridge.toH3;
      const line = L.polyline([[la, lo], [lb, ll]], {
        color: lit ? "#ffeb3b" : "#000", weight: lit ? 6 : 4, opacity: lit ? 1 : 0.85,
      });
      line.addTo(map);
      bridgeLinesRef.current.push(line);
    }

    if (!fittedRef.current && Object.keys(gs.cells).length > 0) {
      fittedRef.current = true;
      map.fitBounds(L.latLngBounds(Object.keys(gs.cells).map(id => cellToLatLng(id) as [number, number])), { padding: [40, 40] });
    }
  }, [gs]);

  // Player census: flash all player cells at the start of each player turn
  useEffect(() => {
    if (!gs || gs.phase !== "reinforcement") return;
    if (gs.turn === lastCensusTurnRef.current) return;
    if (!polygonsRef.current.size) return;
    lastCensusTurnRef.current = gs.turn;
    const ids = Object.values(gs.cells).filter(c => c.owner === "player").map(c => c.h3Index);
    if (!ids.length) return;
    runCensusFlash(sortCellsGeographically(ids), polygonsRef.current, mapRef.current);
  }, [gs?.phase, gs?.turn]);

  // ── actions ──────────────────────────────────────────────────────────────────

  function handleButton(action: "attack" | "attack_confirm" | "troop_plus" | "troop_minus" | "troop_confirm" | "move" | "pass" | "skip_ai" | "cancel") {
    // One manual round of combat per click
    if (action === "attack_confirm") {
      SFX.attack();
      setGs(prev => {
        if (!prev || !prev.attackSource || !prev.attackTarget) return prev;
        const from = prev.cells[prev.attackSource];
        const to   = prev.cells[prev.attackTarget];

        const d6 = () => Math.floor(Math.random() * 6) + 1;
        const atkRoll = d6();
        const defRoll = d6();
        const roundMsg = `Ataque: ${atkRoll}  Defensa: ${defRoll}`;

        const next = { ...prev.cells };

        if (atkRoll > defRoll) {
          // Attacker wins this round
          const newDef = to.troops - 1;
          next[prev.attackTarget] = { ...to, troops: newDef };

          if (newDef === 0) {
            SFX.conquest();
            if (from.troops <= 2) {
              // Only one possible move (1 to conquered, 1 stays) — auto-confirm
              next[prev.attackSource] = { ...from, troops: 1 };
              next[prev.attackTarget] = { ...to, owner: from.owner, troops: 1 };
              const winner = checkWinner(next);
              if (winner) setTimeout(() => SFX.win(), 50);
              return {
                ...prev, cells: next, winner,
                phase: winner ? "game_over" : "action",
                attackTarget: null, attackSource: null, postConquestTroops: 0,
                combatMsg: `¡CONQUISTA! — ${roundMsg}`,
                log: [`¡CONQUISTA! +1 tropa`, ...prev.log.slice(0, 9)],
              };
            }
            // More troops — let player choose how many to move
            next[prev.attackTarget] = { ...to, owner: from.owner, troops: 0 };
            return {
              ...prev, cells: next,
              phase: "post_conquest",
              postConquestTroops: 1,
              combatMsg: `¡CONQUISTA! — ${roundMsg}`,
            };
          }
          return { ...prev, cells: next, combatMsg: `${roundMsg} → defensor pierde 1` };

        } else {
          // Defender wins this round
          const newAtk = from.troops - 1;
          next[prev.attackSource] = { ...from, troops: newAtk };

          if (newAtk <= 1) {
            // Attacker forced to retreat
            setTimeout(() => SFX.lose(), 50);
            return {
              ...prev, cells: next,
              phase: "action", attackTarget: null, attackSource: null,
              combatMsg: `RETIRADA FORZADA — ${roundMsg}`,
              log: [`ATAQUE FALLIDO (${from.troops} vs ${to.troops})`, ...prev.log.slice(0, 9)],
            };
          }
          return { ...prev, cells: next, combatMsg: `${roundMsg} → atacante pierde 1` };
        }
      });
      return;
    }

    setGs(prev => {
      if (!prev) return prev;

      if (action === "troop_plus" && prev.phase === "post_conquest") {
        const max = (prev.cells[prev.attackSource!]?.troops ?? 2) - 1;
        return { ...prev, postConquestTroops: Math.min(prev.postConquestTroops + 1, max) };
      }
      if (action === "troop_minus" && prev.phase === "post_conquest") {
        return { ...prev, postConquestTroops: Math.max(prev.postConquestTroops - 1, 1) };
      }
      if (action === "troop_confirm" && prev.phase === "post_conquest") {
        const from = prev.cells[prev.attackSource!];
        const to   = prev.cells[prev.attackTarget!];
        const n    = prev.postConquestTroops;
        const next = {
          ...prev.cells,
          [prev.attackSource!]: { ...from, troops: from.troops - n },
          [prev.attackTarget!]: { ...to,   troops: n },
        };
        const winner = checkWinner(next);
        if (winner) setTimeout(() => SFX.win(), 50);
        return {
          ...prev, cells: next, winner,
          phase: winner ? "game_over" : "action",
          attackTarget: null, attackSource: null, postConquestTroops: 0,
          combatMsg: `MOVIÓ ${n} TROPAS`,
          log: [`¡CONQUISTA! +${n} tropas`, ...prev.log.slice(0, 9)],
        };
      }

      if (action === "attack" && prev.phase === "action") {
        return { ...prev, phase: "attack_target", combatMsg: "" };
      }
      if (action === "move" && prev.phase === "action") {
        SFX.select();
        return { ...prev, phase: "move_source", combatMsg: "" };
      }
      if (action === "pass" && (prev.phase === "action" || prev.phase === "reinforcement")) {
        SFX.pass();
        const newTurn = prev.turn + 1;
        const { events, finalCells, winner, reinf } = planAITurns(prev.cells, prev.bridges, prev.islands, newTurn);
        aiFinalRef.current = { cells: finalCells, turn: newTurn, reinf, winner };
        return {
          ...prev,
          phase: "ai_turn",
          aiQueue: events,
          aiMsg: "",
          attackTarget: null, attackSource: null, moveSource: null, postConquestTroops: 0,
          combatMsg: "",
        };
      }
      if (action === "skip_ai") {
        if (aiTimerRef.current) clearTimeout(aiTimerRef.current);
        const f = aiFinalRef.current;
        if (!f) return prev;
        return {
          ...prev, cells: f.cells, phase: f.winner ? "game_over" : "reinforcement",
          turn: f.turn, reinforcementsLeft: f.reinf, winner: f.winner,
          currentFaction: f.winner ?? "player",
          aiQueue: [], aiMsg: "", attackSource: null, attackTarget: null,
          combatMsg: f.winner ? `¡${NAMES[f.winner]} dominó la Mesh!` : "",
          log: [`TURNO ${f.turn} — +${f.reinf} TROPAS`, ...prev.log.slice(0, 9)],
        };
      }
      if (action === "cancel") {
        return {
          ...prev,
          phase: ["reinforcement"].includes(prev.phase) ? "reinforcement" : "action",
          attackTarget: null, attackSource: null, moveSource: null, postConquestTroops: 0, combatMsg: "",
        };
      }
      return prev;
    });
  }

  function startGame() {
    fittedRef.current = false;
    setGs(initGame(nodesRef.current));
  }

  // ── render ────────────────────────────────────────────────────────────────────

  const factionList: Faction[] = ["player", "ai1", "ai2", "ai3"];

  const scoreRows = gs
    ? factionList.map(f => ({
        f,
        territories: Object.values(gs.cells).filter(c => c.owner === f).length,
        islands:     countControlledIslands(gs.islands, gs.cells, f),
      }))
    : [];

  const [statusLine1, statusLine2] = gs ? statusText(gs) : ["", ""];

  const btnStyle = (color: string, disabled = false): React.CSSProperties => ({
    background: disabled ? "#2a2a2a" : color,
    color: disabled ? "#555" : "#fff",
    border: `2px solid ${disabled ? "#333" : color}`,
    borderRadius: 4, padding: "10px 18px",
    fontFamily: "monospace", fontWeight: 700, fontSize: 15,
    cursor: disabled ? "not-allowed" : "pointer",
    letterSpacing: 1, minWidth: 80,
  });

  const inAction       = gs?.phase === "action";
  const inConfirm      = gs?.phase === "attack_confirm";
  const inPostConquest = gs?.phase === "post_conquest";
  const inAiTurn       = gs?.phase === "ai_turn";
  const canCancel      = gs ? !["action", "reinforcement", "post_conquest", "ai_turn", "game_over"].includes(gs.phase) : false;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "#1a2332" }}>

      {/* Map area — 80% */}
      <div style={{ flex: 4, position: "relative", minHeight: 0 }}>
        <div ref={containerRef} style={{ width: "100%", height: "100%" }} />

        {/* Score panel — top left overlay */}
        {gs && (
          <div style={{
            position: "absolute", top: 10, left: 10, zIndex: 1000,
            background: "rgba(0,0,0,0.88)", border: "1px solid #1976d2",
            padding: "6px 10px", fontFamily: "monospace", fontSize: 13,
            minWidth: 200,
          }}>
            {scoreRows.map(({ f, territories, islands }) => (
              <div key={f} style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 2 }}>
                <span style={{ color: "#ccc", width: 12 }}>
                  {f === gs.currentFaction && gs.phase !== "game_over" ? "▶" : " "}
                </span>
                <span style={{ color: COLORS[f], width: 72, fontWeight: 700 }}>{NAMES[f]}</span>
                <span style={{ color: "#fff", width: 28, textAlign: "right" }}>{territories}</span>
                <span style={{ color: "#888" }}>C&gt;</span>
                <span style={{ color: "#ffd700" }}>{islands}</span>
              </div>
            ))}
          </div>
        )}

        {/* Start screen */}
        {!gs && (
          <div style={{
            position: "absolute", inset: 0, display: "flex", flexDirection: "column",
            alignItems: "center", justifyContent: "center", background: "rgba(0,0,0,0.7)", zIndex: 2000,
          }}>
            <div style={{ fontFamily: "monospace", color: "#1976d2", fontSize: 32, fontWeight: 700, marginBottom: 8 }}>
              MESH WARS
            </div>
            <div style={{ color: "#8b949e", marginBottom: 24, fontFamily: "monospace" }}>
              {nodesReady ? "Datos de la mesh cargados" : "Cargando nodos..."}
            </div>
            {nodesReady && (
              <button style={btnStyle("#c62828")} onClick={startGame}>⚔ COMENZAR PARTIDA</button>
            )}
          </div>
        )}
      </div>

      {/* Bottom bar — 20% */}
      <div style={{
        flex: 1, display: "flex", background: "#0d1117",
        borderTop: "2px solid #30363d", minHeight: 0,
      }}>
        {/* Left: status */}
        <div style={{
          flex: 1, padding: "12px 20px", display: "flex", flexDirection: "column",
          justifyContent: "center", borderRight: "1px solid #30363d",
        }}>
          {gs ? (
            <>
              <div style={{ color: "#f0f6fc", fontFamily: "monospace", fontSize: 16, fontWeight: 700, marginBottom: 6 }}>
                {statusLine1}
              </div>
              <div style={{ color: "#8b949e", fontFamily: "monospace", fontSize: 14 }}>
                {statusLine2}
              </div>
            </>
          ) : (
            <div style={{ color: "#484f58", fontFamily: "monospace", fontSize: 16 }}>MESH WARS</div>
          )}
        </div>

        {/* Center: buttons */}
        <div style={{
          flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
          gap: 10, borderRight: "1px solid #30363d", padding: "0 12px",
        }}>
          {!gs ? null : gs.phase === "game_over" ? (
            <button style={btnStyle("#1976d2")} onClick={startGame}>NUEVA PARTIDA</button>
          ) : inAiTurn ? (
            <button
              onClick={() => handleButton("skip_ai")}
              style={{ ...btnStyle("#455a64"), fontSize: 16, padding: "12px 32px", letterSpacing: 2 }}
            >⏭ SALTAR</button>
          ) : inPostConquest ? (
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <button
                onClick={() => handleButton("troop_minus")}
                style={{ ...btnStyle("#455a64"), fontSize: 24, padding: "8px 20px", minWidth: 50 }}
              >−</button>
              <span style={{
                color: "#ffd700", fontFamily: "monospace", fontWeight: 700,
                fontSize: 32, minWidth: 48, textAlign: "center",
              }}>
                {gs!.postConquestTroops}
              </span>
              <button
                onClick={() => handleButton("troop_plus")}
                style={{ ...btnStyle("#455a64"), fontSize: 24, padding: "8px 20px", minWidth: 50 }}
              >+</button>
              <button
                onClick={() => handleButton("troop_confirm")}
                style={{
                  ...btnStyle("#c62828"), fontSize: 16, padding: "12px 24px",
                  marginLeft: 8, boxShadow: "0 0 12px #c6282866",
                }}
              >MOVER</button>
            </div>
          ) : inConfirm ? (
            <>
              <button
                onClick={() => handleButton("attack_confirm")}
                style={{
                  background: "#c62828", color: "#fff", border: "2px solid #ef5350",
                  borderRadius: 4, padding: "14px 36px", fontFamily: "monospace",
                  fontWeight: 700, fontSize: 20, cursor: "pointer", letterSpacing: 2,
                  boxShadow: "0 0 16px #c6282888",
                }}
              >⚔ ATACAR</button>
              <button
                onClick={() => handleButton("cancel")}
                style={{
                  background: "#1a2332", color: "#90a4ae", border: "2px solid #455a64",
                  borderRadius: 4, padding: "14px 36px", fontFamily: "monospace",
                  fontWeight: 700, fontSize: 20, cursor: "pointer", letterSpacing: 2,
                }}
              >↩ RETIRAR</button>
            </>
          ) : (
            <>
              <button style={btnStyle("#c62828", !inAction)} onClick={() => inAction && handleButton("attack")}>ATACAR</button>
              <button style={btnStyle("#006064", !inAction)} onClick={() => inAction && handleButton("move")}>MOVER</button>
              <button style={btnStyle("#2e7d32")} onClick={() => handleButton("pass")}>PASAR</button>
              <button style={btnStyle("#424242", true)}>CARTA</button>
              <button style={btnStyle("#f57f17", !canCancel)} onClick={() => canCancel && handleButton("cancel")}>CANCELAR</button>
            </>
          )}
        </div>

        {/* Right: combat info */}
        <div style={{
          flex: 1, padding: "12px 20px", display: "flex", flexDirection: "column",
          justifyContent: "center",
        }}>
          {gs?.combatMsg ? (
            <div style={{ color: "#ffd700", fontFamily: "monospace", fontSize: 16, fontWeight: 700, marginBottom: 6 }}>
              {gs.combatMsg}
            </div>
          ) : gs?.log[0] ? (
            <div style={{ color: "#484f58", fontFamily: "monospace", fontSize: 13 }}>
              {gs.log[0]}
            </div>
          ) : null}
          {gs?.phase === "attack_confirm" && gs.attackSource && gs.attackTarget && (
            <div style={{ color: "#90a4ae", fontFamily: "monospace", fontSize: 15, marginTop: 4 }}>
              {gs.cells[gs.attackSource].troops} CONTRA {gs.cells[gs.attackTarget].troops}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
