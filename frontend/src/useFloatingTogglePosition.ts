import { useEffect, useState, type RefObject } from "react";

/** Posición (fixed, respecto al viewport) para flotar justo debajo de los
 * controles de zoom de MapLibre, pegado a la esquina superior derecha del
 * área del mapa -- medida en vivo porque esa esquina se mueve según si hay
 * cartel de error, altura de la topbar, etc. */
export function useFloatingTogglePosition(containerRef: RefObject<HTMLElement | null>) {
  const [pos, setPos] = useState({ top: 110, right: 16 });

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const update = () => {
      const rect = el.getBoundingClientRect();
      setPos({
        top: Math.round(rect.top + 110),
        right: Math.round(window.innerWidth - rect.right + 16),
      });
    };

    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    window.addEventListener("resize", update);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", update);
    };
  }, [containerRef]);

  return pos;
}
