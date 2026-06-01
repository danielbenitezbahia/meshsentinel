// Los Viejos del Éter — conservadores, defensivos, territoriales
// Tolerancia al riesgo: baja (solo ataca con adv >= 2)

import type { GameCell, Bridge, Faction } from "./types";
import { cellNeighbors, geoDistSq, islandCompletionBonus, islandReinfBonus } from "./utils";

export function pickReinforcementPool(
  faction: Faction,
  owned: GameCell[],
  cells: Record<string, GameCell>,
  bridges: Bridge[],
  islands: string[][],
): string[] {
  const pool: string[] = [];
  for (const cell of owned) {
    const nbs        = cellNeighbors(cell.h3Index, cells, bridges);
    const enemyCount = nbs.filter(id => cells[id]?.owner !== cell.owner).length;
    if (enemyCount === 0) continue;
    const infraBonus = (cell.isProduction || cell.isNodeActive) ? 3 : 0;
    const islBonus   = islandReinfBonus(cell.h3Index, faction, cells, islands);
    const weight     = enemyCount * 2 + infraBonus + islBonus;
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
  const ownSet = new Set(owned.map(c => c.h3Index));
  const opts: Array<{ from: string; to: string; score: number }> = [];

  for (const cell of owned) {
    if (cell.troops < 3) continue;
    for (const nbId of cellNeighbors(cell.h3Index, cells, bridges)) {
      const nb = cells[nbId];
      if (!nb || nb.owner === cell.owner) continue;
      const adv    = cell.troops - nb.troops;
      const islBonus = islandCompletionBonus(nbId, faction, cells, islands);

      // BROWN baja su exigencia de adv si completar la isla está muy cerca
      const minAdv = islBonus >= 10 ? 0 : islBonus >= 6 ? 1 : 2;
      if (adv < minAdv) continue;

      const targetNbs            = cellNeighbors(nbId, cells, bridges);
      const ownNeighborsOfTarget = targetNbs.filter(id => ownSet.has(id)).length;

      let score = adv + ownNeighborsOfTarget * 1.5 + islBonus;
      if (nb.isProduction)            score += 3;
      if (nb.isNodeActive)            score += 2;
      if (ownNeighborsOfTarget === 0) score -= 4;
      if (focusCell) score -= geoDistSq(cell.h3Index, focusCell) * 800;
      opts.push({ from: cell.h3Index, to: nbId, score });
    }
  }

  if (!opts.length) return null;
  opts.sort((a, b) => b.score - a.score);
  return { from: opts[0].from, to: opts[0].to };
}
