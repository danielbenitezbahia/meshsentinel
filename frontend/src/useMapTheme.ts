import { useState, useEffect, useCallback } from "react";

export type MapTheme = "dark" | "light";

const STORAGE_KEY = "meshboard_map_theme";

function readStored(): MapTheme {
  try {
    return localStorage.getItem(STORAGE_KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

// Tema compartido por TODOS los mapas de la webapp (mesh 2D/3D, incendios,
// propagación, trayectorias): un toggle en cualquiera de ellos cambia a
// todos a la vez (vía el Set de listeners, ya que cada mapa tiene su propio
// estado de React) y la elección persiste entre visitas. Por default
// arrancan en modo nocturno -- pedido explícito del usuario.
let current: MapTheme = readStored();
const listeners = new Set<(t: MapTheme) => void>();

function setTheme(next: MapTheme) {
  current = next;
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // localStorage deshabilitado (modo privado, etc.) -- el tema sigue
    // funcionando en memoria para esta sesión de pestaña.
  }
  listeners.forEach((l) => l(next));
}

export function useMapTheme(): [MapTheme, () => void] {
  const [theme, setLocalTheme] = useState<MapTheme>(current);

  useEffect(() => {
    listeners.add(setLocalTheme);
    return () => {
      listeners.delete(setLocalTheme);
    };
  }, []);

  const toggle = useCallback(() => {
    setTheme(current === "dark" ? "light" : "dark");
  }, []);

  return [theme, toggle];
}
