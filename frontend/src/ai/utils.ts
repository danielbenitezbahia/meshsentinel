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
