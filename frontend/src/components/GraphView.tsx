import { useEffect, useRef } from "react";
import cytoscape, { Core, ElementDefinition } from "cytoscape";
import type { MeshNode, MeshEdge } from "../types";

interface Props {
  nodes: MeshNode[];
  edges: MeshEdge[];
  selectedId: string | null;
  onSelectNode: (id: string) => void;
}

function snrColor(snr: number | null | undefined): string {
  if (snr == null) return "#37474f";
  if (snr >= 8) return "#00e676";
  if (snr >= 4) return "#ffeb3b";
  if (snr >= 0) return "#ff9800";
  return "#f44336";
}

function edgeWidth(snr: number | null | undefined): number {
  if (snr == null) return 1;
  if (snr >= 8) return 3;
  if (snr >= 4) return 2;
  if (snr >= 0) return 1.5;
  return 1;
}

export default function GraphView({ nodes, edges, selectedId, onSelectNode }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);

  useEffect(() => {
    if (!containerRef.current || nodes.length === 0) return;

    if (cyRef.current) {
      cyRef.current.destroy();
      cyRef.current = null;
    }

    const nodeSet = new Set(nodes.map((n) => n.node_id));

    const cyNodes: ElementDefinition[] = nodes.map((n) => {
      const fresh = n.last_seen_mins_ago != null && n.last_seen_mins_ago < 60;
      const hops = n.hops_from_bbs ?? 99;
      return {
        data: {
          id: n.node_id,
          label: n.short_name || n.node_id,
          hops,
          fresh,
          lastSeenMins: n.last_seen_mins_ago,
          neighborCount: n.neighbor_count,
        },
      };
    });

    const seenEdges = new Set<string>();
    const cyEdges: ElementDefinition[] = [];
    for (const e of edges) {
      if (!nodeSet.has(e.reporter) || !nodeSet.has(e.neighbor)) continue;
      const key = [e.reporter, e.neighbor].sort().join("||");
      if (seenEdges.has(key)) continue;
      seenEdges.add(key);
      const snr = e.snr ?? e.snr_reverse;
      cyEdges.push({
        data: {
          id: `e-${key}`,
          source: e.reporter,
          target: e.neighbor,
          snr,
          snrLabel: snr != null ? `${snr}dB` : "",
          color: snrColor(snr),
          width: edgeWidth(snr),
        },
      });
    }

    const hasEdges = cyEdges.length > 0;

    // When no neighborinfo yet, create synthetic edges by hops to show topology hint
    const layoutEdges: ElementDefinition[] = hasEdges ? cyEdges : (() => {
      const byHops: Record<number, string[]> = {};
      for (const n of nodes) {
        const h = n.hops_from_bbs ?? 99;
        (byHops[h] = byHops[h] || []).push(n.node_id);
      }
      const synth: ElementDefinition[] = [];
      const hopLevels = Object.keys(byHops).map(Number).sort((a, b) => a - b);
      for (let i = 1; i < hopLevels.length; i++) {
        const parents = byHops[hopLevels[i - 1]];
        const children = byHops[hopLevels[i]];
        children.forEach((child, idx) => {
          const parent = parents[idx % parents.length];
          synth.push({
            data: {
              id: `s-${parent}-${child}`,
              source: parent,
              target: child,
              snr: null,
              snrLabel: "",
              color: "#1e3a5f",
              width: 1,
              synthetic: true,
            },
          });
        });
      }
      return synth;
    })();

    const cy = cytoscape({
      container: containerRef.current,
      elements: [...cyNodes, ...layoutEdges],
      style: [
        {
          selector: "node",
          style: {
            "background-color": "#1565c0",
            "border-width": 2,
            "border-color": "#42a5f5",
            label: "data(label)",
            color: "#cfd8dc",
            "font-size": 9,
            "text-valign": "bottom",
            "text-margin-y": 4,
            "text-outline-width": 1,
            "text-outline-color": "#050e1a",
            width: 22,
            height: 22,
          },
        },
        {
          selector: "node[?fresh]",
          style: {
            "background-color": "#1976d2",
            "border-color": "#90caf9",
          },
        },
        {
          selector: "node:selected",
          style: {
            "background-color": "#e91e63",
            "border-color": "#f48fb1",
            "border-width": 3,
          },
        },
        {
          selector: "edge",
          style: {
            width: "data(width)",
            "line-color": "data(color)",
            "curve-style": "bezier",
            "target-arrow-shape": "triangle",
            "target-arrow-color": "data(color)",
            "arrow-scale": 1.2,
            label: "data(snrLabel)",
            "font-size": 8,
            color: "#607d8b",
            "text-outline-width": 1,
            "text-outline-color": "#050e1a",
            "text-rotation": "autorotate",
          },
        },
        {
          selector: "edge[?synthetic]",
          style: {
            "line-style": "dashed",
            "line-dash-pattern": [4, 4],
            "target-arrow-shape": "triangle",
            opacity: 0.35,
          },
        },
      ],
      layout: {
        name: "breadthfirst",
        directed: true,
        padding: 30,
        spacingFactor: 1.6,
        animate: false,
        roots: nodes
          .filter((n) => n.hops_from_bbs === 0)
          .map((n) => `#${CSS.escape(n.node_id)}`),
      },
    });

    cy.on("tap", "node", (evt) => {
      onSelectNode(evt.target.id());
    });
    cy.on("tap", (evt) => {
      if (evt.target === cy) onSelectNode("");
    });

    cyRef.current = cy;
    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [nodes, edges]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.$("node:selected").unselect();
    if (selectedId) {
      const el = cy.$(`#${CSS.escape(selectedId)}`);
      el.select();
      if (el.length) {
        cy.animate({ center: { eles: el }, zoom: cy.zoom() }, { duration: 300 });
      }
    }
  }, [selectedId]);

  return (
    <div ref={containerRef} style={{ width: "100%", height: "100%", background: "#0a1929" }} />
  );
}
