// Los Fundadores Corruptos — obsesivos con nodos de infra y producción de Lucaína-T
// Modo calculador (>= mitad de celdas prod): adv >= 0 para prod, adv >= 1 para nodo activo
// Modo desesperado (< mitad de celdas prod): adv >= -1 para prod, adv >= 0 para nodo activo

import type { GameCell, Bridge, Faction } from "./types";
import { cellNeighbors, geoDistSq, islandCompletionBonus, islandReinfBonus } from "./utils";

function lucainaMode(owned: GameCell[], cells: Record<string, GameCell>): "desperate" | "calculated" {
  const allProd   = Object.values(cells).filter(c => c.isProduction).length;
  if (allProd === 0) return "calculated";
  const ownedProd = owned.filter(c => c.isProduction).length;
  return ownedProd < allProd / 2 ? "desperate" : "calculated";
}

export function pickReinforcementPool(
  faction: Faction,
  owned: GameCell[],
  cells: Record<string, GameCell>,
  bridges: Bridge[],
  islands: string[][],
): string[] {
  const pool: string[] = [];
  for (const cell of owned) {
    const nbs = cellNeighbors(cell.h3Index, cells, bridges);
    let weight = 0;
    if (cell.isProduction) {
      weight = 5;
    } else if (cell.isNodeActive) {
      weight = 3;
    } else {
      const adjToEnemyProd = nbs.some(id => cells[id]?.isProduction && cells[id]?.owner !== cell.owner);
      const adjToEnemyNode = nbs.some(id => cells[id]?.isNodeActive && cells[id]?.owner !== cell.owner);
      const onBorder       = nbs.some(id => cells[id]?.owner !== cell.owner);
      if (adjToEnemyProd)      weight = 3;
      else if (adjToEnemyNode) weight = 2;
      else if (onBorder)       weight = 1;
    }
    weight += islandReinfBonus(cell.h3Index, faction, cells, islands);
    for (let i = 0; i < weight; i++) pool.push(cell.h3Index);
  }
  return pool.length > 0 ? pool : owned.map(c => c.h3Index);
}

export function pickAttack(
  faction: Faction,
  owned: GameCell[],
  cells: Record<string, GameCell>,
  bridges: Bridge[],
  focusCell: string | null,
  islands: string[][],
): { from: string; to: string } | null {
  const mode = lucainaMode(owned, cells);
  const opts: Array<{ from: string; to: string; score: number }> = [];

  for (const cell of owned) {
    if (cell.troops < 2) continue;
    for (const nbId of cellNeighbors(cell.h3Index, cells, bridges)) {
      const nb = cells[nbId];
      if (!nb || nb.owner === cell.owner) continue;
      const adv = cell.troops - nb.troops;

      // Para celdas sin valor estratégico, solo ataca si completa una isla
      const islBonus = islandCompletionBonus(nbId, faction, cells, islands);
      if (!nb.isProduction && !nb.isNodeActive && islBonus === 0) continue;

      if (nb.isProduction) {
        if (mode === "desperate"  && adv < -1) continue;
        if (mode === "calculated" && adv <  0) continue;
      } else if (nb.isNodeActive) {
        if (mode === "desperate"  && adv <  0) continue;
        if (mode === "calculated" && adv <  1) continue;
      } else {
        // celda sin valor propio pero completa isla — riesgo medio
        if (adv < 0) continue;
      }

      let score = adv;
      if (nb.isProduction) score += 6;
      if (nb.isNodeActive) score += 4;
      if (mode === "desperate") score += 2;
      score += islBonus;
      if (focusCell) score -= geoDistSq(cell.h3Index, focusCell) * 600;
      opts.push({ from: cell.h3Index, to: nbId, score });
    }
  }

  if (!opts.length) return null;
  opts.sort((a, b) => b.score - a.score);
  return { from: opts[0].from, to: opts[0].to };
}
