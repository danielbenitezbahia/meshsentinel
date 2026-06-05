import { gridDisk, cellToLatLng } from "h3-js";
import type { Faction, GameCell, Bridge } from "./types";

export function cellNeighbors(h3: string, cells: Record<string, GameCell>, bridges: Bridge[]): string[] {
  const h3nb = gridDisk(h3, 1).filter(id => id !== h3 && id in cells);
  const brnb = bridges.filter(b => b.fromH3 === h3 || b.toH3 === h3).map(b => b.fromH3 === h3 ? b.toH3 : b.fromH3);
  return [...new Set([...h3nb, ...brnb])];
}

export function geoDistSq(a: string, b: string): number {
  const [la, lo] = cellToLatLng(a);
  const [lb, ll] = cellToLatLng(b);
  return (la - lb) ** 2 + (lo - ll) ** 2;
}

// Devuelve la isla donde la facción tiene mayor % de ocupación (su "continente objetivo").
export function getPriorityIsland(
  faction: Faction,
  cells: Record<string, GameCell>,
  islands: string[][],
): { island: string[]; pct: number; complete: boolean } | null {
  let best: { island: string[]; pct: number; complete: boolean } | null = null;
  for (const island of islands) {
    if (island.length < 3) continue;
    const owned = island.filter(id => cells[id]?.owner === faction).length;
    if (owned === 0) continue;
    const pct      = owned / island.length;
    const complete = owned === island.length;
    if (!best || pct > best.pct) best = { island, pct, complete };
  }
  return best;
}

// Bonus para atacar/reforzar dentro del continente objetivo.
// Si está completa, bonus reducido (deja competir a otras estrategias).
export function continentAttackBonus(
  targetId: string,
  priority: { island: string[]; pct: number; complete: boolean } | null,
): number {
  if (!priority || !priority.island.includes(targetId)) return 0;
  return priority.complete ? 4 : 12;
}

// Bonus de refuerzo para celdas dentro del continente objetivo.
// Máximo cuando la celda mira un enemigo dentro de la propia isla (frente de avance real).
// Bonus reducido si está en la isla pero el enemigo adyacente está fuera de ella.
export function continentReinfBonus(
  cellId: string,
  faction: Faction,
  priority: { island: string[]; pct: number; complete: boolean } | null,
  cells: Record<string, GameCell>,
  bridges: Bridge[],
): number {
  if (!priority || !priority.island.includes(cellId)) return 0;
  if (priority.complete) return 2;
  const islandSet = new Set(priority.island);
  const nbs = cellNeighbors(cellId, cells, bridges);
  const facingIslandEnemy = nbs.some(nb => islandSet.has(nb) && cells[nb]?.owner !== faction);
  return facingIslandEnemy ? 8 : 3;
}

// Bonus para atacar celdas en islas enemigas completas o casi completas
// (romper continente enemigo → le quita el bonus de refuerzo).
export function breakEnemyIslandBonus(
  targetId: string,
  faction: Faction,
  cells: Record<string, GameCell>,
  islands: string[][],
): number {
  for (const island of islands) {
    if (!island.includes(targetId)) continue;
    if (island.length < 3) continue;
    const enemyCounts: Partial<Record<Faction, number>> = {};
    for (const id of island) {
      const owner = cells[id]?.owner;
      if (owner && owner !== faction) enemyCounts[owner] = (enemyCounts[owner] ?? 0) + 1;
    }
    for (const [, count] of Object.entries(enemyCounts)) {
      const pct = count / island.length;
      if (pct === 1)   return 8;  // isla 100% enemiga → romperla vale mucho
      if (pct >= 0.8)  return 5;  // casi completa → prevenir que la complete
    }
  }
  return 0;
}

// Bonus para atacar una celda que acerca a la facción a controlar una isla entera.
// Controlar una isla completa da +2 refuerzos por turno → vale la pena priorizarlo.
// El bonus decae según cuántas celdas faltan para completar la isla (máx 3 ataques/turno).
export function islandCompletionBonus(
  targetId: string,
  faction: Faction,
  cells: Record<string, GameCell>,
  islands: string[][],
): number {
  for (const island of islands) {
    if (!island.includes(targetId)) continue;
    if (island.length < 2) continue;
    const remaining = island.filter(id => id !== targetId && cells[id]?.owner !== faction).length;
    if (remaining === 0) return 10; // completaría la isla ahora
    if (remaining === 1) return 6;  // en 2 ataques
    if (remaining === 2) return 3;  // en 3 ataques (límite del turno)
  }
  return 0;
}

// Bonus para reforzar celdas en islas casi completas (proteger lo casi ganado).
export function islandReinfBonus(
  cellId: string,
  faction: Faction,
  cells: Record<string, GameCell>,
  islands: string[][],
): number {
  for (const island of islands) {
    if (!island.includes(cellId)) continue;
    if (island.length < 2) continue;
    const notOwned = island.filter(id => cells[id]?.owner !== faction).length;
    if (notOwned === 0) continue; // ya completa, no urgente
    if (notOwned <= 2) return 3;
    if (notOwned <= 3 && island.filter(id => cells[id]?.owner === faction).length >= island.length / 2) return 2;
  }
  return 0;
}

export function nnSort(ids: string[]): string[] {
  if (ids.length <= 1) return [...ids];
  const rem = [...ids];
  const out: string[] = [rem.splice(0, 1)[0]];
  while (rem.length > 0) {
    const last = out[out.length - 1];
    let bi = 0, bd = Infinity;
    for (let i = 0; i < rem.length; i++) {
      const d = geoDistSq(last, rem[i]);
      if (d < bd) { bd = d; bi = i; }
    }
    out.push(rem.splice(bi, 1)[0]);
  }
  return out;
}
