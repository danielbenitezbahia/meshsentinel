// Compartido entre MapView (2D) y Mesh3DView (3D) para que un mismo nodo se
// vea igual (mismo color) y se oculte con el mismo criterio en los dos mapas.

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
