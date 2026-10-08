// Capa custom de MapLibre que dibuja, con three.js, un poste + esfera por
// nodo desde el piso del relieve hasta su altura "efectiva" (relieve / GPS /
// manual). A diferencia de los Marker de MapLibre (que se apoyan siempre
// sobre el terreno), esto sí flota de verdad en el espacio 3D.
//
// Referencia del mecanismo (matrices, MercatorCoordinate, WebGLRenderer
// compartiendo el contexto gl del mapa): ejemplo oficial "Adding 3D models
// using three.js on terrain" de la doc de MapLibre.
import * as THREE from "three";
import { MercatorCoordinate } from "maplibre-gl";
import type { Map as MapLibreMap, CustomLayerInterface, CustomRenderMethodInput } from "maplibre-gl";

export interface FloatingNode {
  id: string;
  lng: number;
  lat: number;
  groundElevation: number;
  floatElevation: number;
  color: string;
  selected: boolean;
}

// Una conexión entre dos nodos, ya resuelta a los dos extremos en 3D (lng/lat
// + su altura EFECTIVA, no la del piso) — se dibuja como un cilindro recto
// entre esos dos puntos, cortando derecho por el aire en vez de seguir el
// relieve del terreno en el medio.
export interface FloatingEdge {
  id: string;
  fromLng: number;
  fromLat: number;
  fromElevation: number;
  toLng: number;
  toLat: number;
  toElevation: number;
  color: string;
}

const SPHERE_RADIUS_METERS = 45;
const EDGE_RADIUS_METERS = 5;

// Geometría compartida por TODOS los cilindros de conexión: un cilindro
// "unidad" (radio 1, altura 1, a lo largo del eje Y) que cada instancia
// escala/rota/traslada para volverse el segmento real — evita crear/destruir
// geometría por cada línea en cada draw().
const EDGE_GEOMETRY = new THREE.CylinderGeometry(1, 1, 1, 8);
const UP_AXIS = new THREE.Vector3(0, 1, 0);

type ObjEntry = { sphere: THREE.Mesh; pole: THREE.Line };

export class FloatingNodesLayer implements CustomLayerInterface {
  id = "floating-nodes";
  type = "custom" as const;
  renderingMode = "3d" as const;

  private map: MapLibreMap | null = null;
  private camera = new THREE.Camera();
  private scene = new THREE.Scene();
  private renderer: THREE.WebGLRenderer | null = null;
  private originLngLat: [number, number] = [0, 0];
  private originElevation = 0;
  private objects = new Map<string, ObjEntry>();
  private edgeObjects = new Map<string, THREE.Mesh>();
  // Última matriz de proyección usada en render(), para poder convertir un
  // punto 3D (lng/lat/altura) a píxeles de pantalla desde afuera (ver
  // projectToScreen) — lo usa el hover de las líneas en Mesh3DView, ya que
  // las conexiones ahora viven acá (capa three.js) y no como feature de
  // MapLibre, así que no hay query de features nativo para detectarlas.
  private lastProjectionMatrix: THREE.Matrix4 | null = null;

  // NOTA: a propósito NO se rota/escala `scene` acá. Esa rotación aparece en
  // el ejemplo oficial de MapLibre+three.js porque ahí se posiciona un único
  // modelo GLTF externo (con su propio eje "arriba") en el origen local
  // (0,0,0) — rota la ORIENTACIÓN del modelo, no la posición. Acá se posicionan
  // varios objetos propios (esfera+poste) en offsets reales no nulos, y esa
  // rotación de escena termina mezclando el eje norte/sur con el de altura al
  // combinarse con la matriz `l` de render(). Más simple: generar la geometría
  // directamente en la convención este/norte/altura que espera `l` (ver
  // localPosition) y dejar la escena sin transformar.

  onAdd(map: MapLibreMap, gl: WebGLRenderingContext | WebGL2RenderingContext) {
    this.map = map;
    this.renderer = new THREE.WebGLRenderer({
      canvas: map.getCanvas(),
      context: gl as WebGLRenderingContext,
      antialias: true,
    });
    this.renderer.autoClear = false;
  }

  onRemove() {
    for (const { sphere, pole } of this.objects.values()) {
      sphere.geometry.dispose();
      (sphere.material as THREE.Material).dispose();
      pole.geometry.dispose();
      (pole.material as THREE.Material).dispose();
    }
    this.objects.clear();
    for (const mesh of this.edgeObjects.values()) {
      // La geometría (EDGE_GEOMETRY) es compartida entre todas las
      // instancias — no se dispone acá, solo el material propio de cada una.
      (mesh.material as THREE.Material).dispose();
    }
    this.edgeObjects.clear();
    this.renderer?.dispose();
    this.renderer = null;
    this.map = null;
  }

  /** Punto de referencia local para evitar errores de precisión de punto
   * flotante lejos del origen. Cualquier punto cercano a los nodos sirve. */
  setOrigin(lng: number, lat: number, elevation: number) {
    this.originLngLat = [lng, lat];
    this.originElevation = elevation;
  }

  /** Posición local lista para asignar a `object.position`/`setFromPoints`,
   * en metros reales este/norte/altura relativos al origen.
   *
   * La matriz `l` de render() espera esta convención exacta (x=este,
   * y=norte-positivo, z=altura) — por eso el signo de "north" está invertido
   * respecto de la resta cruda de coordenadas mercator: la coordenada Y de
   * mercator CRECE hacia el sur, así que "más al norte" = Y de mercator más
   * chico. `l` ya tiene un -scale en su propio eje Y pensado justamente para
   * convertir desde esta convención (norte-positivo) al mercator real.
   */
  private localPosition(lng: number, lat: number, elevation: number): THREE.Vector3 {
    const origin = MercatorCoordinate.fromLngLat(this.originLngLat, this.originElevation);
    const scale = origin.meterInMercatorCoordinateUnits();
    const p = MercatorCoordinate.fromLngLat([lng, lat], elevation);
    const east = (p.x - origin.x) / scale;
    const north = -(p.y - origin.y) / scale;
    const up = (p.z - origin.z) / scale;
    return new THREE.Vector3(east, north, up);
  }

  setNodes(nodes: FloatingNode[]) {
    const seen = new Set<string>();

    for (const n of nodes) {
      seen.add(n.id);
      const groundPos = this.localPosition(n.lng, n.lat, n.groundElevation);
      const floatPos = this.localPosition(n.lng, n.lat, n.floatElevation);
      const color = new THREE.Color(n.color);

      let entry = this.objects.get(n.id);
      if (!entry) {
        const sphere = new THREE.Mesh(
          new THREE.SphereGeometry(SPHERE_RADIUS_METERS, 12, 12),
          new THREE.MeshBasicMaterial({ color })
        );
        const pole = new THREE.Line(
          new THREE.BufferGeometry(),
          new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.65 })
        );
        this.scene.add(sphere);
        this.scene.add(pole);
        entry = { sphere, pole };
        this.objects.set(n.id, entry);
      }

      entry.sphere.position.copy(floatPos);
      entry.sphere.scale.setScalar(n.selected ? 1.6 : 1);
      (entry.sphere.material as THREE.MeshBasicMaterial).color.set(color);

      entry.pole.geometry.setFromPoints([groundPos, floatPos]);
      (entry.pole.material as THREE.LineBasicMaterial).color.set(color);
    }

    for (const [id, entry] of this.objects.entries()) {
      if (seen.has(id)) continue;
      this.scene.remove(entry.sphere);
      this.scene.remove(entry.pole);
      entry.sphere.geometry.dispose();
      (entry.sphere.material as THREE.Material).dispose();
      entry.pole.geometry.dispose();
      (entry.pole.material as THREE.Material).dispose();
      this.objects.delete(id);
    }

    this.map?.triggerRepaint();
  }

  /** Dibuja cada conexión como un cilindro recto entre la altura EFECTIVA de
   * un nodo y la del otro — a propósito no sigue el relieve en el medio, es
   * la línea recta en el espacio 3D (p.ej. de Cerro Tres Picos a ~1000m
   * bajando derecho hasta un nodo a ~400m), igual que pediste. */
  setEdges(edges: FloatingEdge[]) {
    const seen = new Set<string>();

    for (const e of edges) {
      seen.add(e.id);
      const start = this.localPosition(e.fromLng, e.fromLat, e.fromElevation);
      const end = this.localPosition(e.toLng, e.toLat, e.toElevation);
      const mid = start.clone().add(end).multiplyScalar(0.5);
      const direction = end.clone().sub(start);
      const length = direction.length();
      const color = new THREE.Color(e.color);

      let mesh = this.edgeObjects.get(e.id);
      if (!mesh) {
        mesh = new THREE.Mesh(EDGE_GEOMETRY, new THREE.MeshBasicMaterial({ color }));
        this.scene.add(mesh);
        this.edgeObjects.set(e.id, mesh);
      }

      mesh.position.copy(mid);
      mesh.scale.set(EDGE_RADIUS_METERS, length, EDGE_RADIUS_METERS);
      if (length > 1e-6) {
        mesh.quaternion.setFromUnitVectors(UP_AXIS, direction.normalize());
      }
      (mesh.material as THREE.MeshBasicMaterial).color.set(color);
    }

    for (const [id, mesh] of this.edgeObjects.entries()) {
      if (seen.has(id)) continue;
      this.scene.remove(mesh);
      (mesh.material as THREE.Material).dispose();
      this.edgeObjects.delete(id);
    }

    this.map?.triggerRepaint();
  }

  /** Proyecta un punto 3D (lng/lat/altura) a píxeles de pantalla, usando la
   * MISMA matriz que el último render() — para hit-testing de hover en
   * Mesh3DView.tsx sobre las líneas (que ya no son features de MapLibre).
   * Devuelve null si todavía no hubo ningún render (matriz no disponible) o
   * si el punto cae detrás de la cámara. */
  projectToScreen(lng: number, lat: number, elevation: number): { x: number; y: number } | null {
    if (!this.lastProjectionMatrix || !this.map) return null;
    const local = this.localPosition(lng, lat, elevation);
    const clip = local.clone().applyMatrix4(this.lastProjectionMatrix);
    // Vector3.applyMatrix4 ya hace la división perspectiva (/w); si w era
    // negativo o ~0 el punto está detrás/en el plano de cámara — descartar.
    if (!Number.isFinite(clip.x) || !Number.isFinite(clip.y)) return null;
    const canvas = this.map.getCanvas();
    const width = canvas.clientWidth || canvas.width;
    const height = canvas.clientHeight || canvas.height;
    return {
      x: (clip.x * 0.5 + 0.5) * width,
      y: (1 - (clip.y * 0.5 + 0.5)) * height,
    };
  }

  render(_gl: WebGL2RenderingContext, options: CustomRenderMethodInput) {
    if (!this.renderer || !this.map) return;

    const origin = MercatorCoordinate.fromLngLat(this.originLngLat, this.originElevation);
    const scale = origin.meterInMercatorCoordinateUnits();

    const m = new THREE.Matrix4().fromArray(options.defaultProjectionData.mainMatrix);
    const l = new THREE.Matrix4()
      .makeTranslation(origin.x, origin.y, origin.z)
      .scale(new THREE.Vector3(scale, -scale, scale));

    this.camera.projectionMatrix = m.multiply(l);
    this.lastProjectionMatrix = this.camera.projectionMatrix.clone();
    this.renderer.resetState();
    this.renderer.render(this.scene, this.camera);
    // OJO: no llamar map.triggerRepaint() acá — eso generaría un loop de
    // repintado infinito (cada render() pide otro render()) y el mapa nunca
    // llegaría a estado "idle", rompiendo cualquier lógica que dependa de
    // ese evento (ej. el reintento de queryTerrainElevation una vez que
    // cargan los tiles). setNodes() ya pide un repaint puntual cuando
    // realmente cambian los datos; no hace falta forzarlo en cada frame.
  }
}
