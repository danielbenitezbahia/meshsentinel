import type { CSSProperties } from "react";
import { createPortal } from "react-dom";
import { useMapTheme } from "../useMapTheme";

interface Props {
  style?: CSSProperties;
  // Si se pasa, el botón se renderiza con position:fixed vía portal a
  // document.body en vez de quedar como hijo absolute dentro del mapa --
  // necesario en Mesh3DView: un position:absolute hermano del canvas WebGL
  // de MapLibre/three.js queda tapado por el canvas en algunos navegadores
  // salvo mientras el mapa se está redibujando activamente (mismo motivo
  // por el que btn-view-toggle ya usa este patrón, ver App.css).
  portal?: boolean;
}

export default function MapThemeToggle({ style, portal }: Props) {
  const [theme, toggle] = useMapTheme();

  const button = (
    <button
      type="button"
      className={`map-theme-toggle${portal ? " map-theme-toggle-fixed" : ""}`}
      style={style}
      onClick={toggle}
      title={theme === "dark" ? "Cambiar a modo día" : "Cambiar a modo noche"}
    >
      {theme === "dark" ? "🌙" : "☀️"}
    </button>
  );

  return portal ? createPortal(button, document.body) : button;
}
