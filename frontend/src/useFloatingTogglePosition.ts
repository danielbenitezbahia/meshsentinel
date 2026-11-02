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

/** Posición (fixed, respecto al viewport) pegada a la esquina superior
 * IZQUIERDA del área del mapa -- usada por el toggle de tema día/noche en
 * Mesh3DView, donde la esquina superior derecha ya está ocupada por el
 * NavigationControl de MapLibre y por btn-view-toggle. Mismo motivo que
 * btn-view-toggle para usar fixed+portal en vez de absolute (ver
 * App.css: .btn-view-toggle). */
export function useFloatingTopLeft(containerRef: RefObject<HTMLElement | null>) {
  const [pos, setPos] = useState({ top: 10, left: 10 });

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const update = () => {
      const rect = el.getBoundingClientRect();
      setPos({
        top: Math.round(rect.top + 10),
        left: Math.round(rect.left + 10),
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
