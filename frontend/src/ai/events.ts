// Motor de eventos aleatorios — efectos instantáneos (troopsDelta únicamente)
// Probabilidad: 60% nada · 25% menor · 12% medio · 3% fuerte
// Se evalúa al inicio del turno del jugador, sobre el estado final post-IA.

import type { GameCell, Faction } from "./types";

export interface GameEvent {
  id:             string;
  type:           "POSITIVE" | "NEGATIVE" | "NEUTRAL";
  severity:       "MINOR" | "MEDIUM" | "MAJOR";
  targetFaction:  Faction;
  targetCell:     string;
  troopsDelta:    number;
  title:          string;
  description:    string;
}

type CandFn = () => GameEvent | null;

const rnd = <T,>(arr: T[]): T => arr[Math.floor(Math.random() * arr.length)];

export function generateEvent(
  cells: Record<string, GameCell>,
  _turn: number,
): GameEvent | null {
  const roll = Math.random();
  if (roll < 0.60) return null;

  const severity: GameEvent["severity"] =
    roll < 0.85 ? "MINOR" : roll < 0.97 ? "MEDIUM" : "MAJOR";
  const maxDelta = severity === "MINOR" ? 1 : severity === "MEDIUM" ? 2 : 3;

  const by = (f: Faction) => Object.values(cells).filter(c => c.owner === f);
  const brownCells  = by("ai3");
  const redCells    = by("ai1");
  const greenCells  = by("ai2");
  const playerCells = by("player");

  const candidates: CandFn[] = [];

  // ── BROWN — Los Viejos del Éter ────────────────────────────────────────────
  if (brownCells.length > 0) {
    candidates.push(() => {
      const pool = brownCells.filter(c => !c.isSynthetic);
      if (!pool.length) return null;
      const cell = rnd(pool);
      const d    = Math.floor(Math.random() * maxDelta) + 1;
      return {
        id: "brown_island_secured", type: "POSITIVE", severity,
        targetFaction: "ai3", targetCell: cell.h3Index, troopsDelta: d,
        title: "Isla asegurada",
        description: `La zona fue declarada 'más o menos segura', que para los Marrones equivale a una victoria histórica. (+${d} tropas en ${cell.nodeName})`,
      };
    });

    const brownActive = brownCells.filter(c => c.isNodeActive);
    if (brownActive.length > 0) {
      candidates.push(() => {
        const cell = rnd(brownActive);
        return {
          id: "brown_maintenance", type: "POSITIVE", severity,
          targetFaction: "ai3", targetCell: cell.h3Index, troopsDelta: 1,
          title: "Mantenimiento preventivo",
          description: `Se hizo mantenimiento en ${cell.nodeName}. Nadie sabe qué tocaron, pero ahora anda mejor. (+1 tropa)`,
        };
      });
    }
  }

  // ── RED — El Círculo DX ────────────────────────────────────────────────────
  if (redCells.length > 0) {
    candidates.push(() => {
      const cell = rnd(redCells);
      const d    = Math.floor(Math.random() * maxDelta) + 1;
      return {
        id: "red_dx_contact", type: "POSITIVE", severity,
        targetFaction: "ai1", targetCell: cell.h3Index, troopsDelta: d,
        title: "Contacto lejano exitoso",
        description: `Una transmisión imposible llegó a destino. Nadie sabe cómo, pero los Rojos ya están imprimiendo certificados. (+${d} tropas en ${cell.nodeName})`,
      };
    });

    const bigRed = redCells.filter(c => c.troops >= 3);
    if (bigRed.length > 0) {
      candidates.push(() => {
        const cell = rnd(bigRed);
        const d    = -(Math.floor(Math.random() * Math.min(maxDelta, cell.troops - 1)) + 1);
        return {
          id: "red_internal_fight", type: "NEGATIVE", severity,
          targetFaction: "ai1", targetCell: cell.h3Index, troopsDelta: d,
          title: "Pelea interna DX",
          description: `Dos operadores discutieron quién había hecho primero el contacto. La discusión escaló y se perdieron tropas. (${d} en ${cell.nodeName})`,
        };
      });
    }

    const redStrong = redCells.filter(c => c.troops >= 2);
    if (redStrong.length > 0) {
      candidates.push(() => {
        const cell = rnd(redStrong);
        return {
          id: "red_overexpansion", type: "NEGATIVE", severity,
          targetFaction: "ai1", targetCell: cell.h3Index, troopsDelta: -1,
          title: "Sobreexpansión",
          description: `El Círculo DX conquistó más rápido de lo que podía explicar en el grupo de WhatsApp. (-1 tropa en ${cell.nodeName})`,
        };
      });
    }
  }

  // ── GREEN — Los Fundadores Corruptos ───────────────────────────────────────
  if (greenCells.length > 0) {
    const greenProd = greenCells.filter(c => c.isProduction);
    if (greenProd.length > 0) {
      candidates.push(() => {
        const cell = rnd(greenProd);
        const d    = Math.floor(Math.random() * maxDelta) + 1;
        return {
          id: "green_lucaina_spike", type: "POSITIVE", severity,
          targetFaction: "ai2", targetCell: cell.h3Index, troopsDelta: d,
          title: "Pico de Lucaína",
          description: `Un nodo de infraestructura emitió un pico de Lucaína. Los Verdes dejaron de parpadear en binario. (+${d} tropas en ${cell.nodeName})`,
        };
      });
    }

    const greenNonProd = greenCells.filter(c => !c.isProduction && c.troops >= 2);
    if (greenNonProd.length > 0) {
      candidates.push(() => {
        const cell = rnd(greenNonProd);
        const d    = -(Math.floor(Math.random() * Math.min(maxDelta, cell.troops - 1)) + 1);
        return {
          id: "green_binary_abstinence", type: "NEGATIVE", severity,
          targetFaction: "ai2", targetCell: cell.h3Index, troopsDelta: d,
          title: "Abstinencia binaria",
          description: `Una célula Verde entró en abstinencia y empezó a ver nodos de infraestructura donde solo había pasto. (${d} tropas en ${cell.nodeName})`,
        };
      });
    }

    const greenCommon = greenCells.filter(c => !c.isProduction && !c.isNodeActive && c.troops >= 2);
    if (greenCommon.length > 0) {
      candidates.push(() => {
        const cell = rnd(greenCommon);
        return {
          id: "green_node_obsession", type: "NEGATIVE", severity,
          targetFaction: "ai2", targetCell: cell.h3Index, troopsDelta: -1,
          title: "Obsesión de nodo",
          description: `Los Verdes abandonaron una posición porque alguien dijo 'nodo de infraestructura' tres veces seguidas. (-1 tropa en ${cell.nodeName})`,
        };
      });
    }
  }

  // ── PLAYER — Los Custodios del BBS ─────────────────────────────────────────
  if (playerCells.length > 0) {
    candidates.push(() => {
      const cell = rnd(playerCells);
      const d    = Math.floor(Math.random() * maxDelta) + 1;
      return {
        id: "player_signal_boost", type: "POSITIVE", severity,
        targetFaction: "player", targetCell: cell.h3Index, troopsDelta: d,
        title: "Señal reforzada del BBS",
        description: `Los Custodios detectaron una anomalía favorable en la Mesh. (+${d} tropas en ${cell.nodeName})`,
      };
    });

    const playerVuln = playerCells.filter(c => c.troops >= 2);
    if (playerVuln.length > 0) {
      candidates.push(() => {
        const cell = rnd(playerVuln);
        const d    = -(Math.floor(Math.random() * Math.min(maxDelta, cell.troops - 1)) + 1);
        return {
          id: "player_interference", type: "NEGATIVE", severity,
          targetFaction: "player", targetCell: cell.h3Index, troopsDelta: d,
          title: "Interferencia en la Mesh",
          description: `Una interferencia misteriosa afectó una posición de los Custodios del BBS. (${d} tropas en ${cell.nodeName})`,
        };
      });
    }
  }

  // ── NEUTRAL — Tormenta eléctrica ───────────────────────────────────────────
  const activeAny = Object.values(cells).filter(c => c.isNodeActive && c.troops >= 2);
  if (activeAny.length > 0) {
    candidates.push(() => {
      const cell = rnd(activeAny);
      return {
        id: "storm", type: "NEUTRAL", severity,
        targetFaction: cell.owner, targetCell: cell.h3Index, troopsDelta: -1,
        title: "Tormenta eléctrica",
        description: `El clima decidió participar de la guerra, como suele hacer en el Sudoeste Bonaerense. (-1 tropa en ${cell.nodeName})`,
      };
    });
  }

  const shuffled = [...candidates].sort(() => Math.random() - 0.5);
  for (const fn of shuffled) {
    const ev = fn();
    if (ev) return ev;
  }
  return null;
}
