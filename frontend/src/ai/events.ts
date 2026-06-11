// Motor de eventos aleatorios — efectos al inicio del turno del jugador.
// Probabilidad: 60% nada · 25% menor · 12% medio · 3% fuerte

import { gridDisk } from "h3-js";
import type { GameCell, Faction } from "./types";

export interface GameEvent {
  id:            string;
  type:          "POSITIVE" | "NEGATIVE" | "NEUTRAL";
  severity:      "MINOR" | "MEDIUM" | "MAJOR";
  targetFaction: Faction | null;
  targetCell:    string;
  affectedCells: string[];
  deltas:        Record<string, number>;
  title:         string;
  description:   string;
}

const rnd = <T,>(arr: T[]): T => arr[Math.floor(Math.random() * arr.length)];

const PROD_CAP = 15;

function pickContiguousCluster(
  cells: Record<string, GameCell>,
  n: number,
  filter?: (c: GameCell) => boolean,
): string[] | null {
  const pool = Object.values(cells).filter(c => filter ? filter(c) : true);
  if (pool.length < n) return null;

  const shuffled = [...pool].sort(() => Math.random() - 0.5);
  for (const start of shuffled) {
    const cluster: string[] = [start.h3Index];
    const visited = new Set(cluster);
    const frontier = new Set<string>();
    gridDisk(start.h3Index, 1).forEach(nb => {
      if (nb !== start.h3Index && cells[nb] && (!filter || filter(cells[nb])) && !visited.has(nb))
        frontier.add(nb);
    });
    while (cluster.length < n && frontier.size > 0) {
      const arr = [...frontier];
      const pick = arr[Math.floor(Math.random() * arr.length)];
      cluster.push(pick);
      visited.add(pick);
      frontier.delete(pick);
      gridDisk(pick, 1).forEach(nb => {
        if (!visited.has(nb) && cells[nb] && (!filter || filter(cells[nb])))
          frontier.add(nb);
      });
    }
    if (cluster.length >= n) return cluster.slice(0, n);
  }
  return null;
}

export function generateEvent(
  cells: Record<string, GameCell>,
  _turn: number,
): GameEvent | null {
  const roll = Math.random();
  if (roll < 0.60) return null;

  const severity: GameEvent["severity"] =
    roll < 0.85 ? "MINOR" : roll < 0.97 ? "MEDIUM" : "MAJOR";

  type CandFn = () => GameEvent | null;
  const candidates: CandFn[] = [];

  // ── 1. Pulso Electromagnético ─────────────────────────────────────────────
  // 4-5 celdas H3-adyacentes de cualquier facción, -1 a -2 tropas cada una
  {
    const empSize  = severity === "MINOR" ? 4 : 5;
    const empLoss  = severity === "MAJOR" ? 2 : 1;
    if (Object.values(cells).filter(c => c.troops >= 2).length >= empSize) {
      candidates.push(() => {
        const cluster = pickContiguousCluster(cells, empSize, c => c.troops >= 2);
        if (!cluster) return null;
        const deltas: Record<string, number> = {};
        cluster.forEach(id => { deltas[id] = -empLoss; });
        const names = cluster.map(id => cells[id].nodeName).join(", ");
        return {
          id: "emp", type: "NEGATIVE", severity,
          targetFaction: null, targetCell: cluster[0],
          affectedCells: cluster, deltas,
          title: "Pulso Electromagnético",
          description: `Una descarga masiva barrió la zona. ${names} pierden ${empLoss} tropa${empLoss > 1 ? "s" : ""} cada uno.`,
        };
      });
    }
  }

  // ── 2. Rebelión Interna ───────────────────────────────────────────────────
  // 2-3 celdas H3-adyacentes del mismo color, -1 tropa cada una
  {
    const rebSize = severity === "MINOR" ? 2 : 3;
    const factions: Faction[] = ["player", "ai1", "ai2", "ai3"];
    const eligible = factions.filter(f =>
      Object.values(cells).filter(c => c.owner === f && c.troops >= 2).length >= rebSize
    );
    if (eligible.length > 0) {
      candidates.push(() => {
        const faction = rnd(eligible);
        const cluster = pickContiguousCluster(cells, rebSize, c => c.owner === faction && c.troops >= 2);
        if (!cluster) return null;
        const deltas: Record<string, number> = {};
        cluster.forEach(id => { deltas[id] = -1; });
        const names = cluster.map(id => cells[id].nodeName).join(", ");
        return {
          id: "rebellion", type: "NEGATIVE", severity,
          targetFaction: faction, targetCell: cluster[0],
          affectedCells: cluster, deltas,
          title: "Rebelión Interna",
          description: `Tensiones internas sacuden ${names}. Cada posición pierde 1 tropa.`,
        };
      });
    }
  }

  // ── 3. Descubrimiento de Lucaína-T ────────────────────────────────────────
  // 1 celda aleatoria, +50% tropas actuales (ceil, cap PROD_CAP)
  {
    const pool = Object.values(cells).filter(c => c.troops < PROD_CAP);
    if (pool.length > 0) {
      candidates.push(() => {
        const cell      = rnd(pool);
        const gain      = Math.max(1, Math.ceil(cell.troops * 0.5));
        const newTroops = Math.min(PROD_CAP, cell.troops + gain);
        const actual    = newTroops - cell.troops;
        if (actual <= 0) return null;
        return {
          id: "lucaina_t", type: "POSITIVE", severity,
          targetFaction: cell.owner, targetCell: cell.h3Index,
          affectedCells: [cell.h3Index], deltas: { [cell.h3Index]: actual },
          title: "Descubrimiento de Lucaína-T",
          description: `Depósito hallado en ${cell.nodeName}. Tropas: ${cell.troops} → ${newTroops} (+${actual}).`,
        };
      });
    }
  }

  // ── 4. Emisiones Solares Anómalas ─────────────────────────────────────────
  // 3-4 celdas H3-adyacentes con nodo activo, +1 tropa cada una
  {
    const solarSize = severity === "MINOR" ? 3 : 4;
    if (Object.values(cells).filter(c => c.isNodeActive).length >= solarSize) {
      candidates.push(() => {
        const cluster = pickContiguousCluster(cells, solarSize, c => c.isNodeActive);
        if (!cluster) return null;
        const deltas: Record<string, number> = {};
        cluster.forEach(id => { deltas[id] = +1; });
        const names = cluster.map(id => cells[id].nodeName).join(", ");
        return {
          id: "solar_emission", type: "POSITIVE", severity,
          targetFaction: null, targetCell: cluster[0],
          affectedCells: cluster, deltas,
          title: "Emisiones Solares Anómalas",
          description: `Radiación solar recarga los nodos activos. ${names} ganan +1 tropa cada uno.`,
        };
      });
    }
  }

  const shuffled = [...candidates].sort(() => Math.random() - 0.5);
  for (const fn of shuffled) {
    const ev = fn();
    if (ev) return ev;
  }
  return null;
}
