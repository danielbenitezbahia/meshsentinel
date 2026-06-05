// El Círculo DX — agresivo, expansivo, oportunista
// Tolerancia al riesgo: media-alta (ataca con adv >= -1)

import type { GameCell, Bridge, Faction } from "./types";
import { cellNeighbors, geoDistSq, islandCompletionBonus, islandReinfBonus, getPriorityIsland, continentAttackBonus, continentReinfBonus, breakEnemyIslandBonus } from "./utils";

export function pickReinforcementPool(
  faction: Faction,
  owned: GameCell[],
  cells: Record<string, GameCell>,
  bridges: Bridge[],
  islands: string[][],
): string[] {
  const priority = getPriorityIsland(faction, cells, islands);
  const pool: string[] = [];
  for (const cell of owned) {
    const nbs        = cellNeighbors(cell.h3Index, cells, bridges);
    const enemies    = nbs.filter(id => cells[id]?.owner !== cell.owner);
    if (enemies.length === 0) continue;
    const weakEnemies = enemies.filter(id => (cells[id]?.troops ?? 99) <= 2).length;
    const infraBonus  = (cell.isProduction || cell.isNodeActive) ? 2 : 0;
    const islBonus    = islandReinfBonus(cell.h3Index, faction, cells, islands);
    const contBonus   = continentReinfBonus(cell.h3Index, faction, priority, cells, bridges);
    const weight      = enemies.length * 2 + weakEnemies + infraBonus + islBonus + contBonus + Math.floor(cell.troops / 2);
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
  const priority = getPriorityIsland(faction, cells, islands);
  const opts: Array<{ from: string; to: string; score: number }> = [];

  for (const cell of owned) {
    if (cell.troops < 2) continue;
    for (const nbId of cellNeighbors(cell.h3Index, cells, bridges)) {
      const nb = cells[nbId];
      if (!nb || nb.owner === cell.owner) continue;
      const adv = cell.troops - nb.troops;
      if (adv < -1) continue;
      let score = adv;
      if (nb.isProduction) score += 4;
      if (nb.isNodeActive) score += 3;
      score += islandCompletionBonus(nbId, faction, cells, islands);
      score += continentAttackBonus(nbId, priority);
      score += breakEnemyIslandBonus(nbId, faction, cells, islands);
      if (focusCell) score -= geoDistSq(cell.h3Index, focusCell) * 600;
      opts.push({ from: cell.h3Index, to: nbId, score });
    }
  }

  if (!opts.length) return null;
  opts.sort((a, b) => b.score - a.score);
  return { from: opts[0].from, to: opts[0].to };
}
