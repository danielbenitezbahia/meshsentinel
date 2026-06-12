import { useState, useEffect, useRef } from "react";
import meshwarsLogo from "../assets/meshwars-logo.png";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { latLngToCell, gridDisk, cellToBoundary, cellToLatLng } from "h3-js";
import { cellNeighbors, nnSort } from "../ai/utils";
import { pickReinforcementPool, pickAttack as pickFactionAttack } from "../ai/index";
import { generateEvent } from "../ai/events";
import type { GameEvent } from "../ai/events";
import { fetchGraph } from "../api";
import type { MeshNode } from "../types";

const ACTIVE_MINS    = 120;
const H3_RES         = 7;
const PROD_INTERVAL  = 5;     // every N turns, production centers double troops
const PROD_CAP       = 15;    // max troops after doubling
const INFRA_KEYWORDS = ["carpa", "3 picos", "picos", "estomba", "brandsen", "dorrego", "monte"];
function isInfraNode(name: string): boolean {
  const n = name.toLowerCase();
  return INFRA_KEYWORDS.some(k => n.includes(k));
}

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
  | "move_confirm"
  | "bomb_target"
  | "ai_turn"
  | "game_over";

type AIEvent =
  | { type: "faction_start";   faction: Faction }
  | { type: "census_start";    faction: Faction; cellIds: string[] }
  | { type: "ai_card_draw";    faction: Faction; card: CardType; handSize: number }
  | { type: "ai_card_trade";   faction: Faction; effectType: CardType; bonus: number; handSize: number }
  | { type: "reinforce";       cellId: string; troops: number }
  | { type: "attack_announce"; fromId: string; toId: string }
  | { type: "attack_round";    fromId: string; toId: string; fromTroops: number; toTroops: number; atkRoll: number; defRoll: number }
  | { type: "attack_result";   fromId: string; toId: string; won: boolean; fromTroops: number; toTroops: number; newOwner: Faction | null }
  | { type: "ai_bomb_attack";  faction: Faction; targetId: string; troopsLost: number; remaining: number; repelled: boolean }
  | { type: "production_announce"; cellIds: string[] }
  | { type: "production_double";   cellId: string; troops: number }
  | { type: "done" };

const COLORS: Record<Faction, string> = {
  player: "#1976d2", ai1: "#c62828", ai2: "#2e7d32", ai3: "#6d3b1f",
};
const NAMES: Record<Faction, string> = {
  player: "AZUL", ai1: "ROJO", ai2: "VERDE", ai3: "MARRÓN",
};
const AI_FACTIONS: Faction[] = ["ai1", "ai2", "ai3"];

type CardType =
  | "comodin"
  | "air_move"
  | "bomba"
  | "troops5"
  | "troops8"
  | "troops15"
  | "antibomba"
  | "lose_all";

interface CardMeta { label: string; sub: string; symbol: string; color: string; bg: string }
const CARD_META: Record<CardType, CardMeta> = {
  comodin:   { label: "COMODÍN",  sub: "COMODÍN",   symbol: "★",  color: "#ffd700", bg: "#1a1530" },
  air_move:  { label: "AIR MOVE", sub: "AÉREO",      symbol: "✈",  color: "#00bcd4", bg: "#041520" },
  bomba:     { label: "BOMBA",    sub: "ATAQUE",      symbol: "◉",  color: "#f44336", bg: "#1a0505" },
  troops5:   { label: "+5",       sub: "TROPAS",      symbol: "⚔",  color: "#4caf50", bg: "#051505" },
  troops8:   { label: "+8",       sub: "TROPAS",      symbol: "⚔",  color: "#42a5f5", bg: "#051020" },
  troops15:  { label: "+15",      sub: "TROPAS",      symbol: "⚔",  color: "#ffca28", bg: "#181000" },
  antibomba: { label: "ANTI",     sub: "BOMBA",       symbol: "⛨",  color: "#ce93d8", bg: "#120820" },
  lose_all:  { label: "PIERDE",   sub: "TUS CARTAS",  symbol: "☠",  color: "#78909c", bg: "#080a0a" },
};
// lose_all = 5%, las otras 7 cartas reparten el 95% en partes iguales (×140 → 7 y 19)
const CARD_WEIGHTS: [CardType, number][] = [
  ["comodin",   19],
  ["air_move",  19],
  ["bomba",     19],
  ["troops5",   19],
  ["troops8",   19],
  ["troops15",  19],
  ["antibomba", 19],
  ["lose_all",   7],
];
function drawCard(hand: CardType[] = []): CardType {
  // Si el jugador ya tiene antibomba no puede recibir bomba
  const blocked = new Set<CardType>(hand.includes("antibomba") ? ["bomba"] : []);
  const pool = CARD_WEIGHTS.filter(([t]) => !blocked.has(t));
  const total = pool.reduce((s, [, w]) => s + w, 0);
  let r = Math.random() * total;
  for (const [type, w] of pool) { r -= w; if (r < 0) return type; }
  return pool[pool.length - 1][0];
}

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
  cards:              CardType[];         // player hand (max 5)
  pendingCard:        CardType | null;   // carta dibujada esperando descarte (mano llena)
  wonCellThisTurn:    boolean;           // player conquered at least once this turn
  aiHands:            Record<string, CardType[]>;
  aiWonLastTurn:      Record<string, boolean>;
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
      isProduction: isInfraNode(node.long_name ?? node.short_name ?? node.node_id),
    };
  }

  const components = findComponents([...cellMap.keys()]);

  const TARGET_CELLS = 50;

  // helper: add N synthetic neighbors spread evenly around anchorId.
  // ownOccupied tracks cells belonging to the current island being extended;
  // candidates adjacent to any OTHER occupied cell are rejected to prevent island merges.
  const addSynthetics = (anchorId: string, anchorName: string, count: number, ownOccupied: Set<string>) => {
    const [cLat, cLon] = cellToLatLng(anchorId);
    const free = gridDisk(anchorId, 1)
      .filter(nb => {
        if (nb === anchorId || occupied.has(nb)) return false;
        return gridDisk(nb, 1).every(x => x === nb || !occupied.has(x) || ownOccupied.has(x));
      })
      .sort((a, b) => {
        const [aLat, aLon] = cellToLatLng(a), [bLat, bLon] = cellToLatLng(b);
        return bearingDeg(cLat, cLon, aLat, aLon) - bearingDeg(cLat, cLon, bLat, bLon);
      });
    const n     = Math.min(count, free.length);
    const toAdd = Array.from({ length: n }, (_, i) => free[Math.floor(i * free.length / n)]);
    const usedDirs = new Set<string>();
    for (const nb of toAdd) {
      const [nLat, nLon] = cellToLatLng(nb);
      let dir = bearingToDir(bearingDeg(cLat, cLon, nLat, nLon));
      if (usedDirs.has(dir)) dir += " 2";
      usedDirs.add(dir);
      occupied.add(nb);
      ownOccupied.add(nb);
      cells[nb] = { h3Index: nb, owner: "player", troops: 0,
        nodeName: `${anchorName} - ${dir}`, isNodeActive: false, isSynthetic: true, isProduction: false };
    }
  };

  // 1-cell islands → always at least 2 synthetics (triangle), 4 if gap allows
  for (const [nodeId] of components.filter(c => c.length === 1)) {
    const gap = TARGET_CELLS - Object.keys(cells).length;
    const node = cellMap.get(nodeId)!;
    const count = gap >= 4 ? 4 : 2; // minimum 2 → always 3-cell island
    addSynthetics(nodeId, node.long_name ?? node.short_name ?? node.node_id, count, new Set([nodeId]));
  }

  // 2-cell islands → always add 1st synthetic (minimum 3 cells), 2nd if gap allows
  for (const comp of components.filter(c => c.length === 2)) {
    const gap = TARGET_CELLS - Object.keys(cells).length;
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
    const name     = cellMap.get(anchorId)!.long_name ?? cellMap.get(anchorId)!.short_name ?? anchorId;
    occupied.add(cand);
    cells[cand] = { h3Index: cand, owner: "player", troops: 0,
      nodeName: `${name} - ${bearingToDir(bearingDeg(aLat, aLon, cLat, cLon))}`,
      isNodeActive: false, isSynthetic: true, isProduction: false };
    if (gap >= 2) addSynthetics(idA === anchorId ? idB : idA, name, 1, new Set([idA, idB, cand]));
  }

  // 3-cell islands → add 1 synthetic if still below target
  for (const comp of components.filter(c => c.length === 3)) {
    if (Object.keys(cells).length >= TARGET_CELLS) break;
    const islandSet = new Set(comp);
    const anchor = comp.reduce((best, id) => {
      const a = gridDisk(id, 1).filter(nb => nb !== id && islandSet.has(nb)).length;
      const b = gridDisk(best, 1).filter(nb => nb !== best && islandSet.has(nb)).length;
      return a > b ? id : best;
    });
    const anchorCell = cells[anchor];
    const anchorName = anchorCell.isSynthetic
      ? anchorCell.nodeName.replace(/ - (N|NE|E|SE|S|SO|O|NO)( 2)?$/, "")
      : anchorCell.nodeName;
    addSynthetics(anchor, anchorName, 1, new Set(comp));
  }

  // Cap oversized islands recursively.
  // Prefers removing synthetics; falls back to real nodes if needed.
  // Picks the removal that minimizes the largest resulting sub-component.
  const MAX_ISLAND_CELLS = 11;
  function capComponent(ids: string[]): void {
    if (ids.length <= MAX_ISLAND_CELLS) return;
    const synths = ids.filter(id => cells[id]?.isSynthetic);
    // Prefer synthetics; if none, allow removing non-production real nodes
    const candidates = synths.length > 0
      ? synths
      : ids.filter(id => !cells[id]?.isProduction);
    if (candidates.length === 0) return; // all production nodes — can't cap
    let bestId = candidates[0];
    let bestMax = ids.length;
    for (const id of candidates) {
      const rem = ids.filter(x => x !== id);
      const maxSub = Math.max(...findComponents(rem).map(s => s.length));
      if (maxSub < bestMax) { bestMax = maxSub; bestId = id; }
    }
    delete cells[bestId];
    occupied.delete(bestId);
    for (const sc of findComponents(ids.filter(x => x !== bestId))) {
      capComponent(sc);
    }
  }
  for (const comp of findComponents(Object.keys(cells))) {
    capComponent(comp);
  }

  // Guarantee at least one island [10,11] and one [8,9].
  // Grows islands by appending safe synthetics one cell at a time.
  function growIsland(ids: string[], targetMin: number, targetMax: number): void {
    const ownOccupied = new Set(ids);
    const current = [...ids];
    while (current.length < targetMin) {
      let grew = false;
      for (const id of [...current]) {
        if (current.length >= targetMax) break;
        const [cLat, cLon] = cellToLatLng(id);
        const free = gridDisk(id, 1).filter(nb => {
          if (nb === id || occupied.has(nb)) return false;
          return gridDisk(nb, 1).every(x => x === nb || !occupied.has(x) || ownOccupied.has(x));
        });
        if (free.length === 0) continue;
        const nb = free[0];
        const baseName = cells[id].nodeName.replace(/ - (N|NE|E|SE|S|SO|O|NO)( 2)?$/, "");
        const [nLat, nLon] = cellToLatLng(nb);
        const dir = bearingToDir(bearingDeg(cLat, cLon, nLat, nLon));
        occupied.add(nb); ownOccupied.add(nb);
        cells[nb] = { h3Index: nb, owner: "player", troops: 0,
          nodeName: `${baseName} - ${dir}`, isNodeActive: false, isSynthetic: true, isProduction: false };
        current.push(nb);
        grew = true;
        break;
      }
      if (!grew) break;
    }
  }

  let postComps = findComponents(Object.keys(cells)).sort((a, b) => b.length - a.length);
  if (!postComps.some(c => c.length >= 10)) {
    growIsland(postComps[0], 10, 11);
    postComps = findComponents(Object.keys(cells)).sort((a, b) => b.length - a.length);
  }
  if (!postComps.some(c => c.length >= 8 && c.length <= 9)) {
    const candidate = postComps.find(c => c.length < 8 && c.length >= 3);
    if (candidate) growIsland(candidate, 8, 9);
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



// ── AI event queue ────────────────────────────────────────────────────────────

function planAITurns(
  cells:          Record<string, GameCell>,
  bridges:        Bridge[],
  islands:        string[][],
  newTurn:        number,
  aiHands:        Record<string, CardType[]>,
  aiWonLastTurn:  Record<string, boolean>,
  playerCards:    CardType[],
): {
  events: AIEvent[]; finalCells: Record<string, GameCell>; winner: Faction | null; reinf: number;
  newAiHands: Record<string, CardType[]>; newAiWonLastTurn: Record<string, boolean>; newPlayerCards: CardType[];
  factionCells: Record<string, Record<string, GameCell>>;
} {
  const events: AIEvent[] = [];
  let c = { ...cells };
  const factionCells: Record<string, Record<string, GameCell>> = {};
  const localHands: Record<string, CardType[]> = {
    ai1: [...(aiHands.ai1 ?? [])], ai2: [...(aiHands.ai2 ?? [])], ai3: [...(aiHands.ai3 ?? [])],
  };
  const wonThisPhase: Record<string, boolean> = { ai1: false, ai2: false, ai3: false };
  let localPlayerCards = [...playerCards];

  for (const faction of AI_FACTIONS) {
    events.push({ type: "faction_start", faction });

    // Award card if won last turn
    const hand = localHands[faction];
    if (aiWonLastTurn[faction] && hand.length < 5) {
      const card = drawCard();
      hand.push(card);
      events.push({ type: "ai_card_draw", faction, card, handSize: hand.length });
    }

    // Trade best troop set (runs every turn, not just after drawing)
    {
      const troopSets = findTradeSets(localHands[faction])
        .filter(s => troopBonus(s.effectType) > 0)
        .sort((a, b) => troopBonus(b.effectType) - troopBonus(a.effectType));
      if (troopSets.length > 0) {
        const best = troopSets[0];
        localHands[faction] = localHands[faction].filter((_, i) => !best.indices.includes(i));
        events.push({ type: "ai_card_trade", faction, effectType: best.effectType, bonus: troopBonus(best.effectType), handSize: localHands[faction].length });
      }
    }

    // Trade bomb set if available — target player cell with most troops
    {
      const bombSets = findTradeSets(localHands[faction]).filter(s => s.effectType === "bomba");
      if (bombSets.length > 0) {
        const best = bombSets[0];
        localHands[faction] = localHands[faction].filter((_, i) => !best.indices.includes(i));
        const playerCells = Object.values(c).filter(x => x.owner === "player");
        if (playerCells.length > 0) {
          const target = playerCells.reduce((a, b) => a.troops > b.troops ? a : b);
          const antibombaIdx = localPlayerCards.indexOf("antibomba");
          if (antibombaIdx >= 0) {
            localPlayerCards = localPlayerCards.filter((_, i) => i !== antibombaIdx);
            events.push({ type: "ai_bomb_attack", faction, targetId: target.h3Index, troopsLost: 0, remaining: target.troops, repelled: true });
          } else {
            const lossPct = 0.5 + Math.random() * 0.4;
            const lost = Math.max(1, Math.floor(target.troops * lossPct));
            const remaining = Math.max(1, target.troops - lost);
            c = { ...c, [target.h3Index]: { ...c[target.h3Index], troops: remaining } };
            events.push({ type: "ai_bomb_attack", faction, targetId: target.h3Index, troopsLost: lost, remaining, repelled: false });
          }
        }
      }
    }

    // Census
    const ownedIds = Object.values(c).filter(x => x.owner === faction).map(x => x.h3Index);
    events.push({ type: "census_start", faction, cellIds: sortCellsGeographically(ownedIds) });

    // Reinforcements (base + troop trade bonus)
    const owned  = Object.values(c).filter(x => x.owner === faction);
    const tradeBonus = events
      .filter(e => e.type === "ai_card_trade" && (e as any).faction === faction)
      .reduce((sum, e) => sum + (e as any).bonus, 0);
    let reinf = calcReinf(c, islands, faction) + tradeBonus;
    const stratPool   = pickReinforcementPool(faction, owned, c, bridges, islands);
    const actualPool  = stratPool.length > 0 ? stratPool : owned.map(x => x.h3Index);
    const allocations = new Map<string, number>();
    let remaining = reinf;
    while (remaining-- > 0 && actualPool.length > 0) {
      const id = actualPool[Math.floor(Math.random() * actualPool.length)];
      allocations.set(id, (allocations.get(id) ?? 0) + 1);
    }
    let focusCell: string | null = null;
    for (const cellId of nnSort([...allocations.keys()])) {
      const count = allocations.get(cellId)!;
      for (let i = 0; i < count; i++) {
        c = { ...c, [cellId]: { ...c[cellId], troops: c[cellId].troops + 1 } };
        events.push({ type: "reinforce", cellId, troops: c[cellId].troops });
      }
      focusCell = cellId;
    }

    // Attacks (up to 3) — delegated to faction strategy
    for (let i = 0; i < 3; i++) {
      const factionOwned = Object.values(c).filter(x => x.owner === faction);
      const attack = pickFactionAttack(faction, factionOwned, c, bridges, focusCell, islands);
      if (!attack) break;

      const { from, to } = attack;
      focusCell = from;
      events.push({ type: "attack_announce", fromId: from, toId: to });

      let atk = c[from].troops, def = c[to].troops;
      while (atk > 1 && def > 0) {
        const atkRoll = Math.floor(Math.random() * 6) + 1;
        const defRoll = Math.floor(Math.random() * 6) + 1;
        if (atkRoll > defRoll) def--; else atk--;
        events.push({ type: "attack_round", fromId: from, toId: to, fromTroops: atk, toTroops: def, atkRoll, defRoll });
      }

      const won = def === 0;
      if (won) wonThisPhase[faction] = true;
      c = won
        ? { ...c, [from]: { ...c[from], troops: 1 }, [to]: { ...c[to], owner: faction, troops: Math.max(1, atk - 1) } }
        : { ...c, [from]: { ...c[from], troops: atk }, [to]: { ...c[to], troops: def } };
      events.push({ type: "attack_result", fromId: from, toId: to, won, fromTroops: c[from].troops, toTroops: c[to].troops, newOwner: won ? faction : null });
    }
    factionCells[faction] = { ...c };
  }

  if (newTurn % PROD_INTERVAL === 0) {
    const prodCells = nnSort(
      Object.values(c).filter(cell => cell.isProduction).map(cell => cell.h3Index)
    );
    if (prodCells.length > 0) {
      events.push({ type: "production_announce", cellIds: prodCells });
      for (const cellId of prodCells) {
        const newTroops = Math.min(PROD_CAP, c[cellId].troops * 2);
        c = { ...c, [cellId]: { ...c[cellId], troops: newTroops } };
        events.push({ type: "production_double", cellId, troops: newTroops });
      }
    }
  }
  const finalCells = c;
  const winner     = checkWinner(finalCells);
  const reinf      = calcReinf(finalCells, islands, "player");
  events.push({ type: "done" });

  return { events, finalCells, winner, reinf, newAiHands: localHands, newAiWonLastTurn: wonThisPhase, newPlayerCards: localPlayerCards, factionCells };
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
  reinforce: () => {
    playTone(330, 0.05, "sine",   0.10);
    setTimeout(() => playTone(440, 0.06, "sine",   0.16), 55);
    setTimeout(() => playTone(587, 0.07, "square", 0.20), 120);
    setTimeout(() => playTone(880, 0.10, "sine",   0.14), 210);
  },
  pass:     () => playTone(330, 0.1, "sine", 0.08),
  move:     () => { playTone(550, 0.08, "sine", 0.1); setTimeout(() => playTone(660, 0.08, "sine", 0.1), 90); },
  bomb:     () => {
    playTone(90,  0.6, "sawtooth", 0.28);
    setTimeout(() => playTone(70,  0.5, "sine",     0.22), 80);
    setTimeout(() => playTone(55,  0.7, "sawtooth", 0.18), 220);
    setTimeout(() => playTone(180, 0.2, "sawtooth", 0.10), 40);
  },
  productionAnnounce: () => {
    playTone(55,   1.0, "sawtooth", 0.14);
    setTimeout(() => playTone(73.4,  0.7, "sawtooth", 0.11), 250);
    setTimeout(() => playTone(110,   0.6, "sine",     0.09), 600);
    setTimeout(() => playTone(146.8, 0.5, "sine",     0.08), 1000);
    setTimeout(() => playTone(220,   0.4, "square",   0.11), 1450);
    setTimeout(() => playTone(440,   0.3, "square",   0.09), 1900);
    setTimeout(() => playTone(880,   0.4, "square",   0.13), 2200);
    setTimeout(() => playTone(1760,  0.3, "sine",     0.08), 2550);
  },
  productionDouble: () => {
    playTone(880,    0.05, "sine",   0.08);
    setTimeout(() => playTone(1174.7, 0.08, "sine",   0.12), 70);
    setTimeout(() => playTone(1318.5, 0.10, "square", 0.14), 150);
    setTimeout(() => playTone(1760,   0.12, "sine",   0.09), 260);
  },
  menuStart: () => {
    playTone(220, 0.05, "square", 0.12);
    setTimeout(() => playTone(330, 0.05, "square", 0.14), 55);
    setTimeout(() => playTone(440, 0.05, "square", 0.16), 110);
    setTimeout(() => playTone(660, 0.18, "square", 0.18), 170);
  },
  menuGuide: () => {
    playTone(440, 0.07, "sine", 0.10);
    setTimeout(() => playTone(550, 0.07, "sine", 0.12), 80);
    setTimeout(() => playTone(660, 0.12, "sine", 0.10), 165);
  },
};

function playDiffSound(d: Difficulty) {
  const freq: Record<Difficulty, number> = { easy: 660, medium: 550, hard: 440, vhard: 330 };
  playTone(freq[d], 0.06, "square", 0.10);
  setTimeout(() => playTone(freq[d] * 1.5, 0.05, "square", 0.07), 65);
}

// ── intro ─────────────────────────────────────────────────────────────────────

const INTRO_TEXT =
`Año 2045

Gran parte de la humanidad ha quedado extinta.

Los sobrevivientes han pasado su conciencia al mundo virtual como forma de ser eficientes ante las IAs.

Año 2051.

Internet cayó.
La nube cayó.
Los satélites callaron.

Tras la expansión de la IA, el ser humano se volvió obsoleto. La IA decidió desconectarnos.

En un último movimiento desesperado, algunos humanos lograron sobrevivir transfiriendo sus conciencias a redes menores, sistemas olvidados y hardware obsoleto.

En el Sudoeste Bonaerense, una red de nodos LoRa sobrevivió desconectada del resto del mundo, los sobrevivientes de esta región decidieron adoptarlo como su nuevo hogar.

La llamaron la Mesh.

De sus rutas, paquetes perdidos y memorias digitales surgió una sustancia extraña: la Lucaína-T. Energía, droga y combustible para las conciencias digitales.

Pero la Mesh no está en paz.

Cuatro facciones luchan por controlarla.

Los Viejos RCBB buscan orden en el éter. (jugador Marrón)

El Círculo DX, codicioso, busca expansión. (jugador Rojo)

Los Fundadores de Meshtastic, hoy corrompidos por los vicios, buscan saciar su abstinencia con Lucaína-T. (Jugador Verde)

Los Custodios del BBS buscan preservar lo último que queda de la humanidad: Este es tu rol. Esta es tu misión. (Jugador Azul)

La señal está abierta.
La guerra comenzó.`;

function startIntroMusic(): () => void {
  try {
    const ctx = getCtx();
    const oscs: OscillatorNode[] = [];
    const gains: GainNode[] = [];

    const addDrone = (freq: number, type: OscillatorType, vol: number, detune = 0) => {
      const osc  = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = type;
      osc.frequency.value = freq;
      osc.detune.value = detune;
      gain.gain.setValueAtTime(0, ctx.currentTime);
      gain.gain.linearRampToValueAtTime(vol, ctx.currentTime + 3);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      oscs.push(osc);
      gains.push(gain);
    };

    addDrone(55,    "sine",     0.016);
    addDrone(73.4,  "sine",     0.012);
    addDrone(110,   "sine",     0.009);
    addDrone(146.8, "sawtooth", 0.005, -10);
    addDrone(196,   "sine",     0.004);

    // Slow D-minor arpeggio
    const arpFreqs = [110, 130.8, 146.8, 174.6, 196, 220, 174.6, 146.8];
    let arpIdx = 0;
    const arpId = setInterval(() => {
      playTone(arpFreqs[arpIdx % arpFreqs.length], 1.4, "sine", 0.09);
      arpIdx++;
    }, 950);

    return () => {
      clearInterval(arpId);
      const t = ctx.currentTime;
      gains.forEach(g => {
        g.gain.setValueAtTime(g.gain.value, t);
        g.gain.linearRampToValueAtTime(0, t + 0.9);
      });
      setTimeout(() => {
        oscs.forEach(o => { try { o.stop(); o.disconnect(); } catch {} });
        gains.forEach(g => { try { g.disconnect(); } catch {} });
      }, 1000);
    };
  } catch {
    return () => {};
  }
}

// Returns the point where the line from `from` to `to` exits the hexagon boundary of `from`
function hexEdgePoint(
  from: [number, number],
  to:   [number, number],
  boundary: [number, number][],
): [number, number] {
  const [x0, y0] = from;
  const [x1, y1] = to;
  const dx = x1 - x0, dy = y1 - y0;
  for (let i = 0; i < boundary.length; i++) {
    const [ax, ay] = boundary[i];
    const [bx, by] = boundary[(i + 1) % boundary.length];
    const dax = bx - ax, day = by - ay;
    const denom = dx * day - dy * dax;
    if (Math.abs(denom) < 1e-12) continue;
    const t = ((ax - x0) * day - (ay - y0) * dax) / denom;
    const s = ((ax - x0) * dy  - (ay - y0) * dx)  / denom;
    if (t > 0 && s >= 0 && s <= 1) return [x0 + t * dx, y0 + t * dy];
  }
  return from;
}

const HEX_VISIBLE_ZOOM = 11;

function troopMarkerHtml(troops: number, isProduction: boolean, ownerColor: string, factionBg = false): string {
  const bg     = factionBg ? ownerColor : "rgba(0,0,0,0.6)";
  const border = factionBg ? "rgba(255,255,255,0.7)" : "rgba(255,255,255,0.55)";
  return `<div style="background:${bg};color:#fff;font-size:13px;font-weight:700;font-family:monospace;width:26px;height:26px;border-radius:50%;display:flex;align-items:center;justify-content:center;border:2px solid ${border};pointer-events:none;transform:translate(-50%,-50%);position:relative;">${troops}${isProduction ? '<span style="color:#ffd700;font-size:8px;position:absolute;top:-2px;right:-2px">★</span>' : ""}</div>`;
}

function runRechargeFlash(cellId: string, polygons: Map<string, L.Polygon>): void {
  const poly = polygons.get(cellId);
  if (!poly) return;
  const saved = {
    fillColor:   (poly.options as any).fillColor  as string,
    fillOpacity: (poly.options as any).fillOpacity as number,
    color:       (poly.options as any).color       as string,
    weight:      (poly.options as any).weight      as number,
  };
  const el = (poly as any)._path as SVGPathElement | undefined;

  // Instant swell: faction color at full brightness + thick white border
  poly.setStyle({ fillColor: saved.fillColor, fillOpacity: 0.93, color: "#ffffff", weight: 5.5 });

  // After 90ms, CSS transition smoothly returns to normal
  setTimeout(() => {
    if (el) el.style.transition = "fill-opacity 0.28s ease-out, stroke-width 0.28s ease-out";
    poly.setStyle(saved);
    setTimeout(() => { if (el) el.style.transition = ""; }, 350);
  }, 90);
}

function runBombFlash(cellId: string, polygons: Map<string, L.Polygon>): void {
  const poly = polygons.get(cellId);
  if (!poly) return;
  const saved = {
    fillColor:   (poly.options as any).fillColor  as string,
    fillOpacity: (poly.options as any).fillOpacity as number,
    color:       (poly.options as any).color       as string,
    weight:      (poly.options as any).weight      as number,
  };
  const BLINKS = 6, PERIOD = 160;
  for (let b = 0; b < BLINKS; b++) {
    setTimeout(() => poly.setStyle({ fillColor: "#f44336", fillOpacity: 0.95, color: "#ff1744", weight: 3.5 }), b * PERIOD);
    setTimeout(() => poly.setStyle(saved), b * PERIOD + PERIOD / 2);
  }
  setTimeout(() => poly.setStyle(saved), BLINKS * PERIOD + 50);
}

// Defender: full-cell red fill flash (soft/blurred effect)
function runRoundFlash(cellId: string, polygons: Map<string, L.Polygon>): void {
  const poly = polygons.get(cellId);
  if (!poly) return;
  const saved = {
    fillColor:   (poly.options as any).fillColor   as string,
    fillOpacity: (poly.options as any).fillOpacity as number,
    color:       (poly.options as any).color       as string,
    weight:      (poly.options as any).weight      as number,
  };
  poly.setStyle({ fillColor: "#ff1744", fillOpacity: 0.88, color: "#ff1744", weight: 1.5 });
  setTimeout(() => poly.setStyle(saved), 220);
}

// Animated chevron arrow. Listens to map move/zoom to stay in sync during fly animations.
function runAttackArrow(
  fromId: string,
  toId: string,
  map: L.Map,
  container: HTMLDivElement,
): HTMLElement {
  const [fLat, fLon] = cellToLatLng(fromId);
  const [tLat, tLon] = cellToLatLng(toId);
  const ns = (tag: string) => document.createElementNS("http://www.w3.org/2000/svg", tag);

  const H = 4, W = 5, SW = 2.5, STEP = 20, MAX_CH = 22;
  const clipId = `mwac${Date.now()}`;

  // Build static SVG structure once; only update positional attributes on move
  const svg    = ns("svg") as SVGSVGElement;
  Object.assign((svg as unknown as HTMLElement).style, {
    position: "absolute", top: "0", left: "0",
    width: "100%", height: "100%",
    pointerEvents: "none", zIndex: "500", overflow: "visible",
  });

  const defs   = ns("defs");
  const clip   = ns("clipPath"); clip.setAttribute("id", clipId);
  const cRect  = ns("rect") as SVGRectElement;
  cRect.setAttribute("y",      `${-H - SW}`);
  cRect.setAttribute("height", `${(H + SW) * 2}`);
  clip.appendChild(cRect); defs.appendChild(clip); svg.appendChild(defs);

  const bgLine = ns("line") as SVGLineElement;
  bgLine.setAttribute("stroke",         "#ffffff");
  bgLine.setAttribute("stroke-width",   `${H * 2 + 2}`);
  bgLine.setAttribute("opacity",        "0.13");
  bgLine.setAttribute("stroke-linecap", "round");
  svg.appendChild(bgLine);

  const outerG    = ns("g") as SVGGElement;
  const clipG     = ns("g") as SVGGElement; clipG.setAttribute("clip-path", `url(#${clipId})`);
  const animG     = ns("g") as SVGGElement;
  const anim      = ns("animateTransform");
  anim.setAttribute("attributeName", "transform");
  anim.setAttribute("type",          "translate");
  anim.setAttribute("from",          "0 0");
  anim.setAttribute("to",            `-${STEP} 0`);
  anim.setAttribute("dur",           "0.45s");
  anim.setAttribute("repeatCount",   "indefinite");
  animG.appendChild(anim);

  const chevs: SVGPathElement[] = [];
  for (let i = 0; i < MAX_CH; i++) {
    const p = ns("path") as SVGPathElement;
    p.setAttribute("fill",             "none");
    p.setAttribute("stroke",           "#ffffff");
    p.setAttribute("stroke-width",     `${SW}`);
    p.setAttribute("stroke-linecap",   "butt");
    p.setAttribute("stroke-linejoin",  "miter");
    p.setAttribute("stroke-miterlimit","12");
    p.setAttribute("opacity",          "0.92");
    animG.appendChild(p); chevs.push(p);
  }
  clipG.appendChild(animG); outerG.appendChild(clipG); svg.appendChild(outerG);

  function render() {
    const fp   = map.latLngToContainerPoint([fLat, fLon]);
    const tp   = map.latLngToContainerPoint([tLat, tLon]);
    const dx   = tp.x - fp.x, dy = tp.y - fp.y;
    const dist = Math.sqrt(dx * dx + dy * dy);
    const ang  = Math.atan2(dy, dx) * 180 / Math.PI;
    const numCh = Math.ceil(dist / STEP) + 2;

    bgLine.setAttribute("x1", `${fp.x}`); bgLine.setAttribute("y1", `${fp.y}`);
    bgLine.setAttribute("x2", `${tp.x}`); bgLine.setAttribute("y2", `${tp.y}`);
    outerG.setAttribute("transform", `translate(${fp.x},${fp.y}) rotate(${ang})`);
    cRect.setAttribute("x",     "0");
    cRect.setAttribute("width", `${dist}`);

    for (let i = 0; i < MAX_CH; i++) {
      if (i <= numCh) {
        const x = (i - 1) * STEP;
        chevs[i].setAttribute("d", `M${x - W} ${-H} L${x} 0 L${x - W} ${H}`);
        chevs[i].removeAttribute("display");
      } else {
        chevs[i].setAttribute("display", "none");
      }
    }
  }

  render();
  map.on("move",    render);
  map.on("zoomend", render);
  (svg as any)._cleanup = () => { map.off("move", render); map.off("zoomend", render); };

  container.appendChild(svg as unknown as HTMLElement);
  return svg as unknown as HTMLElement;
}

function runProductionFlash(cellId: string, polygons: Map<string, L.Polygon>): void {
  const poly = polygons.get(cellId);
  if (!poly) return;
  const saved = {
    fillColor:   (poly.options as any).fillColor  as string,
    fillOpacity: (poly.options as any).fillOpacity as number,
    color:       (poly.options as any).color       as string,
    weight:      (poly.options as any).weight      as number,
  };
  const BLINKS = 5, PERIOD = 180;
  for (let b = 0; b < BLINKS; b++) {
    setTimeout(() => poly.setStyle({ fillColor: "#ffd700", fillOpacity: 0.95, color: "#ffea00", weight: 3.5 }), b * PERIOD);
    setTimeout(() => poly.setStyle({ fillColor: "#ff6f00", fillOpacity: 0.80, color: "#ffd740", weight: 2.5 }), b * PERIOD + PERIOD / 2);
  }
  setTimeout(() => poly.setStyle(saved), BLINKS * PERIOD + 80);
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

type Difficulty = "easy" | "medium" | "hard" | "vhard";
const DIFFICULTY_PLAYER_FRACTION: Record<Difficulty, number> = {
  easy:   0.40,
  medium: 0.30,
  hard:   0.22,
  vhard:  0.15,
};
const DIFFICULTY_LABEL: Record<Difficulty, string> = {
  easy:   "FÁCIL",
  medium: "MEDIO",
  hard:   "DIFÍCIL",
  vhard:  "MUY DIFÍCIL",
};

function initGame(nodes: MeshNode[], difficulty: Difficulty = "medium"): GameState {
  const active = nodes.filter(n => {
    if (n.lat == null || n.lon == null) return false;
    const name = n.long_name ?? n.short_name ?? n.node_id;
    return isInfraNode(name) || (n.last_seen_mins_ago ?? 999) <= ACTIVE_MINS;
  });
  if (!active.length) return {
    cells: {}, bridges: [], islands: [], phase: "action", turn: 1,
    reinforcementsLeft: 0, attackTarget: null, attackSource: null, moveSource: null, postConquestTroops: 0,
    winner: null, currentFaction: "player", combatMsg: "", aiQueue: [], aiMsg: "", log: ["Sin nodos activos con GPS."],
    cards: [], pendingCard: null, wonCellThisTurn: false,
    aiHands: { ai1: [], ai2: [], ai3: [] }, aiWonLastTurn: { ai1: false, ai2: false, ai3: false },
  };

  const cellMap = new Map<string, MeshNode>();
  for (const node of active) {
    const cid  = latLngToCell(node.lat!, node.lon!, H3_RES);
    const prev = cellMap.get(cid);
    if (!prev || (node.snr_from_bbs ?? -999) > (prev.snr_from_bbs ?? -999)) cellMap.set(cid, node);
  }

  const cells    = buildCells(cellMap);
  const shuffled = Object.keys(cells).sort(() => Math.random() - 0.5);
  const n        = shuffled.length;
  const playerN  = Math.max(1, Math.round(n * DIFFICULTY_PLAYER_FRACTION[difficulty]));
  const aiN      = Math.floor((n - playerN) / 3);
  const counts: Record<Faction, number> = {
    player: playerN,
    ai1:    aiN,
    ai2:    aiN,
    ai3:    n - playerN - aiN * 2, // remainder to last AI
  };
  const order: Faction[] = ["player", "ai1", "ai2", "ai3"];
  let idx = 0;
  for (const faction of order) {
    for (let i = 0; i < counts[faction]; i++, idx++) {
      cells[shuffled[idx]].owner  = faction;
      cells[shuffled[idx]].troops = Math.floor(Math.random() * 3) + 2;
    }
  }

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
    cards: [], pendingCard: null, wonCellThisTurn: false,
    aiHands: { ai1: [], ai2: [], ai3: [] }, aiWonLastTurn: { ai1: false, ai2: false, ai3: false },
  };
}

// ── card trade logic ──────────────────────────────────────────────────────────

function troopBonus(type: CardType): number {
  return type === "troops5" ? 5 : type === "troops8" ? 8 : type === "troops15" ? 15 : 0;
}

function findTradeSets(cards: CardType[]): Array<{ indices: number[]; effectType: CardType }> {
  const sets: Array<{ indices: number[]; effectType: CardType }> = [];
  const n = cards.length;
  for (let a = 0; a < n - 2; a++) {
    for (let b = a + 1; b < n - 1; b++) {
      for (let c = b + 1; c < n; c++) {
        const t     = [cards[a], cards[b], cards[c]];
        const jokers = t.filter(x => x === "comodin").length;
        const nonJ   = t.filter(x => x !== "comodin");
        if (jokers === 0 && t[0] === t[1] && t[1] === t[2]) {
          sets.push({ indices: [a, b, c], effectType: t[0] as CardType });
        } else if (jokers === 1 && nonJ[0] === nonJ[1]) {
          sets.push({ indices: [a, b, c], effectType: nonJ[0] });
        } else if (jokers === 2 && nonJ.length === 1) {
          sets.push({ indices: [a, b, c], effectType: nonJ[0] });
        }
      }
    }
  }
  return sets;
}

function getSelectionEffect(cards: CardType[], indices: number[]): CardType | null {
  if (indices.length !== 3) return null;
  const t      = indices.map(i => cards[i]);
  const jokers = t.filter(x => x === "comodin").length;
  const nonJ   = t.filter(x => x !== "comodin");
  if (jokers === 0 && t[0] === t[1] && t[1] === t[2]) return t[0];
  if (jokers === 1 && nonJ[0] === nonJ[1])             return nonJ[0];
  if (jokers === 2 && nonJ.length === 1)               return nonJ[0];
  return null;
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
    case "move_confirm": { const max = (gs.cells[gs.moveSource!]?.troops ?? 2) - 1;
      return [`MOVER TROPAS: ${gs.postConquestTroops}`, `MÍN: 1  MÁX: ${max}`]; }
    case "bomb_target":    return ["◉ SELECCIONÁ EL OBJETIVO DE LA BOMBA", "HACÉ CLIC EN UN TERRITORIO ENEMIGO"];
    case "ai_turn":        return [gs.aiMsg || "IA jugando...", ""];
    case "game_over":      return ["FIN DE PARTIDA", gs.winner === "player" ? "¡VICTORIA!" : `GANÓ ${NAMES[gs.winner!]}`];
    default:               return ["", ""];
  }
}

// ── tutorial ──────────────────────────────────────────────────────────────────

const TUTORIAL_SLIDES = [
  {
    icon: "🎯",
    color: "#00e5ff",
    title: "OBJETIVO",
    body: "Sos los Custodios del BBS (azul). Tu misión: conquistar TODOS los territorios de la Mesh.\n\nEl juego termina cuando una facción controla el 100% del mapa.",
    tip: "Controlá los nodos de infraestructura (★) — producen tropas extra.",
  },
  {
    icon: "⚡",
    color: "#ffcc00",
    title: "TU TURNO",
    body: "Cada turno tiene dos fases:\n\n1. REFUERZO — colocás tropas nuevas en tus territorios. Más territorios = más refuerzos.\n\n2. ACCIÓN — atacás un enemigo, movés tropas entre tus territorios, o pasás.",
    tip: "Podés hacer solo una acción por turno (atacar O mover).",
  },
  {
    icon: "⚔",
    color: "#ff3355",
    title: "COMBATE",
    body: "Para atacar necesitás al menos 2 tropas en tu territorio. Se tiran dados ronda a ronda: el que saca más gana la ronda y el rival pierde 1 tropa.\n\nEl combate para cuando alguien queda en 0. Si ganás, conquistás el territorio.",
    tip: "Tener más tropas que el defensor aumenta mucho tus chances.",
  },
  {
    icon: "🃏",
    color: "#aa00ff",
    title: "CARTAS & PRODUCCIÓN",
    body: "Conquistar un territorio te da una carta. Juntá 3 cartas del mismo tipo y canjeálas por tropas extra al inicio del turno.\n\nCada ciertos turnos ocurre la Producción de Lucaína-T: los nodos de infraestructura (★) duplican sus tropas.",
    tip: "Guardá la bomba (◉) para un momento decisivo — ataca a distancia.",
  },
];

function tutorialContent(gs: GameState): { icon: string; color: string; title: string; desc: string; tip: string | null } | null {
  switch (gs.phase) {
    case "reinforcement":
      return {
        icon: "🛡", color: "#00e5ff",
        title: "FASE DE REFUERZO",
        desc: `Tenés ${gs.reinforcementsLeft} tropa${gs.reinforcementsLeft !== 1 ? "s" : ""} para colocar.\n\nHacé clic en cualquiera de tus territorios (azul) para reforzarlo. Repetí hasta agotar el stock.`,
        tip: "Reforzá las fronteras donde más te presionan los enemigos.",
      };
    case "action":
      return {
        icon: "⚡", color: "#ffcc00",
        title: "TU TURNO — ELEGÍ UNA ACCIÓN",
        desc: "• ATACAR — conquistá un territorio enemigo adyacente\n• MOVER — redistribuí tropas entre tus territorios\n• PASAR — terminá tu turno sin hacer nada\n• CARTAS — canjeá 3 cartas iguales por refuerzos",
        tip: "Conquistar un territorio te da una carta al final del turno.",
      };
    case "attack_target":
      return {
        icon: "⚔", color: "#ff3355",
        title: "SELECCIONÁ OBJETIVO",
        desc: "Hacé clic en un territorio ENEMIGO que sea vecino de uno tuyo.\n\nVerás los territorios posibles resaltados en el mapa.",
        tip: "Atacá donde tenés más tropas que el defensor para maximizar chances.",
      };
    case "attack_source":
      return {
        icon: "📍", color: "#ff3355",
        title: `ATACAR → ${gs.cells[gs.attackTarget!]?.nodeName ?? "?"}`,
        desc: "Ahora hacé clic en TU territorio desde el que vas a lanzar el ataque.\n\nDebe ser adyacente al objetivo y tener al menos 2 tropas.",
        tip: "Elegí el que tenga más tropas para tener ventaja.",
      };
    case "attack_confirm": {
      const atk = gs.cells[gs.attackSource!]?.troops ?? 0;
      const def = gs.cells[gs.attackTarget!]?.troops ?? 0;
      const fav = atk > def;
      return {
        icon: "⚔", color: "#ff3355",
        title: `${atk} TROPAS CONTRA ${def}`,
        desc: `Tu territorio tiene ${atk} tropas, el enemigo tiene ${def}.\n\nSe combate ronda a ronda con dados. Cada bando pierde 1 tropa por ronda perdida.`,
        tip: fav ? "Tenés ventaja numérica — conviene atacar." : "Estás en desventaja. Podés retirarte sin perder nada.",
      };
    }
    case "post_conquest":
      return {
        icon: "🏆", color: "#00ff88",
        title: "¡CONQUISTA!",
        desc: "Ganaste el combate. Elegí cuántas tropas mover al nuevo territorio con los botones +/−.\n\nDebe quedar al menos 1 tropa en el territorio origen.",
        tip: "Mové suficientes tropas para defender el nuevo territorio.",
      };
    case "move_source":
      return {
        icon: "↔", color: "#00e5ff",
        title: "MOVER TROPAS — ORIGEN",
        desc: "Hacé clic en TU territorio desde el que querés mover tropas.\n\nSolo podés mover a territorios propios adyacentes.",
        tip: "Útil para concentrar fuerzas antes de un ataque.",
      };
    case "move_target":
      return {
        icon: "↔", color: "#00e5ff",
        title: `DESTINO DESDE ${gs.cells[gs.moveSource!]?.nodeName ?? "?"}`,
        desc: "Hacé clic en un territorio TUYO adyacente al origen.\n\nLas tropas se moverán de un nodo al otro.",
        tip: "Solo podés mover a vecinos directos, no a distancia.",
      };
    case "move_confirm": {
      const max = (gs.cells[gs.moveSource!]?.troops ?? 2) - 1;
      return {
        icon: "↔", color: "#00e5ff",
        title: "CONFIRMAR MOVIMIENTO",
        desc: `Elegí cuántas tropas mover (máx: ${max}) con los botones +/−.\n\nSiempre queda 1 tropa en el origen. Luego presioná MOVER.`,
        tip: null,
      };
    }
    case "bomb_target":
      return {
        icon: "◉", color: "#ff6600",
        title: "ATAQUE BOMBA",
        desc: "Canjeaste una bomba. Hacé clic en CUALQUIER territorio enemigo del mapa para lanzarla.\n\nElimina entre 1 y 3 tropas enemigas según la severidad.",
        tip: "Ideal para debilitar un territorio fuertemente defendido antes de atacar.",
      };
    case "ai_turn":
      return {
        icon: "🤖", color: "#aa00ff",
        title: "TURNO DE LA IA",
        desc: "Las otras facciones están jugando. Observá en el mapa cómo se mueven y atacan.\n\nPodés presionar SALTAR para avanzar rápido al próximo turno.",
        tip: null,
      };
    case "game_over":
      return {
        icon: gs.winner === "player" ? "🏆" : "💀",
        color: gs.winner === "player" ? "#00ff88" : "#ff3355",
        title: gs.winner === "player" ? "¡VICTORIA!" : "DERROTA",
        desc: gs.winner === "player"
          ? "¡Dominaste la Mesh! Conquistaste todos los territorios."
          : `${NAMES[gs.winner!]} dominó la Mesh esta vez.\n\nCada partida es diferente — intentalo de nuevo.`,
        tip: null,
      };
    default:
      return null;
  }
}

// ── component ─────────────────────────────────────────────────────────────────

export default function MeshWarsView() {
  const [gs, setGs]               = useState<GameState | null>(null);
  const [nodesReady, setReady]    = useState(false);
  const [cardFlash, setCardFlash] = useState(false);
  const [selectedCards, setSelectedCards] = useState<number[]>([]);
  const [loseAllFlash, setLoseAllFlash] = useState(false);
  const [blinkVisible, setBlinkVisible] = useState(true);
  const [introState, setIntroState] = useState<"start" | "typing" | "done" | "tutorial_slides">("start");
  const [tutorialMode, setTutorialMode] = useState(false);
  const [tutorialSlide, setTutorialSlide] = useState(0);
  const [difficulty, setDifficulty] = useState<Difficulty>("medium");
  const [introChars, setIntroChars] = useState(0);
  const [introCursor, setIntroCursor] = useState(true);
  const introStopRef = useRef<(() => void) | null>(null);
  const introTextRef = useRef<HTMLDivElement>(null);
  const [logLines, setLogLines]     = useState<string[]>([]);
  const logEndRef                   = useRef<HTMLDivElement>(null);
  const logScrollRef                = useRef<HTMLDivElement>(null);
  const rightScrollRef              = useRef<HTMLDivElement>(null);
  const [logHasMore,   setLogHasMore]   = useState(false);
  const [rightHasMore, setRightHasMore] = useState(false);
  const [mapZoom, setMapZoom]       = useState(12);
  const nodesRef               = useRef<MeshNode[]>([]);
  const mapRef                 = useRef<L.Map | null>(null);
  const containerRef           = useRef<HTMLDivElement>(null);
  const polygonsRef            = useRef<Map<string, L.Polygon>>(new Map());
  const infraRingsRef          = useRef<L.Polygon[]>([]);
  const troopMarkersRef        = useRef<Map<string, L.Marker>>(new Map());
  const bridgeLinesRef         = useRef<L.Polyline[]>([]);
  const attackOverlayRef       = useRef<HTMLElement | null>(null);
  const gsRef                  = useRef<GameState | null>(null);
  const fittedRef              = useRef(false);
  const aiTimerRef             = useRef<ReturnType<typeof setTimeout> | null>(null);
  const aiFinalRef             = useRef<{ cells: Record<string, GameCell>; turn: number; reinf: number; winner: Faction | null; aiHands: Record<string, CardType[]>; aiWonLastTurn: Record<string, boolean>; playerCards: CardType[]; factionCells: Record<string, Record<string, GameCell>> } | null>(null);
  const lastCensusTurnRef      = useRef(-1);
  const loseAllTimerRef        = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingEventRef        = useRef<GameEvent | null>(null);
  const [eventBanner, setEventBanner] = useState<GameEvent | null>(null);
  const pendingRechargeRef     = useRef<string | null>(null);
  const prevCardsRef           = useRef(0);
  const [isMobile, setIsMobile] = useState(() => window.innerWidth <= 768);
  useEffect(() => {
    const handler = () => setIsMobile(window.innerWidth <= 768);
    window.addEventListener("resize", handler);
    return () => window.removeEventListener("resize", handler);
  }, []);
  gsRef.current = gs;

  // AI animation loop — fires each time the queue shrinks
  useEffect(() => {
    if (gs?.phase !== "ai_turn" || !gs.aiQueue.length) return;
    if (aiTimerRef.current) clearTimeout(aiTimerRef.current);

    const event = gs.aiQueue[0];

    // Immediate side effects (sound + map pan)
    if (event.type === "attack_round") {
      SFX.attack();
      runRoundFlash(event.toId, polygonsRef.current);
    }
    if (event.type === "attack_result") {
      event.won ? SFX.conquest() : SFX.lose();
      (attackOverlayRef.current as any)?._cleanup?.();
      attackOverlayRef.current?.remove();
      attackOverlayRef.current = null;
    }
    if (event.type === "reinforce") {
      if (mapRef.current) {
        const [lat, lon] = cellToLatLng(event.cellId);
        mapRef.current.flyTo([lat, lon], 12, { animate: true, duration: 0.45 });
      }
      // Wait for flyTo to finish + small pause, then show the reinforcement
      const cellId = event.cellId;
      const newTroops = event.troops;
      setTimeout(() => {
        SFX.reinforce();
        runRechargeFlash(cellId, polygonsRef.current);
        const cell = gsRef.current?.cells[cellId];
        const marker = troopMarkersRef.current.get(cellId);
        if (marker && cell)
          marker.setIcon(L.divIcon({ className: "", html: troopMarkerHtml(newTroops, cell.isProduction, COLORS[cell.owner], (mapRef.current?.getZoom() ?? 12) < HEX_VISIBLE_ZOOM), iconSize: [0, 0], iconAnchor: [0, 0] }));
      }, 580); // 450ms flyTo + 130ms pause
    }
    if (event.type === "census_start" && mapRef.current)
      runCensusFlash(event.cellIds, polygonsRef.current, mapRef.current);
    if (event.type === "ai_bomb_attack") {
      SFX.bomb();
      if (!event.repelled) runBombFlash(event.targetId, polygonsRef.current);
      else { playTone(660, 0.1, "sine", 0.15); setTimeout(() => playTone(880, 0.15, "sine", 0.12), 100); }
    }
    if (event.type === "attack_announce") {
      (attackOverlayRef.current as any)?._cleanup?.();
      attackOverlayRef.current?.remove();
      runRoundFlash(event.toId, polygonsRef.current);
      if (mapRef.current && containerRef.current)
        attackOverlayRef.current = runAttackArrow(event.fromId, event.toId, mapRef.current, containerRef.current);
    }
    if (event.type === "attack_announce" && mapRef.current) {
      const isBridgeAttack = !gridDisk(event.fromId, 1).includes(event.toId);
      if (isBridgeAttack) {
        const [laF, loF] = cellToLatLng(event.fromId);
        const [laT, loT] = cellToLatLng(event.toId);
        mapRef.current.fitBounds([[laF, loF], [laT, loT]], { padding: [100, 100], maxZoom: 12, animate: true, duration: 0.5 });
      } else {
        const [lat, lon] = cellToLatLng(event.fromId);
        mapRef.current.flyTo([lat, lon], 12, { animate: true, duration: 0.45 });
      }
    }
    if (event.type === "done" && aiFinalRef.current)
      pendingEventRef.current = generateEvent(aiFinalRef.current.cells, aiFinalRef.current.turn);

    if (event.type === "production_announce") {
      SFX.productionAnnounce();
    }
    if (event.type === "production_double") {
      if (mapRef.current) {
        const [lat, lon] = cellToLatLng(event.cellId);
        mapRef.current.flyTo([lat, lon], mapRef.current.getZoom(), { animate: true, duration: 0.5 });
      }
      const pdCellId = event.cellId;
      const pdTroops = event.troops;
      setTimeout(() => {
        SFX.productionDouble();
        runProductionFlash(pdCellId, polygonsRef.current);
        const cell = gsRef.current?.cells[pdCellId];
        const marker = troopMarkersRef.current.get(pdCellId);
        if (marker && cell)
          marker.setIcon(L.divIcon({ className: "", html: troopMarkerHtml(pdTroops, true, COLORS[cell.owner], (mapRef.current?.getZoom() ?? 12) < HEX_VISIBLE_ZOOM), iconSize: [0, 0], iconAnchor: [0, 0] }));
      }, 600);
    }

    let delay = 400;
    if (event.type === "faction_start")             delay = 1200;
    else if (event.type === "census_start")         delay = censusDuration(event.cellIds.length);
    else if (event.type === "ai_card_draw")         delay = 1000;
    else if (event.type === "ai_card_trade")        delay = 1600;
    else if (event.type === "ai_bomb_attack")       delay = 2000;
    else if (event.type === "reinforce")            delay = 930;
    else if (event.type === "attack_announce")      delay = 1100;
    else if (event.type === "attack_result")        delay = 700;
    else if (event.type === "production_announce")  delay = 3000;
    else if (event.type === "production_double")    delay = 1500;

    aiTimerRef.current = setTimeout(() => {
      const capturedEvent = event.type === "done" ? pendingEventRef.current : null;
      if (event.type === "done") pendingEventRef.current = null;

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

          case "ai_card_draw":
            return { ...base, combatMsg: `${NAMES[evt.faction]} recibe carta: ${CARD_META[evt.card].symbol} ${CARD_META[evt.card].label}`, aiMsg: `${NAMES[evt.faction]}: +1 carta (${evt.handSize}/5)` };

          case "ai_card_trade": {
            const em = CARD_META[evt.effectType];
            return { ...base, combatMsg: `${NAMES[evt.faction]} canjea → +${evt.bonus} tropas extra  ${em.symbol}${em.symbol}${em.symbol}`, aiMsg: `${NAMES[evt.faction]}: canjea cartas (${evt.handSize}/5 restantes)` };
          }

          case "ai_bomb_attack": {
            if (evt.repelled) {
              return {
                ...base,
                combatMsg: `◉ BOMBA ${NAMES[evt.faction]} — ANTIBOMBA activa! Ataque repelido`,
                aiMsg: `${NAMES[evt.faction]}: bomba repelida`,
              };
            }
            const tgt = prev.cells[evt.targetId];
            return {
              ...base,
              cells: { ...prev.cells, [evt.targetId]: { ...tgt, troops: evt.remaining } },
              combatMsg: `◉ BOMBA ${NAMES[evt.faction]}! ${tgt?.nodeName ?? "?"} pierde ${evt.troopsLost} tropas (${evt.remaining} restantes)`,
              aiMsg: `${NAMES[evt.faction]}: ataque bomba`,
            };
          }

          case "production_announce":
            return { ...base, aiMsg: "★ PRODUCCIÓN DE LUCAÍNA-T ★", combatMsg: "Los centros de infraestructura duplican tropas" };

          case "production_double": {
            const cell = prev.cells[evt.cellId];
            const prevTroops = Math.floor(evt.troops / 2);
            return { ...base, cells: { ...prev.cells, [evt.cellId]: { ...cell, troops: evt.troops } }, aiMsg: `★ ${cell.nodeName}: ${prevTroops} → ${evt.troops}`, combatMsg: "Lucaína-T duplicando producción..." };
          }

          case "done": {
            const f = aiFinalRef.current!;
            let finalCells = f.cells;
            if (capturedEvent?.affectedCells?.length) {
              const nc = { ...finalCells };
              for (const [cellId, delta] of Object.entries(capturedEvent.deltas)) {
                if (nc[cellId]) nc[cellId] = { ...nc[cellId], troops: Math.max(1, nc[cellId].troops + delta) };
              }
              finalCells = nc;
            }
            let newCards = f.playerCards;
            let newPending = prev.pendingCard;
            if (prev.wonCellThisTurn) {
              const drawn = drawCard(newCards);
              if (newCards.length < 5) newCards = [...newCards, drawn];
              else if (!prev.pendingCard)  newPending = drawn;
            }
            return {
              ...base, cells: finalCells, phase: f.winner ? "game_over" : "reinforcement",
              turn: f.turn, reinforcementsLeft: f.reinf, winner: f.winner,
              currentFaction: f.winner ?? "player",
              aiQueue: [], aiMsg: "", attackSource: null, attackTarget: null,
              cards: newCards, pendingCard: newPending, wonCellThisTurn: false,
              aiHands: f.aiHands, aiWonLastTurn: f.aiWonLastTurn,
              combatMsg: f.winner ? `¡${NAMES[f.winner]} dominó la Mesh!` : "",
              log: [`TURNO ${f.turn} — +${f.reinf} TROPAS`, ...prev.log.slice(0, 9)],
            };
          }

          default: return base;
        }
      });

      if (capturedEvent) {
        setEventBanner(capturedEvent);
        if (capturedEvent.targetCell && mapRef.current) {
          const [lat, lon] = cellToLatLng(capturedEvent.targetCell);
          mapRef.current.flyTo([lat, lon], 12, { animate: true, duration: 0.5 });
        }
        if (capturedEvent.type === "POSITIVE") {
          playTone(523, 0.1, "sine", 0.10);
          setTimeout(() => playTone(659, 0.12, "sine", 0.12), 110);
          setTimeout(() => playTone(784, 0.14, "square", 0.10), 240);
        } else if (capturedEvent.type === "NEGATIVE") {
          playTone(300, 0.2, "sawtooth", 0.15);
          setTimeout(() => playTone(240, 0.2, "sawtooth", 0.13), 160);
          setTimeout(() => playTone(180, 0.25, "sawtooth", 0.11), 340);
        } else {
          playTone(440, 0.15, "sine", 0.08);
        }
        capturedEvent.affectedCells.forEach((cellId, i) => {
          setTimeout(() => {
            if (capturedEvent.type === "POSITIVE") runRechargeFlash(cellId, polygonsRef.current);
            else runBombFlash(cellId, polygonsRef.current);
          }, 600 + i * 150);
        });
      }
    }, delay);

    return () => { if (aiTimerRef.current) clearTimeout(aiTimerRef.current); };
  }, [gs?.phase, gs?.aiQueue?.length]);

  useEffect(() => { if (!cardFlash) setSelectedCards([]); }, [cardFlash]);

  function triggerLoseAll() {
    setLoseAllFlash(true);
    setCardFlash(true);
    [350, 270, 200, 150].forEach((f, i) => setTimeout(() => playTone(f, 0.4, "sawtooth", 0.2), i * 200));
    setGs(prev => prev ? { ...prev, pendingCard: null, combatMsg: "☠ ¡PERDISTE TODAS TUS CARTAS!" } : prev);
    if (loseAllTimerRef.current) clearTimeout(loseAllTimerRef.current);
    loseAllTimerRef.current = setTimeout(() => {
      setGs(prev => prev ? { ...prev, cards: [], combatMsg: "" } : prev);
      prevCardsRef.current = 0;
      setLoseAllFlash(false);
      setCardFlash(false);
      setSelectedCards([]);
    }, 3000);
  }

  // Show card panel when cards increase; detect lose_all in hand
  useEffect(() => {
    const cur = gs?.cards.length ?? 0;
    if (cur > prevCardsRef.current) {
      setCardFlash(true);
      const newestCard = gs?.cards[cur - 1];
      if (newestCard === "lose_all") triggerLoseAll();
    }
    prevCardsRef.current = cur;
  }, [gs?.cards.length]);

  // Pending card arrived (hand was full) — if it's lose_all, auto-trigger immediately
  useEffect(() => {
    if (!gs?.pendingCard) return;
    if (gs.pendingCard === "lose_all") {
      triggerLoseAll();
      return;
    }
    setCardFlash(true);
  }, [gs?.pendingCard]);

  // Accumulate log messages from combatMsg and significant aiMsg changes
  useEffect(() => {
    const msg = gs?.combatMsg;
    if (!msg) return;
    // Filter noisy intermediates
    if (msg.startsWith("Ataque:") && msg.includes("Defensa:")) return;
    setLogLines(prev => {
      if (prev[prev.length - 1] === msg) return prev;
      const next = [...prev, msg];
      return next.length > 300 ? next.slice(-300) : next;
    });
  }, [gs?.combatMsg]);

  useEffect(() => {
    const msg = gs?.aiMsg;
    if (!msg) return;
    // Only log significant AI messages (not per-reinforce noise)
    if (msg.includes(": refuerza ")) return;
    if (msg.includes(" territorios")) return;
    setLogLines(prev => {
      if (prev[prev.length - 1] === msg) return prev;
      const next = [...prev, msg];
      return next.length > 300 ? next.slice(-300) : next;
    });
  }, [gs?.aiMsg]);

  // Add turn separator when turn advances
  useEffect(() => {
    if (!gs?.turn) return;
    setLogLines(prev => {
      const sep = `── TURNO ${gs.turn} ──`;
      if (prev[prev.length - 1] === sep) return prev;
      return [...prev, sep];
    });
  }, [gs?.turn]);

  // Auto-scroll log to bottom on new entries
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logLines]);

  // Detect scroll overflow for "more content" arrows
  useEffect(() => {
    const el = logScrollRef.current;
    if (!el) return;
    const check = () => setLogHasMore(el.scrollTop + el.clientHeight < el.scrollHeight - 4);
    check();
    el.addEventListener("scroll", check);
    return () => el.removeEventListener("scroll", check);
  }, [logLines]);

  useEffect(() => {
    const el = rightScrollRef.current;
    if (!el) return;
    const check = () => setRightHasMore(el.scrollTop + el.clientHeight < el.scrollHeight - 4);
    check();
    el.addEventListener("scroll", check);
    return () => el.removeEventListener("scroll", check);
  });

  // Re-render troop markers when zoom crosses the hex-visibility threshold
  useEffect(() => {
    const gs = gsRef.current;
    if (!gs) return;
    const factionBg = mapZoom < HEX_VISIBLE_ZOOM;
    for (const [cellId, marker] of troopMarkersRef.current) {
      const cell = gs.cells[cellId];
      if (!cell) continue;
      marker.setIcon(L.divIcon({
        className: "",
        html: troopMarkerHtml(cell.troops, cell.isProduction, COLORS[cell.owner], factionBg),
        iconSize: [0, 0], iconAnchor: [0, 0],
      }));
    }
  }, [mapZoom]);

  // Blink toggle for lose_all animation
  useEffect(() => {
    if (!loseAllFlash) { setBlinkVisible(true); return; }
    const id = setInterval(() => setBlinkVisible(v => !v), 280);
    return () => clearInterval(id);
  }, [loseAllFlash]);

  // Intro cursor blink
  useEffect(() => {
    if (introState !== "typing") { setIntroCursor(true); return; }
    const id = setInterval(() => setIntroCursor(v => !v), 500);
    return () => clearInterval(id);
  }, [introState]);

  // Intro typewriter
  useEffect(() => {
    if (introState !== "typing") return;
    let pos = 0;
    let timerId: ReturnType<typeof setTimeout>;
    const advance = () => {
      if (pos >= INTRO_TEXT.length) { setIntroState("done"); return; }
      const ch = INTRO_TEXT[pos++];
      setIntroChars(pos);
      if (ch !== "\n" && ch !== " ") {
        playTone(200 + Math.random() * 80, 0.045, "square", 0.06);
      }
      let delay = 52;
      if (ch === "\n" && INTRO_TEXT[pos] === "\n") delay = 700;
      else if (ch === "\n") delay = 140;
      else if (ch === "." || ch === "!" || ch === "¡") delay = 380;
      timerId = setTimeout(advance, delay);
    };
    timerId = setTimeout(advance, 600);
    return () => clearTimeout(timerId);
  }, [introState]);

  // Auto-scroll intro text to bottom as it types and when done
  useEffect(() => {
    if (introTextRef.current)
      introTextRef.current.scrollTop = introTextRef.current.scrollHeight;
  }, [introChars]);

  useEffect(() => {
    if (introState === "done" && introTextRef.current)
      introTextRef.current.scrollTop = introTextRef.current.scrollHeight;
  }, [introState]);

  useEffect(() => {
    fetchGraph().then(d => { nodesRef.current = d.nodes; setReady(true); });
  }, []);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    mapRef.current = L.map(containerRef.current, { center: [-38.72, -62.26], zoom: 12, zoomControl: false });
    L.control.zoom({ position: "bottomright" }).addTo(mapRef.current);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap", maxZoom: 19,
    }).addTo(mapRef.current);
    mapRef.current.on("zoomend", () => setMapZoom(mapRef.current!.getZoom()));
    return () => { mapRef.current?.remove(); mapRef.current = null; };
  }, []);

  useEffect(() => {
    if (!mapRef.current || !gs) return;
    const map = mapRef.current;

    polygonsRef.current.forEach(p => p.remove()); polygonsRef.current.clear();
    infraRingsRef.current.forEach(p => p.remove()); infraRingsRef.current = [];
    troopMarkersRef.current.forEach(m => m.remove()); troopMarkersRef.current.clear();
    bridgeLinesRef.current.forEach(l => l.remove()); bridgeLinesRef.current = [];

    // Compute highlight sets
    const validTargets      = new Set<string>();
    const validSources      = new Set<string>();
    const validBombTargets  = new Set<string>();

    if (gs.phase === "bomb_target") {
      Object.values(gs.cells).forEach(c => { if (c.owner !== "player") validBombTargets.add(c.h3Index); });
    }
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
      const isVBT    = validBombTargets.has(cell.h3Index);

      const fillOp   = isAtk ? 1.0 : isSrc ? 1.0 : isVT ? 0.95 : isVS ? 0.95 : isVBT ? 0.95 : 0.95;
      const border   = isAtk ? "#ffeb3b" : isSrc ? "#ffffff" : isVT ? "#ffeb3b" : isVS ? "#ffffff" : isVBT ? "#ff7043" : cell.isProduction ? "#ffffff" : color;
      const bWeight  = isAtk || isSrc || isVT || isVS || isVBT ? 2.5 : cell.isProduction ? 2 : 1.5;

      const poly = L.polygon(bnd, { fillColor: color, fillOpacity: fillOp, color: border, weight: bWeight });

      poly.bindTooltip(
        `<b style="color:${color}">${NAMES[cell.owner]}</b> · ${cell.troops} tropas<br>` +
        `${cell.isProduction ? "★" : cell.isSynthetic ? "◈" : "📡"} ${cell.nodeName}` +
        (cell.isNodeActive ? ` <span style="color:#4caf50">●</span>` : ""),
        { direction: "top", sticky: false, offset: [0, -12], className: "mesh-tooltip" },
      );

      const cellId = cell.h3Index;
      poly.on("click", () => {
        setCardFlash(false);
        const state = gsRef.current;
        if (!state || state.phase === "game_over" || state.phase === "action") return;
        const cur = state.cells[cellId];

        if (state.phase === "reinforcement") {
          if (cur.owner !== "player" || state.reinforcementsLeft <= 0) return;
          SFX.reinforce();
          pendingRechargeRef.current = cellId;
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

        if (state.phase === "bomb_target") {
          if (cur.owner === "player") return;
          SFX.bomb();
          runBombFlash(cellId, polygonsRef.current);
          setGs(prev => {
            if (!prev) return prev;
            const cell = prev.cells[cellId];
            const defFaction = cell.owner;
            const defHand    = prev.aiHands[defFaction] ?? [];
            const abIdx      = defHand.indexOf("antibomba");
            if (abIdx >= 0) {
              const newDefHand = defHand.filter((_, i) => i !== abIdx);
              return {
                ...prev, phase: "action", attackTarget: null,
                aiHands: { ...prev.aiHands, [defFaction]: newDefHand },
                combatMsg: `ANTIBOMBA! ${NAMES[defFaction]} repele el ataque`,
                log: [`ANTIBOMBA de ${NAMES[defFaction]} repelió la bomba`, ...prev.log.slice(0, 9)],
              };
            }
            const troops    = cell.troops;
            const lossPct   = 0.5 + Math.random() * 0.4;
            const lost      = Math.max(1, Math.floor(troops * lossPct));
            const remaining = Math.max(1, troops - lost);
            return {
              ...prev, phase: "action", attackTarget: null,
              cells: { ...prev.cells, [cellId]: { ...cell, troops: remaining } },
              combatMsg: `◉ BOMBA! ${cell.nodeName} pierde ${lost} tropas (${remaining} restantes)`,
              log: [`◉ BOMBA: ${cell.nodeName} ${troops}→${remaining} tropas`, ...prev.log.slice(0, 9)],
            };
          });
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
          SFX.select();
          setGs(prev => prev ? {
            ...prev, phase: "move_confirm",
            attackTarget: cellId, postConquestTroops: 1, combatMsg: "",
          } : prev);
          return;
        }
      });

      poly.addTo(map);
      polygonsRef.current.set(cell.h3Index, poly);

      if (cell.isProduction) {
        const [cLat, cLon] = cellToLatLng(cell.h3Index);
        for (const ratio of [0.75, 0.5, 0.25]) {
          const ring = bnd.map(([la, lo]) => [cLat + (la - cLat) * ratio, cLon + (lo - cLon) * ratio] as [number, number]);
          const rPoly = L.polygon(ring, { fillOpacity: 0, color: "#ffffff", weight: 1.5, opacity: 0.7, interactive: false });
          rPoly.addTo(map);
          infraRingsRef.current.push(rPoly);
        }
      }

      // Troop count marker
      const [cLat, cLon] = cellToLatLng(cell.h3Index);
      const tm = L.marker([cLat, cLon], {
        icon: L.divIcon({ className: "", html: troopMarkerHtml(cell.troops, cell.isProduction, COLORS[cell.owner], mapZoom < HEX_VISIBLE_ZOOM), iconSize: [0, 0], iconAnchor: [0, 0] }),
        interactive: false, zIndexOffset: 500,
      });
      tm.addTo(map);
      troopMarkersRef.current.set(cell.h3Index, tm);
    }

    // Bridges
    for (const bridge of gs.bridges) {
      const centerA = cellToLatLng(bridge.fromH3) as [number, number];
      const centerB = cellToLatLng(bridge.toH3)   as [number, number];
      const boundA  = cellToBoundary(bridge.fromH3) as [number, number][];
      const boundB  = cellToBoundary(bridge.toH3)   as [number, number][];
      const ptA = hexEdgePoint(centerA, centerB, boundA);
      const ptB = hexEdgePoint(centerB, centerA, boundB);
      const isAttackBridge = (gs.attackSource === bridge.fromH3 && gs.attackTarget === bridge.toH3)
                          || (gs.attackSource === bridge.toH3   && gs.attackTarget === bridge.fromH3);
      const isMoveBridge   = gs.moveSource === bridge.fromH3 || gs.moveSource === bridge.toH3;
      const lit = isAttackBridge || isMoveBridge;
      const line = L.polyline([ptA, ptB], {
        color: lit ? "#ffeb3b" : "#000", weight: lit ? 6 : 4, opacity: lit ? 1 : 0.85,
      });
      line.addTo(map);
      bridgeLinesRef.current.push(line);
    }

    if (!fittedRef.current && Object.keys(gs.cells).length > 0) {
      fittedRef.current = true;
      map.fitBounds(L.latLngBounds(Object.keys(gs.cells).map(id => cellToLatLng(id) as [number, number])), { padding: [40, 40] });
    }

    if (pendingRechargeRef.current) {
      const id = pendingRechargeRef.current;
      pendingRechargeRef.current = null;
      runRechargeFlash(id, polygonsRef.current);
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

  function handleButton(action: "attack" | "attack_confirm" | "troop_plus" | "troop_minus" | "troop_plus_big" | "troop_minus_big" | "troop_confirm" | "move" | "pass" | "skip_ai" | "cancel") {
    setCardFlash(false);
    // One manual round of combat per click
    if (action === "attack_confirm") {
      SFX.attack();
      if (gs?.attackSource && gs?.attackTarget) {
        runRoundFlash(gs.attackTarget, polygonsRef.current);
        if (mapRef.current && containerRef.current) {
          (attackOverlayRef.current as any)?._cleanup?.();
          attackOverlayRef.current?.remove();
          attackOverlayRef.current = runAttackArrow(gs.attackSource, gs.attackTarget, mapRef.current, containerRef.current);
          setTimeout(() => { (attackOverlayRef.current as any)?._cleanup?.(); attackOverlayRef.current?.remove(); attackOverlayRef.current = null; }, 520);
        }
      }
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
                wonCellThisTurn: true,
                combatMsg: `¡CONQUISTA! — ${roundMsg}`,
                log: [`¡CONQUISTA! +1 tropa`, ...prev.log.slice(0, 9)],
              };
            }
            // More troops — let player choose how many to move
            next[prev.attackTarget] = { ...to, owner: from.owner, troops: 0 };
            return {
              ...prev, cells: next,
              phase: "post_conquest",
              wonCellThisTurn: true,
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

      if ((action === "troop_plus" || action === "troop_plus_big") && (prev.phase === "post_conquest" || prev.phase === "move_confirm")) {
        const max  = prev.phase === "move_confirm"
          ? (prev.cells[prev.moveSource!]?.troops ?? 2) - 1
          : (prev.cells[prev.attackSource!]?.troops ?? 2) - 1;
        const step = action === "troop_plus_big" ? Math.ceil(max / 3) : 1;
        return { ...prev, postConquestTroops: Math.min(prev.postConquestTroops + step, max) };
      }
      if ((action === "troop_minus" || action === "troop_minus_big") && (prev.phase === "post_conquest" || prev.phase === "move_confirm")) {
        const max  = prev.phase === "move_confirm"
          ? (prev.cells[prev.moveSource!]?.troops ?? 2) - 1
          : (prev.cells[prev.attackSource!]?.troops ?? 2) - 1;
        const step = action === "troop_minus_big" ? Math.ceil(max / 3) : 1;
        return { ...prev, postConquestTroops: Math.max(prev.postConquestTroops - step, 1) };
      }
      if (action === "troop_confirm" && prev.phase === "move_confirm") {
        const from = prev.cells[prev.moveSource!];
        const to   = prev.cells[prev.attackTarget!];
        const n    = prev.postConquestTroops;
        const newCells = {
          ...prev.cells,
          [prev.moveSource!]:   { ...from, troops: from.troops - n },
          [prev.attackTarget!]: { ...to,   troops: to.troops + n },
        };
        SFX.move();
        const newTurn = prev.turn + 1;
        const { events, finalCells, winner, reinf, newAiHands, newAiWonLastTurn, newPlayerCards, factionCells: fc1 } =
          planAITurns(newCells, prev.bridges, prev.islands, newTurn, prev.aiHands, prev.aiWonLastTurn, prev.cards);
        aiFinalRef.current = { cells: finalCells, turn: newTurn, reinf, winner, aiHands: newAiHands, aiWonLastTurn: newAiWonLastTurn, playerCards: newPlayerCards, factionCells: fc1 };
        return {
          ...prev, cells: newCells,
          phase: "ai_turn", aiQueue: events, aiMsg: "",
          moveSource: null, attackTarget: null, postConquestTroops: 0, combatMsg: "",
        };
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
          wonCellThisTurn: true,
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
        const { events, finalCells, winner, reinf, newAiHands, newAiWonLastTurn, newPlayerCards, factionCells: fc2 } = planAITurns(prev.cells, prev.bridges, prev.islands, newTurn, prev.aiHands, prev.aiWonLastTurn, prev.cards);
        aiFinalRef.current = { cells: finalCells, turn: newTurn, reinf, winner, aiHands: newAiHands, aiWonLastTurn: newAiWonLastTurn, playerCards: newPlayerCards, factionCells: fc2 };
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
        attackOverlayRef.current?.remove();
        attackOverlayRef.current = null;
        const f = aiFinalRef.current;
        if (!f) return prev;

        // Find next faction boundary in remaining queue
        const queue = prev.aiQueue;
        const atStart = queue[0]?.type === "faction_start";
        // If we're at a faction_start, skip this whole faction → find the NEXT faction_start
        // If we're mid-faction, find the NEXT faction_start
        const searchFrom = atStart ? 1 : 0;
        const nextFSIdx = queue.findIndex((e, i) => i >= searchFrom && e.type === "faction_start");

        if (nextFSIdx >= 0) {
          // Jump to next faction: restore cells from the faction that just finished
          const nextFaction = (queue[nextFSIdx] as Extract<AIEvent, { type: "faction_start" }>).faction;
          const AI_ORDER: Faction[] = ["ai1", "ai2", "ai3"];
          const nextIdx = AI_ORDER.indexOf(nextFaction);
          const snapCells = nextIdx > 0 ? f.factionCells[AI_ORDER[nextIdx - 1]] : prev.cells;
          return {
            ...prev,
            cells: snapCells,
            aiQueue: queue.slice(nextFSIdx),
            attackSource: null, attackTarget: null,
          };
        }

        // No more factions — jump straight to final state
        let newCards = f.playerCards;
        let newPending = prev.pendingCard;
        if (prev.wonCellThisTurn) {
          const drawn = drawCard(newCards);
          if (newCards.length < 5) newCards = [...newCards, drawn];
          else if (!prev.pendingCard)  newPending = drawn;
        }
        return {
          ...prev, cells: f.cells, phase: f.winner ? "game_over" : "reinforcement",
          turn: f.turn, reinforcementsLeft: f.reinf, winner: f.winner,
          currentFaction: f.winner ?? "player",
          aiQueue: [], aiMsg: "", attackSource: null, attackTarget: null,
          cards: newCards, pendingCard: newPending, wonCellThisTurn: false,
          aiHands: f.aiHands, aiWonLastTurn: f.aiWonLastTurn,
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
    setGs(initGame(nodesRef.current, difficulty));
  }

  function handleTrade(indices: number[], effectType: CardType) {
    setCardFlash(false);
    setGs(prev => {
      if (!prev || prev.phase !== "reinforcement") return prev;
      const newCards = prev.cards.filter((_, i) => !indices.includes(i));
      if (effectType === "bomba") {
        return {
          ...prev, cards: newCards,
          phase: "bomb_target",
          attackTarget: null, attackSource: null,
          combatMsg: "◉ BOMBA LISTA — seleccioná el objetivo",
        };
      }
      const bonus = troopBonus(effectType);
      const meta  = CARD_META[effectType];
      return {
        ...prev, cards: newCards,
        reinforcementsLeft: prev.reinforcementsLeft + bonus,
        combatMsg: bonus > 0
          ? `+${bonus} TROPAS al refuerzo (${meta.label} canjeadas)`
          : `${meta.label} — efecto pendiente`,
      };
    });
  }

  function handleDiscard(index: number) {
    const isLoseAll = gsRef.current?.pendingCard === "lose_all";
    setGs(prev => {
      if (!prev?.pendingCard) return prev;
      const kept = prev.cards.filter((_, i) => i !== index);
      return { ...prev, cards: [...kept, prev.pendingCard!], pendingCard: null };
    });
    if (isLoseAll) {
      setLoseAllFlash(true);
      [350, 270, 200, 150].forEach((f, i) => setTimeout(() => playTone(f, 0.4, "sawtooth", 0.2), i * 200));
      if (loseAllTimerRef.current) clearTimeout(loseAllTimerRef.current);
      loseAllTimerRef.current = setTimeout(() => {
        setGs(prev => prev ? { ...prev, cards: [] } : prev);
        prevCardsRef.current = 0;
        setLoseAllFlash(false);
        setCardFlash(false);
        setSelectedCards([]);
      }, 3000);
    }
  }

  // ── render ────────────────────────────────────────────────────────────────────

  const selectionEffect = cardFlash && gs ? getSelectionEffect(gs.cards, selectedCards) : null;

  const factionList: Faction[] = ["player", "ai1", "ai2", "ai3"];

  const totalCells = gs ? Object.keys(gs.cells).length : 0;
  const scoreRows = gs
    ? factionList.map(f => ({
        f,
        territories: Object.values(gs.cells).filter(c => c.owner === f).length,
        islands:     countControlledIslands(gs.islands, gs.cells, f),
      }))
    : [];

  const [statusLine1] = gs ? statusText(gs) : ["", ""];

  const btnStyle = (color: string, disabled = false): React.CSSProperties => ({
    background: disabled ? "transparent" : `${color}18`,
    color: disabled ? "#1e2d3d" : color,
    border: `1px solid ${disabled ? "#0d1a28" : color}`,
    borderRadius: 2, padding: "8px 16px",
    fontFamily: "monospace", fontWeight: 700, fontSize: 13,
    cursor: disabled ? "not-allowed" : "pointer",
    letterSpacing: 2, minWidth: 80,
    boxShadow: disabled ? "none" : `0 0 10px ${color}55`,
    textTransform: "uppercase" as const,
  });

  const inAction       = gs?.phase === "action";
  const inConfirm      = gs?.phase === "attack_confirm";
  const inPostConquest = gs?.phase === "post_conquest";
  const inMoveConfirm  = gs?.phase === "move_confirm";
  const inAiTurn       = gs?.phase === "ai_turn";
  const canCancel      = gs ? !["action", "reinforcement", "post_conquest", "ai_turn", "game_over"].includes(gs.phase) : false;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 36px)", background: "#1a2332" }}>

      {/* Map area */}
      <div style={{ flex: 1, minHeight: 0, position: "relative" }}>
        <div ref={containerRef} style={{ width: "100%", height: "100%" }} />

        {/* Score panel — top left overlay */}
        {gs && (
          <>
            <style>{`
              @keyframes mw-pulse {
                0%, 100% { background-color: transparent; }
                50%       { background-color: var(--pulse-color); }
              }
              @keyframes mw-scanline {
                0%   { transform: translateY(-100%); }
                100% { transform: translateY(400%); }
              }
              @keyframes mw-blink {
                0%, 49% { opacity: 1; }
                50%, 100% { opacity: 0; }
              }
              @keyframes mw-border-pulse {
                0%, 100% { border-left-color: var(--faction-color); box-shadow: inset 3px 0 8px var(--faction-color-dim); }
                50%       { border-left-color: #fff; box-shadow: inset 3px 0 16px var(--faction-color); }
              }
            `}</style>
            <div style={{
              position: "absolute", top: 12, left: 12, zIndex: 1000,
              background: "rgba(4, 8, 16, 0.92)",
              backdropFilter: "blur(12px)",
              WebkitBackdropFilter: "blur(12px)",
              border: "1px solid #00e5ff33",
              borderRadius: 3,
              overflow: "hidden",
              padding: "0",
              fontFamily: "monospace",
              minWidth: isMobile ? 190 : 240,
              boxShadow: "0 0 24px #00e5ff18, 0 4px 20px rgba(0,0,0,0.6), inset 0 0 40px rgba(0,230,255,0.03)",
            }}>
              {/* Gradient top accent */}
              <div style={{ height: 2, background: "linear-gradient(90deg, #00e5ff, #aa00ff, #ff0099)" }} />

              {/* Scanline overlay */}
              <div style={{
                position: "absolute", inset: 0, pointerEvents: "none", zIndex: 1,
                background: "repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,0,0,0.08) 2px, rgba(0,0,0,0.08) 4px)",
              }} />

              <div style={{ padding: "8px 12px 10px", position: "relative", zIndex: 2 }}>
                {/* Header */}
                <div style={{
                  display: "flex", justifyContent: "space-between", alignItems: "center",
                  marginBottom: 8, paddingBottom: 6,
                  borderBottom: "1px solid #00e5ff18",
                }}>
                  <span style={{ color: "#00e5ffbb", fontSize: 10, fontWeight: 800, letterSpacing: 4 }}>
                    MESH WARS
                  </span>
                  <span style={{ color: "#00e5ff", fontSize: 12, fontWeight: 800, letterSpacing: 2, textShadow: "0 0 10px #00e5ff" }}>
                    TURNO {gs.turn}
                  </span>
                </div>

                {/* Faction rows */}
                {scoreRows.map(({ f, territories, islands }) => {
                  const isActive = f === gs.currentFaction && gs.phase !== "game_over";
                  const pct = totalCells > 0 ? (territories / totalCells) * 100 : 0;
                  return (
                    <div
                      key={f}
                      style={{
                        display: "flex", alignItems: "center", gap: 8,
                        padding: "5px 6px",
                        marginBottom: 2,
                        borderRadius: 2,
                        borderLeft: `3px solid ${isActive ? COLORS[f] : COLORS[f] + "30"}`,
                        ["--pulse-color" as any]: `${COLORS[f]}20`,
                        ["--faction-color" as any]: COLORS[f],
                        ["--faction-color-dim" as any]: `${COLORS[f]}44`,
                        animation: isActive ? "mw-border-pulse 0.9s ease-in-out infinite" : "none",
                        transition: "border-color 0.3s",
                        background: isActive ? `${COLORS[f]}0f` : "transparent",
                      }}
                    >
                      {/* Name */}
                      <span style={{
                        color: isActive ? COLORS[f] : COLORS[f] + "cc",
                        width: 68, fontSize: isActive ? 15 : 13,
                        fontWeight: 800, letterSpacing: 2,
                        textShadow: isActive ? `0 0 12px ${COLORS[f]}, 0 0 4px ${COLORS[f]}` : `0 0 6px ${COLORS[f]}66`,
                        transition: "all 0.3s",
                        display: "flex", alignItems: "center", gap: 4,
                      }}>
                        {isActive && (
                          <span style={{
                            fontSize: 10, lineHeight: 1,
                            color: COLORS[f],
                            textShadow: `0 0 8px ${COLORS[f]}`,
                            animation: "mw-blink 0.7s step-end infinite",
                            flexShrink: 0,
                          }}>►</span>
                        )}
                        {NAMES[f]}
                      </span>

                      {/* Bar + numbers */}
                      <div style={{ flex: 1 }}>
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 3 }}>
                          <span style={{
                            color: isActive ? "#ffffff" : COLORS[f] + "dd",
                            fontSize: isActive ? 24 : 17,
                            fontWeight: 800, lineHeight: 1,
                            textShadow: isActive ? `0 0 14px ${COLORS[f]}, 0 0 6px #fff` : `0 0 8px ${COLORS[f]}88`,
                            transition: "all 0.3s",
                          }}>
                            {territories}
                          </span>
                          {islands > 0 && (
                            <span style={{
                              color: isActive ? "#ffcc00" : "#ffcc0099",
                              fontSize: 10, fontWeight: 700,
                              textShadow: isActive ? "0 0 8px #ffcc00" : "none",
                            }}>
                              {islands}★
                            </span>
                          )}
                        </div>
                        <div style={{ height: isActive ? 3 : 2, background: "#0a1828", borderRadius: 1, overflow: "hidden", transition: "height 0.3s" }}>
                          <div style={{
                            height: "100%", width: `${pct}%`,
                            background: isActive
                              ? `linear-gradient(90deg, ${COLORS[f]}88, ${COLORS[f]})`
                              : COLORS[f] + "44",
                            borderRadius: 1,
                            transition: "width 0.5s ease",
                            boxShadow: isActive ? `0 0 6px ${COLORS[f]}` : "none",
                          }} />
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </>
        )}

        {/* Card hand — bottom right of map (compact overlay) */}
        {gs && gs.cards.length > 0 && !cardFlash && (
          <div style={{
            position: "absolute", bottom: 10, right: 10, zIndex: 1000,
            display: "flex", gap: 4, alignItems: "flex-end",
          }}>
            {gs.cards.map((card, i) => {
              const m = CARD_META[card];
              return (
                <div key={i} style={{
                  width: 56, height: 80,
                  background: m.bg,
                  border: `2px solid ${m.color}`,
                  borderRadius: 6,
                  display: "flex", flexDirection: "column",
                  alignItems: "center", justifyContent: "center",
                  gap: 3,
                  boxShadow: `0 3px 10px rgba(0,0,0,0.7)`,
                  fontFamily: "monospace",
                }}>
                  <span style={{ fontSize: 32, color: m.color, lineHeight: 1 }}>{m.symbol}</span>
                  <span style={{ fontSize: 13, color: m.color, fontWeight: 700, lineHeight: 1 }}>{m.label}</span>
                  <span style={{ fontSize: 10, color: m.color, opacity: 0.75, lineHeight: 1 }}>{m.sub}</span>
                </div>
              );
            })}
          </div>
        )}

        {/* Random event banner */}
        {eventBanner && gs && (() => {
          const fColor  = eventBanner.targetFaction ? COLORS[eventBanner.targetFaction] : "#78909c";
          const tColor  = eventBanner.type === "POSITIVE" ? "#00e5ff" : eventBanner.type === "NEGATIVE" ? "#ff1744" : "#ffd740";
          const tIcon   = eventBanner.type === "POSITIVE" ? "▲" : eventBanner.type === "NEGATIVE" ? "▼" : "◆";
          const tLabel  = eventBanner.type === "POSITIVE" ? "EVENTO POSITIVO" : eventBanner.type === "NEGATIVE" ? "EVENTO NEGATIVO" : "EVENTO NEUTRO";
          const sevDots = eventBanner.severity === "MINOR" ? "█ □ □" : eventBanner.severity === "MEDIUM" ? "█ █ □" : "█ █ █";
          return (
            <>
              <div style={{ position: "absolute", inset: 0, zIndex: 1500, background: "rgba(0,0,0,0.55)" }} onClick={() => setEventBanner(null)} />
              <div style={{
                position: "absolute", top: "18%", left: "50%", transform: "translateX(-50%)",
                zIndex: 1600, background: "#060d14",
                border: `1px solid ${tColor}`,
                borderTop: `3px solid ${tColor}`,
                borderRadius: 3, padding: 0, minWidth: isMobile ? "92vw" : 380, maxWidth: isMobile ? "92vw" : 500,
                boxShadow: `0 0 30px ${tColor}44, 0 0 80px ${tColor}18, inset 0 0 40px rgba(0,0,0,0.6)`,
                fontFamily: "monospace", overflow: "hidden",
              }}>
                {/* Header */}
                <div style={{
                  background: `linear-gradient(90deg, ${tColor}25 0%, ${tColor}06 100%)`,
                  borderBottom: `1px solid ${tColor}44`,
                  padding: "10px 16px",
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontSize: 17, color: tColor, lineHeight: 1, textShadow: `0 0 14px ${tColor}` }}>{tIcon}</span>
                    <span style={{ fontSize: 10, color: tColor, letterSpacing: 3, fontWeight: 700, textShadow: `0 0 8px ${tColor}` }}>{tLabel}</span>
                  </div>
                  <span style={{ fontSize: 12, color: tColor, letterSpacing: 1, opacity: 0.75, textShadow: `0 0 6px ${tColor}` }}>{sevDots}</span>
                </div>
                {/* Body */}
                <div style={{ padding: "16px 20px 20px" }}>
                  <div style={{ display: "flex", alignItems: "flex-start", gap: 12, marginBottom: 14 }}>
                    <div style={{
                      width: 3, alignSelf: "stretch", flexShrink: 0,
                      background: fColor, boxShadow: `0 0 10px ${fColor}`, borderRadius: 2,
                    }} />
                    <div>
                      <div style={{ fontSize: 16, fontWeight: 800, color: "#fff", letterSpacing: 2, textShadow: "0 0 10px rgba(255,255,255,0.35)" }}>
                        {eventBanner.title.toUpperCase()}
                      </div>
                      <div style={{ fontSize: 10, color: fColor, marginTop: 4, letterSpacing: 2, textShadow: `0 0 6px ${fColor}` }}>
                        {eventBanner.targetFaction ? NAMES[eventBanner.targetFaction].toUpperCase() : "TODAS LAS FACCIONES"}
                      </div>
                    </div>
                  </div>
                  <p style={{ color: "#b8cfe0", fontSize: 13, lineHeight: 1.75, margin: "0 0 18px", paddingLeft: 15 }}>
                    {eventBanner.description}
                  </p>
                  <button onClick={() => setEventBanner(null)} style={{ ...btnStyle(tColor), width: "100%", fontSize: 12, padding: "9px" }}>
                    ENTENDIDO
                  </button>
                </div>
              </div>
            </>
          );
        })()}

        {/* Start screen */}
        {!gs && introState === "start" && (
          <div style={{
            position: "absolute", inset: 0, display: "flex", flexDirection: "column",
            alignItems: "center", justifyContent: "center", background: "rgba(0,0,0,0.7)", zIndex: 2000,
          }}>
            <img src={meshwarsLogo} alt="MESH WARS" style={{ width: 480, maxWidth: "80vw", marginBottom: 8 }} />
            <div style={{ color: "#8b949e", marginBottom: 24, fontFamily: "monospace" }}>
              {nodesReady ? "Datos de la mesh cargados" : "Cargando nodos..."}
            </div>
            {nodesReady && (
              <div style={{ display: "flex", gap: isMobile ? 16 : 28, alignItems: "center", flexDirection: isMobile ? "column" : "row" }}>
                {/* Difficulty selector — vertical column */}
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <div style={{ color: "#4a8a9a", fontFamily: "monospace", fontSize: 10, letterSpacing: 3, marginBottom: 4, textAlign: "center" }}>DIFICULTAD</div>
                  {(["easy","medium","hard","vhard"] as Difficulty[]).map(d => {
                    const active = difficulty === d;
                    const color  = d === "easy" ? "#00ff88" : d === "medium" ? "#00e5ff" : d === "hard" ? "#ffcc00" : "#ff3355";
                    return (
                      <button key={d} onClick={() => { setDifficulty(d); playDiffSound(d); }} style={{
                        background: active ? `${color}22` : "#0a1a24",
                        color: active ? color : "#6a9ab0",
                        border: `1px solid ${active ? color : "#2a4a5a"}`,
                        borderRadius: 2, padding: "6px 16px",
                        fontFamily: "monospace", fontWeight: 700, fontSize: 11,
                        cursor: "pointer", letterSpacing: 2,
                        boxShadow: active ? `0 0 10px ${color}55` : "none",
                        textTransform: "uppercase" as const,
                        transition: "all 0.15s",
                      }}>{DIFFICULTY_LABEL[d]}</button>
                    );
                  })}
                </div>
                {/* Action buttons — fixed width so both are equal */}
                <div style={{ display: "flex", flexDirection: "column", gap: 10, width: 220 }}>
                  <button style={{ ...btnStyle("#c62828"), width: "100%" }} onClick={() => {
                    SFX.menuStart();
                    setTutorialMode(false);
                    introStopRef.current = startIntroMusic();
                    setIntroChars(0);
                    setIntroState("typing");
                  }}>⚔ COMENZAR PARTIDA</button>
                  <button style={{ ...btnStyle("#00e5ff"), width: "100%" }} onClick={() => {
                    SFX.menuGuide();
                    setTutorialMode(true);
                    setTutorialSlide(0);
                    introStopRef.current = startIntroMusic();
                    setIntroChars(0);
                    setIntroState("typing");
                  }}>📖 JUGAR CON GUÍA</button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Tutorial slides */}
        {!gs && introState === "tutorial_slides" && (() => {
          const slide = TUTORIAL_SLIDES[tutorialSlide];
          const isLast = tutorialSlide === TUTORIAL_SLIDES.length - 1;
          return (
            <div style={{
              position: "absolute", inset: 0, zIndex: 2000,
              background: "rgba(0,0,0,0.88)",
              display: "flex", flexDirection: "column",
              alignItems: "center", justifyContent: "center",
              padding: "40px 10%", boxSizing: "border-box",
            }}>
              {/* Slide card */}
              <div style={{
                background: "#06090f",
                border: `1px solid ${slide.color}`,
                borderTop: `3px solid ${slide.color}`,
                borderRadius: 4,
                padding: isMobile ? "20px 16px" : "32px 36px",
                maxWidth: 560, width: "100%",
                boxShadow: `0 0 40px ${slide.color}33`,
                fontFamily: "monospace",
              }}>
                {/* Header */}
                <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 22 }}>
                  <span style={{ fontSize: 40, lineHeight: 1 }}>{slide.icon}</span>
                  <div>
                    <div style={{ fontSize: 11, color: slide.color, letterSpacing: 3, fontWeight: 700, textShadow: `0 0 8px ${slide.color}` }}>
                      PASO {tutorialSlide + 1} / {TUTORIAL_SLIDES.length}
                    </div>
                    <div style={{ fontSize: 22, fontWeight: 800, color: "#fff", letterSpacing: 2, marginTop: 2, textShadow: "0 0 10px rgba(255,255,255,0.3)" }}>
                      {slide.title}
                    </div>
                  </div>
                </div>
                {/* Body */}
                <div style={{ color: "#b8cfe0", fontSize: 14, lineHeight: 1.8, whiteSpace: "pre-line", marginBottom: 20 }}>
                  {slide.body}
                </div>
                {/* Tip */}
                {slide.tip && (
                  <div style={{
                    background: `${slide.color}0d`, border: `1px solid ${slide.color}44`,
                    borderRadius: 2, padding: "8px 12px",
                    color: slide.color, fontSize: 12, letterSpacing: 1,
                    textShadow: `0 0 6px ${slide.color}66`,
                    marginBottom: 24,
                  }}>
                    💡 {slide.tip}
                  </div>
                )}
                {/* Navigation */}
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
                  <button
                    onClick={() => setTutorialSlide(s => s - 1)}
                    disabled={tutorialSlide === 0}
                    style={{
                      ...btnStyle("#4a6a7a", tutorialSlide === 0),
                      minWidth: 100, fontSize: 12, padding: "8px 16px",
                    }}
                  >← ANTERIOR</button>
                  {/* Dots */}
                  <div style={{ display: "flex", gap: 6 }}>
                    {TUTORIAL_SLIDES.map((_, i) => (
                      <div key={i} onClick={() => setTutorialSlide(i)} style={{
                        width: 8, height: 8, borderRadius: "50%", cursor: "pointer",
                        background: i === tutorialSlide ? slide.color : "#1a3040",
                        boxShadow: i === tutorialSlide ? `0 0 6px ${slide.color}` : "none",
                        transition: "background 0.2s",
                      }} />
                    ))}
                  </div>
                  {isLast ? (
                    <button
                      onClick={() => {
                        introStopRef.current?.();
                        introStopRef.current = null;
                        setIntroState("start");
                        startGame();
                      }}
                      style={{ ...btnStyle("#c62828"), minWidth: 100, fontSize: 12, padding: "8px 16px" }}
                    >⚔ JUGAR</button>
                  ) : (
                    <button
                      onClick={() => setTutorialSlide(s => s + 1)}
                      style={{ ...btnStyle(slide.color), minWidth: 100, fontSize: 12, padding: "8px 16px" }}
                    >SIGUIENTE →</button>
                  )}
                </div>
              </div>
            </div>
          );
        })()}

        {/* Intro typewriter screen */}
        {!gs && introState !== "start" && introState !== "tutorial_slides" && (
          <div style={{
            position: "absolute", inset: 0, zIndex: 2000,
            background: "#000",
            display: "flex", flexDirection: "column",
            alignItems: "center", justifyContent: "center",
            padding: "40px 10%",
            boxSizing: "border-box",
          }}>
            <div ref={introTextRef} style={{
              fontFamily: "monospace", color: "#00e676", fontSize: isMobile ? 13 : 19, lineHeight: 1.9,
              whiteSpace: "pre-wrap", maxWidth: 680, width: "100%",
              textShadow: "0 0 8px #00e67666",
              overflowY: "hidden", maxHeight: "75vh",
              paddingBottom: introState === "done" ? "100px" : "0",
              boxSizing: "border-box",
            }}>
              {INTRO_TEXT.slice(0, introChars)}
              {introState === "typing" && (
                <span style={{ opacity: introCursor ? 1 : 0, color: "#00e676" }}>█</span>
              )}
            </div>
            {introState === "typing" && (
              <button
                style={{
                  position: "absolute", top: 16, right: 16,
                  background: "transparent", border: "1px solid #00e67666",
                  color: "#00e676aa", fontFamily: "monospace", fontSize: 12,
                  padding: "4px 10px", borderRadius: 4, cursor: "pointer",
                }}
                onClick={() => {
                  setIntroChars(INTRO_TEXT.length);
                  setIntroState("done");
                }}
              >SALTAR »</button>
            )}
            {introState === "done" && (
              <button
                style={{ ...btnStyle("#c62828"), position: "absolute", bottom: 40, left: "50%", transform: "translateX(-50%)" }}
                onClick={() => {
                  if (tutorialMode) {
                    setTutorialSlide(0);
                    setIntroState("tutorial_slides");
                  } else {
                    introStopRef.current?.();
                    introStopRef.current = null;
                    setIntroState("start");
                    startGame();
                  }
                }}
              >{tutorialMode ? "📖 VER GUÍA →" : "⚔ INICIAR LA GUERRA"}</button>
            )}
          </div>
        )}
      </div>

      {/* Bottom bar — cyberpunk */}
      <div style={{
        height: isMobile ? 220 : 160, flexShrink: 0, display: gs ? "flex" : "none", background: "#06090f",
        position: "relative",
      }}>
        {/* Gradient top accent */}
        <div style={{
          position: "absolute", top: 0, left: 0, right: 0, height: 2, zIndex: 2,
          background: "linear-gradient(90deg, #00e5ff 0%, #aa00ff 35%, #ff0099 65%, #ffcc00 100%)",
        }} />

        {/* Left: scrollable log */}
        <div style={{
          width: "33%", height: "100%",
          borderRight: "1px solid #0a1828",
          display: isMobile ? "none" : "flex", flexDirection: "column", padding: "10px 0 6px 12px",
          boxSizing: "border-box", background: "#060e18", position: "relative",
        }}>
          {/* Panel label */}
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6, flexShrink: 0, paddingRight: 10 }}>
            <span style={{ color: "#00e5ff", fontFamily: "monospace", fontSize: 9, letterSpacing: 3, fontWeight: 700 }}>[ LOG ]</span>
            <div style={{ flex: 1, height: 1, background: "linear-gradient(90deg, #00e5ff55, transparent)" }} />
          </div>
          {/* Current event — large + highlighted */}
          {gs && (
            <div style={{
              flexShrink: 0, marginBottom: 6, paddingRight: 10,
              textAlign: "center",
            }}>
              <div style={{
                fontFamily: "monospace", fontSize: 15, fontWeight: 700,
                color: "#00e5ff", letterSpacing: 1,
                textShadow: "0 0 10px #00e5ff88",
                border: "1px solid #00e5ff33",
                borderRadius: 2,
                padding: "4px 8px",
                background: "#00e5ff0a",
                whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
              }}>{statusLine1 || "MESH WARS"}</div>
            </div>
          )}
          {gs ? (
            <div ref={logScrollRef} style={{ height: 0, flex: 1, overflowY: "scroll", overflowX: "hidden", paddingRight: 6, paddingBottom: 14 }}>
              {logLines.map((line, i) => {
                const isSep = line.startsWith("──");
                return (
                  <div key={i} style={{
                    fontFamily: "monospace",
                    fontSize: isSep ? 10 : 13,
                    lineHeight: "18px",
                    textAlign: "center",
                    color: isSep ? "#0d1e2e"
                      : line.includes("CONQUISTA") ? "#ffcc00"
                      : line.includes("BOMBA") || line.includes("PERDISTE") ? "#ff0099"
                      : line.includes("victoria") ? "#00ff88"
                      : line.includes("jugando") ? "#00e5ff"
                      : "#4a7a9b",
                    borderTop: isSep ? "1px solid #0a1828" : "none",
                    paddingTop: isSep ? 2 : 0, marginTop: isSep ? 2 : 0,
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                  }}>{line}</div>
                );
              })}
              <div ref={logEndRef} />
            </div>
          ) : (
            <div style={{ color: "#0d2030", fontFamily: "monospace", fontSize: 13, margin: "auto" }}>MESH WARS</div>
          )}
          {logHasMore && (
            <div style={{
              position: "absolute", bottom: 0, left: 0, right: 0,
              height: 28, pointerEvents: "none",
              background: "linear-gradient(transparent, #060e18)",
              display: "flex", alignItems: "flex-end", justifyContent: "center", paddingBottom: 3,
            }}>
              <span style={{ color: "#00e5ff", fontSize: 12, animation: "mw-blink 0.8s step-end infinite", textShadow: "0 0 8px #00e5ff" }}>▼</span>
            </div>
          )}
        </div>

        {/* Center: buttons */}
        <div style={{
          flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
          gap: 8, borderRight: "1px solid #0a1828", padding: isMobile ? "6px 8px" : "0 12px", flexDirection: "column",
        }}>
          {isMobile && gs && (
            <div style={{
              fontFamily: "monospace", fontSize: 11, fontWeight: 700,
              color: "#00e5ff", letterSpacing: 1, textAlign: "center",
              whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
              width: "100%", flexShrink: 0,
              textShadow: "0 0 8px #00e5ff66",
            }}>{statusLine1 || "MESH WARS"}</div>
          )}
          {!gs ? null : gs.phase === "game_over" ? (
            <button style={btnStyle("#00e5ff")} onClick={startGame}>NUEVA PARTIDA</button>
          ) : inAiTurn ? (
            <button
              onClick={() => handleButton("skip_ai")}
              style={{ ...btnStyle("#aa00ff"), fontSize: 15, padding: "10px 28px", letterSpacing: 3 }}
            >⏭ SALTAR</button>
          ) : inPostConquest || inMoveConfirm ? (
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <button onClick={() => handleButton("troop_minus_big")} style={{ ...btnStyle("#00e5ff"), fontSize: 14, padding: "6px 12px", minWidth: 36, letterSpacing: 0 }}>−−</button>
              <button onClick={() => handleButton("troop_minus")}     style={{ ...btnStyle("#00e5ff"), fontSize: 22, padding: "6px 14px", minWidth: 40 }}>−</button>
              <span style={{ color: "#ffcc00", fontFamily: "monospace", fontWeight: 700, fontSize: 30, minWidth: 44, textAlign: "center", textShadow: "0 0 12px #ffcc0088" }}>
                {gs!.postConquestTroops}
              </span>
              <button onClick={() => handleButton("troop_plus")}     style={{ ...btnStyle("#00e5ff"), fontSize: 22, padding: "6px 14px", minWidth: 40 }}>+</button>
              <button onClick={() => handleButton("troop_plus_big")} style={{ ...btnStyle("#00e5ff"), fontSize: 14, padding: "6px 12px", minWidth: 36, letterSpacing: 0 }}>++</button>
              <button onClick={() => handleButton("troop_confirm")} style={{ ...btnStyle("#ff0099"), fontSize: 14, padding: "10px 22px", marginLeft: 6 }}>MOVER</button>
            </div>
          ) : inConfirm ? (
            <div style={{ display: "flex", gap: 10 }}>
              <button
                onClick={() => handleButton("attack_confirm")}
                style={{
                  background: "#ff003318", color: "#ff3355", border: "1px solid #ff3355",
                  borderRadius: 2, padding: "12px 32px", fontFamily: "monospace",
                  fontWeight: 700, fontSize: 18, cursor: "pointer", letterSpacing: 2,
                  boxShadow: "0 0 18px #ff335588", textTransform: "uppercase" as const,
                }}
              >⚔ ATACAR</button>
              <button
                onClick={() => handleButton("cancel")}
                style={{
                  background: "transparent", color: "#2a4a5a", border: "1px solid #0d2030",
                  borderRadius: 2, padding: "12px 32px", fontFamily: "monospace",
                  fontWeight: 700, fontSize: 18, cursor: "pointer", letterSpacing: 2,
                  textTransform: "uppercase" as const,
                }}
              >↩ RETIRAR</button>
            </div>
          ) : (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "center" }}>
              <button style={btnStyle("#ff3355", !inAction)} onClick={() => inAction && handleButton("attack")}>ATACAR</button>
              <button style={btnStyle("#00e5ff", !inAction)} onClick={() => inAction && handleButton("move")}>MOVER</button>
              <button style={btnStyle("#00ff88")} onClick={() => handleButton("pass")}>PASAR</button>
              <button
                style={btnStyle(cardFlash ? "#aa00ff" : "#ffcc00", !gs?.cards.length)}
                onClick={() => { if (gs?.cards.length) setCardFlash(f => !f); }}
              >CARTAS {gs?.cards.length ? `(${gs.cards.length})` : ""}</button>
              <button style={btnStyle("#ff6600", !canCancel)} onClick={() => canCancel && handleButton("cancel")}>CANCELAR</button>
            </div>
          )}
        </div>

        {/* Right: card flash or combat info */}
        <div ref={rightScrollRef} style={{
          flex: 1, padding: isMobile ? "6px 8px 10px" : "10px 16px 22px", display: "flex", flexDirection: "column",
          justifyContent: "flex-start", overflowY: "auto", background: "#080612",
          position: "relative",
        }}>
          {gs?.pendingCard ? (
            /* ── Discard chooser (hand was full) ── */
            <>
              <div style={{ color: "#ffcc00", fontFamily: "monospace", fontSize: 11, fontWeight: 700, marginBottom: 6, letterSpacing: 1 }}>
                MANO LLENA — hacé clic en una carta para descartarla
              </div>
              <div style={{ display: "flex", gap: 4, alignItems: "flex-end", flexWrap: "wrap" }}>
                {/* Pending (new) card — display only */}
                {(() => {
                  const m = CARD_META[gs.pendingCard!];
                  return (
                    <div style={{
                      width: 62, height: 88, background: m.bg,
                      border: "2px solid #ffd700", borderRadius: 6,
                      display: "flex", flexDirection: "column",
                      alignItems: "center", justifyContent: "center",
                      gap: 3, padding: "4px 3px",
                      boxShadow: "0 0 14px #ffd70066",
                      fontFamily: "monospace", position: "relative",
                    }}>
                      <div style={{
                        position: "absolute", top: -9, left: "50%", transform: "translateX(-50%)",
                        background: "#ffd700", color: "#000", fontSize: 8, fontWeight: 700,
                        padding: "2px 5px", borderRadius: 3, whiteSpace: "nowrap",
                      }}>NUEVA</div>
                      <span style={{ fontSize: 34, color: m.color, lineHeight: 1 }}>{m.symbol}</span>
                      <span style={{ fontSize: 13, color: m.color, fontWeight: 700, lineHeight: 1 }}>{m.label}</span>
                      <span style={{ fontSize: 10, color: m.color, opacity: 0.75, lineHeight: 1 }}>{m.sub}</span>
                    </div>
                  );
                })()}
                <div style={{ width: 1, height: 70, background: "#30363d", alignSelf: "center", margin: "0 2px" }} />
                {/* Existing cards — click to discard */}
                {gs.cards.map((card, i) => {
                  const m = CARD_META[card];
                  return (
                    <div key={i} onClick={() => handleDiscard(i)} style={{
                      width: 62, height: 88, background: m.bg,
                      border: "2px solid #c62828", borderRadius: 6,
                      display: "flex", flexDirection: "column",
                      alignItems: "center", justifyContent: "center",
                      gap: 3, padding: "4px 3px",
                      fontFamily: "monospace", position: "relative",
                      cursor: "pointer",
                    }}>
                      <div style={{
                        position: "absolute", top: 2, right: 4,
                        color: "#f44336", fontSize: 10, fontWeight: 900, lineHeight: 1,
                      }}>✕</div>
                      <span style={{ fontSize: 34, color: m.color, lineHeight: 1 }}>{m.symbol}</span>
                      <span style={{ fontSize: 13, color: m.color, fontWeight: 700, lineHeight: 1 }}>{m.label}</span>
                      <span style={{ fontSize: 10, color: m.color, opacity: 0.75, lineHeight: 1 }}>{m.sub}</span>
                    </div>
                  );
                })}
              </div>
            </>
          ) : cardFlash && gs && gs.cards.length > 0 ? (
            <>
              {/* Header */}
              {loseAllFlash ? (
                <div style={{
                  color: blinkVisible ? "#ff0099" : "#3a0020",
                  fontFamily: "monospace", fontSize: 12, fontWeight: 700,
                  marginBottom: 6, letterSpacing: 1,
                  textShadow: blinkVisible ? "0 0 10px #ff009988" : "none",
                  transition: "color 0.1s",
                }}>☠ ¡PERDÉS TODAS TUS CARTAS!</div>
              ) : (
                <div style={{ color: "#aa00ff", fontFamily: "monospace", fontSize: 11, fontWeight: 700, marginBottom: 6, letterSpacing: 1 }}>
                  CARTAS ({gs.cards.length}/5)
                  {selectedCards.length < 3 && (
                    <span style={{ color: "#3a2060", fontWeight: 400, marginLeft: 8 }}>
                      seleccioná {3 - selectedCards.length} más para canjear
                    </span>
                  )}
                </div>
              )}
              {/* Card hand */}
              <div style={{ display: "flex", gap: 5, alignItems: "flex-end", flexWrap: "wrap" }}>
                {gs.cards.map((card, i) => {
                  const m        = CARD_META[card];
                  const isNew    = i === gs.cards.length - 1;
                  const isSel    = selectedCards.includes(i);
                  const maxed    = selectedCards.length >= 3 && !isSel;
                  const isLoseAllCard = loseAllFlash && isNew;
                  const cardOpacity  = loseAllFlash ? (isLoseAllCard ? (blinkVisible ? 1 : 0.1) : 0.18) : (maxed ? 0.45 : 1);
                  const cardBorder   = loseAllFlash
                    ? (isLoseAllCard ? `2px solid ${blinkVisible ? "#f44336" : "#3a0a0a"}` : "2px solid #1a1a1a")
                    : `2px solid ${isSel ? "#ffffff" : isNew ? "#ffd700" : m.color}`;
                  const cardShadow   = loseAllFlash
                    ? (isLoseAllCard && blinkVisible ? "0 0 18px #f4433688" : "none")
                    : (isSel ? "0 0 12px #ffffff55" : isNew ? "0 0 14px #ffd70066" : "0 3px 8px rgba(0,0,0,0.6)");
                  return (
                    <div key={i}
                      onClick={() => {
                        if (loseAllFlash || maxed) return;
                        setSelectedCards(prev =>
                          prev.includes(i) ? prev.filter(x => x !== i) : [...prev, i]
                        );
                      }}
                      style={{
                        width: 76, height: 108,
                        background: m.bg,
                        border: cardBorder,
                        borderRadius: 7,
                        display: "flex", flexDirection: "column",
                        alignItems: "center", justifyContent: "center",
                        gap: 4, padding: "6px 4px",
                        boxShadow: cardShadow,
                        fontFamily: "monospace", position: "relative",
                        cursor: loseAllFlash || maxed ? "default" : "pointer",
                        opacity: cardOpacity,
                        transition: "opacity 0.08s",
                      }}>
                      {/* NEW badge */}
                      {isNew && !isSel && !loseAllFlash && (
                        <div style={{
                          position: "absolute", top: -10, left: "50%", transform: "translateX(-50%)",
                          background: "#ffd700", color: "#000", fontSize: 9, fontWeight: 700,
                          padding: "2px 6px", borderRadius: 3, whiteSpace: "nowrap",
                        }}>NUEVA</div>
                      )}
                      {/* CHECK badge */}
                      {isSel && !loseAllFlash && (
                        <div style={{
                          position: "absolute", top: -10, left: "50%", transform: "translateX(-50%)",
                          background: "#fff", color: "#000", fontSize: 11, fontWeight: 900,
                          padding: "2px 6px", borderRadius: 3, lineHeight: 1,
                        }}>✓</div>
                      )}
                      <span style={{ fontSize: 42, color: loseAllFlash && !isLoseAllCard ? "#333" : m.color, lineHeight: 1 }}>{m.symbol}</span>
                      <span style={{ fontSize: 17, color: loseAllFlash && !isLoseAllCard ? "#333" : m.color, fontWeight: 700, lineHeight: 1 }}>{m.label}</span>
                      <span style={{ fontSize: 13, color: loseAllFlash && !isLoseAllCard ? "#333" : m.color, opacity: 0.75, lineHeight: 1 }}>{m.sub}</span>
                    </div>
                  );
                })}
              </div>
              {/* Trade result row — hidden during lose_all flash */}
              {!loseAllFlash && selectedCards.length === 3 && (
                <div style={{ marginTop: 8, borderTop: "1px solid #0a1828", paddingTop: 7, display: "flex", alignItems: "center", gap: 10 }}>
                  {selectionEffect ? (() => {
                    const bonus   = troopBonus(selectionEffect);
                    const em      = CARD_META[selectionEffect];
                    const canTrade = gs?.phase === "reinforcement";
                    const enabled = canTrade && (bonus > 0 || selectionEffect === "bomba");
                    const label   = !canTrade
                      ? "Solo canjeás al inicio del turno"
                      : bonus > 0
                        ? `+${bonus} tropas al refuerzo`
                        : selectionEffect === "bomba"
                          ? `${em.symbol} ATAQUE BOMBA — elegí objetivo`
                          : `${em.label} — próximamente`;
                    return (
                      <>
                        <span style={{ color: em.color, fontFamily: "monospace", fontSize: 12 }}>{label}</span>
                        <button
                          disabled={!enabled}
                          onClick={() => handleTrade(selectedCards, selectionEffect)}
                          style={{
                            background: enabled ? `${em.color}22` : "transparent",
                            color: enabled ? em.color : "#1a2030",
                            border: `1px solid ${enabled ? em.color : "#0d1828"}`,
                            borderRadius: 2, padding: "4px 12px", fontFamily: "monospace",
                            fontWeight: 700, fontSize: 12, letterSpacing: 1,
                            cursor: enabled ? "pointer" : "not-allowed",
                            boxShadow: enabled ? `0 0 8px ${em.color}44` : "none",
                          }}
                        >CANJEAR</button>
                      </>
                    );
                  })() : (
                    <span style={{ color: "#ff3355", fontFamily: "monospace", fontSize: 12 }}>Combinación inválida</span>
                  )}
                  <button
                    onClick={() => setSelectedCards([])}
                    style={{
                      background: "none", color: "#1a3040", border: "1px solid #0d1828",
                      borderRadius: 2, padding: "3px 9px", fontFamily: "monospace",
                      fontSize: 10, cursor: "pointer", marginLeft: "auto", letterSpacing: 1,
                    }}
                  >limpiar</button>
                </div>
              )}
            </>
          ) : gs && tutorialMode && tutorialContent(gs) ? (() => {
            const tc = tutorialContent(gs)!;
            return (
              <div style={{ display: "flex", flexDirection: "column" }}>
                {/* Icon + title */}
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
                  <span style={{ fontSize: 28, lineHeight: 1 }}>{tc.icon}</span>
                  <div style={{
                    fontSize: 14, fontWeight: 800, color: tc.color, letterSpacing: 2,
                    textShadow: `0 0 10px ${tc.color}88`,
                  }}>{tc.title}</div>
                </div>
                {/* Description */}
                <div style={{
                  color: "#c0d8e8", fontSize: 12, lineHeight: 1.75,
                  whiteSpace: "pre-line", flex: 1,
                }}>
                  {tc.desc}
                </div>
                {/* Tip */}
                {tc.tip && (
                  <div style={{
                    marginTop: 8, marginBottom: 4,
                    background: `${tc.color}0d`, border: `1px solid ${tc.color}33`,
                    borderRadius: 2, padding: "6px 10px",
                    color: tc.color, fontSize: 11, letterSpacing: 1,
                    textShadow: `0 0 6px ${tc.color}55`,
                  }}>
                    💡 {tc.tip}
                  </div>
                )}
                {/* combatMsg below guide if present */}
                {gs.combatMsg ? (
                  <div style={{ color: "#ffcc00", fontFamily: "monospace", fontSize: 13, fontWeight: 700, marginTop: 6, textShadow: "0 0 8px #ffcc0055" }}>
                    {gs.combatMsg}
                  </div>
                ) : null}
              </div>
            );
          })() : gs?.combatMsg ? (
            <div style={{ color: "#ffcc00", fontFamily: "monospace", fontSize: 15, fontWeight: 700, marginBottom: 4, textShadow: "0 0 8px #ffcc0055" }}>
              {gs.combatMsg}
            </div>
          ) : gs?.log[0] ? (
            <div style={{ color: "#1a3040", fontFamily: "monospace", fontSize: 12 }}>
              {gs.log[0]}
            </div>
          ) : null}
          {!cardFlash && gs?.phase === "attack_confirm" && gs.attackSource && gs.attackTarget && (
            <div style={{ color: "#ff3355", fontFamily: "monospace", fontSize: 14, marginTop: 4, letterSpacing: 2, textShadow: "0 0 8px #ff335566" }}>
              {gs.cells[gs.attackSource].troops} CONTRA {gs.cells[gs.attackTarget].troops}
            </div>
          )}
          {rightHasMore && (
            <div style={{
              position: "sticky", bottom: 0,
              height: 28, pointerEvents: "none", marginTop: "auto",
              background: "linear-gradient(transparent, #080612)",
              display: "flex", alignItems: "flex-end", justifyContent: "center", paddingBottom: 3,
            }}>
              <span style={{ color: "#ff6600", fontSize: 12, animation: "mw-blink 0.8s step-end infinite", textShadow: "0 0 8px #ff6600" }}>▼</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
