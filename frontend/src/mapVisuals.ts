// Compartido entre MapView (2D) y Mesh3DView (3D) para que un mismo nodo se
// vea igual (mismo color) y se oculte con el mismo criterio en los dos mapas.
import type { MeshNode, MeshEdge } from "./types";

// Nodos vistos hace más de esto no se dibujan. node_stats es un registro
// permanente que nunca se borra, así que sin este corte un nodo visto hace
// meses queda dibujado para siempre.
export const MAX_AGE_MINUTES_ON_MAP = 360; // 6 horas

export const PALETTE = [
  "#7b1fa2", "#c62828", "#e65100", "#2e7d32",
  "#1565c0", "#00695c", "#4527a0", "#880e4f",
  "#4e342e", "#01579b", "#558b2f", "#f57f17",
  "#37474f", "#6a1520", "#006064", "#827717",
];

export function hashNodeColor(nodeId: string): string {
  let h = 0;
  for (let i = 0; i < nodeId.length; i++) {
    h = (h * 31 + nodeId.charCodeAt(i)) >>> 0;
  }
  return PALETTE[h % PALETTE.length];
}

export function isRecentEnoughForMap(lastSeenMinsAgo: number | null | undefined): boolean {
  return lastSeenMinsAgo != null && lastSeenMinsAgo <= MAX_AGE_MINUTES_ON_MAP;
}

// Nodos de infraestructura conocidos (repetidores en altura, cerros, etc.)
// -- matcheo por substring del nombre, no case-sensitive. Compartido con
// MeshWarsView (que les da más tolerancia de actividad) y con la heurística
// de posición implausible (que les da más tolerancia de distancia, porque
// un repetidor en un cerro alcanza mucho más lejos que un nodo a nivel de
// piso).
const INFRA_KEYWORDS = ["carpa", "3 picos", "picos", "estomba", "brandsen", "dorrego", "monte", "malal"];

export function isInfraNode(name: string | null | undefined): boolean {
  const n = (name ?? "").toLowerCase();
  return INFRA_KEYWORDS.some((k) => n.includes(k));
}

// Nodos ocultos a mano porque su posición reportada no es la real -- hasta
// que se corrija (pedido 2026-10-28). Sacar el nombre de acá cuando se
// confirme la ubicación correcta.
const HIDDEN_NODE_NAMES = new Set(["NUBE", "NR-I", "Nodo Irene", "Uranio"]);

export function isHiddenFromMap(
  shortName: string | null | undefined,
  longName: string | null | undefined
): boolean {
  return HIDDEN_NODE_NAMES.has(shortName ?? "") || HIDDEN_NODE_NAMES.has(longName ?? "");
}

// Heurística para detectar nodos cuya posición reportada probablemente no
// es la real (ej. Uranio figurando en Darregueira cuando está en Coronel
// Dorrego, ~200km de diferencia) sin tener que cargarlos a mano en
// HIDDEN_NODE_NAMES cada vez. Complementa esa lista, no la reemplaza -- si
// el umbral no agarra algún caso, se lo sigue pudiendo agregar a mano.
//
// Idea: para cada arista, la distancia entre las posiciones reportadas de
// sus dos extremos tiene que ser físicamente plausible para un enlace LoRa
// directo. La clave NO es qué tan bueno es el SNR medido -- LoRa está
// diseñado para decodificar señales muy por debajo del piso de ruido, así
// que un SNR flojo en el borde del alcance es comportamiento normal, no una
// señal de alerta. Lo que de verdad importa es si HUBO un SNR medido o no:
// un paquete real que se decodificó (con el SNR que sea) es evidencia de
// que ese enlace existió de verdad en ese momento; una arista SIN dato de
// SNR (ej. de relleno por traceroute, ver _sweep_orphan_nodes en
// interface.py) no tiene ninguna evidencia de RF directa detrás, solo
// adyacencia de ruteo -- ahí sí conviene ser más conservador. En los dos
// casos, un nodo de infraestructura conocido (isInfraNode, típicamente un
// repetidor en altura/cerro) alcanza mucho más lejos que uno a nivel de
// piso, así que se le da bastante más margen. Terreno de referencia: pampa
// llana del sudoeste bonaerense, no sierra ni ciudad.
function maxPlausibleLinkKm(snr: number | null | undefined, involvesInfra: boolean): number {
  if (snr != null) return involvesInfra ? 150 : 50;
  return involvesInfra ? 100 : 20; // sin dato de señal: criterio más conservador
}

function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

// Devuelve los node_id de los nodos cuya posición reportada es sospechosa:
// SOLO los que tienen al menos una arista implausible (ver
// maxPlausibleLinkKm) Y NINGUNA arista plausible. Si un nodo tiene aunque
// sea un vecino a distancia plausible, esa arista es evidencia a favor de
// que SU posición está bien -- la arista implausible que comparte con otro
// nodo es entonces más probable que sea culpa del OTRO extremo, no de este.
// Sin esta distinción, un nodo bien ubicado que simplemente tiene la mala
// suerte de tener un vecino mal ubicado terminaría ocultándose también.
export function computeSuspectNodeIds(nodes: MeshNode[], edges: MeshEdge[]): Set<string> {
  const byId = new Map(nodes.map((n) => [n.node_id, n]));
  const hasPlausibleEdge = new Set<string>();
  const hasImplausibleEdge = new Set<string>();

  for (const e of edges) {
    const a = byId.get(e.reporter);
    const b = byId.get(e.neighbor);
    if (!a || !b || a.lat == null || a.lon == null || b.lat == null || b.lon == null) continue;
    const snr = e.snr ?? e.snr_reverse;
    const distKm = haversineKm(a.lat, a.lon, b.lat, b.lon);
    const involvesInfra =
      isInfraNode(a.short_name) || isInfraNode(a.long_name) ||
      isInfraNode(b.short_name) || isInfraNode(b.long_name);
    const target = distKm > maxPlausibleLinkKm(snr, involvesInfra) ? hasImplausibleEdge : hasPlausibleEdge;
    target.add(a.node_id);
    target.add(b.node_id);
  }

  const suspects = new Set<string>();
  for (const id of hasImplausibleEdge) {
    if (!hasPlausibleEdge.has(id)) suspects.add(id);
  }
  return suspects;
}

// Color de una arista según su SNR -- mismo criterio en 2D y 3D. Sin SNR
// (ej. aristas de relleno por traceroute, que no traen ese dato) usa un
// celeste vivo en vez de gris apagado, para que se vea igual de viva que
// el resto -- no por eso significa "mala", solo "sin medición".
export function snrToColor(snr: number | null | undefined): string {
  return snr == null ? "#29b6f6" : snr >= 8 ? "#00e676" : snr >= 4 ? "#ffeb3b" : snr >= 0 ? "#ff9800" : "#f44336";
}
