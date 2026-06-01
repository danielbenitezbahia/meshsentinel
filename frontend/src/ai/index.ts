import type { GameCell, Bridge, Faction } from "./types";
import { pickReinforcementPool as redReinf,   pickAttack as redAttack   } from "./red";
import { pickReinforcementPool as greenReinf, pickAttack as greenAttack } from "./green";
import { pickReinforcementPool as brownReinf, pickAttack as brownAttack } from "./brown";

export function pickReinforcementPool(
  faction: Faction,
  owned:   GameCell[],
  cells:   Record<string, GameCell>,
  bridges: Bridge[],
  islands: string[][],
): string[] {
  if (faction === "ai1") return redReinf(faction, owned, cells, bridges, islands);
  if (faction === "ai2") return greenReinf(faction, owned, cells, bridges, islands);
  if (faction === "ai3") return brownReinf(faction, owned, cells, bridges, islands);
  return owned.map(c => c.h3Index);
}

export function pickAttack(
  faction:   Faction,
  owned:     GameCell[],
  cells:     Record<string, GameCell>,
  bridges:   Bridge[],
  focusCell: string | null,
  islands:   string[][],
): { from: string; to: string } | null {
  if (faction === "ai1") return redAttack(faction, owned, cells, bridges, focusCell, islands);
  if (faction === "ai2") return greenAttack(faction, owned, cells, bridges, focusCell, islands);
  if (faction === "ai3") return brownAttack(faction, owned, cells, bridges, focusCell, islands);
  return null;
}
