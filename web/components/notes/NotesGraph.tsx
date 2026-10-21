"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Minus, Plus, RotateCcw, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { IconButton } from "@/shared/ui/IconButton";
import { apiFetch, apiUrl } from "@/lib/api";

type GraphNode = { id: string; links: number; read_only: boolean };
type GraphEdge = { source: string; target: string };
type Graph = { nodes: GraphNode[]; edges: GraphEdge[] };
type Placed = GraphNode & { x: number; y: number };

const W = 1000;
const H = 700;

/** Fruchterman–Reingold layout, run once per data set from a deterministic start. */
export function layout(graph: Graph): Placed[] {
  const n = graph.nodes.length;
  if (!n) return [];
  const index = new Map(graph.nodes.map((node, i) => [node.id, i]));
  const pos = graph.nodes.map((node, i) => {
    const angle = (2 * Math.PI * i) / n;
    return { ...node, x: W / 2 + Math.cos(angle) * W * 0.3, y: H / 2 + Math.sin(angle) * H * 0.3 };
  });
  const k = Math.sqrt((W * H) / n) * 0.7;
  const edges = graph.edges.flatMap((e) => {
    const a = index.get(e.source);
    const b = index.get(e.target);
    return a === undefined || b === undefined ? [] : [[a, b] as const];
  });
  const rounds = n > 250 ? 120 : 250;
  for (let step = 0; step < rounds; step++) {
    const temp = (W / 10) * (1 - step / rounds) + 1;
    const dx = new Array<number>(n).fill(0);
    const dy = new Array<number>(n).fill(0);
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let vx = pos[i].x - pos[j].x;
        let vy = pos[i].y - pos[j].y;
        const d = Math.max(Math.hypot(vx, vy), 0.01);
        const force = (k * k) / d;
        vx = (vx / d) * force;
        vy = (vy / d) * force;
        dx[i] += vx; dy[i] += vy; dx[j] -= vx; dy[j] -= vy;
      }
    }
    for (const [a, b] of edges) {
      const vx = pos[a].x - pos[b].x;
      const vy = pos[a].y - pos[b].y;
      const d = Math.max(Math.hypot(vx, vy), 0.01);
      const force = (d * d) / k;
      dx[a] -= (vx / d) * force; dy[a] -= (vy / d) * force;
      dx[b] += (vx / d) * force; dy[b] += (vy / d) * force;
    }
    for (let i = 0; i < n; i++) {
      // a pull toward the centre keeps unlinked notes on the canvas
      dx[i] += (W / 2 - pos[i].x) * 0.02 * k;
      dy[i] += (H / 2 - pos[i].y) * 0.02 * k;
      const d = Math.max(Math.hypot(dx[i], dy[i]), 0.01);
      pos[i].x = Math.min(W - 20, Math.max(20, pos[i].x + (dx[i] / d) * Math.min(d, temp)));
      pos[i].y = Math.min(H - 20, Math.max(20, pos[i].y + (dy[i] / d) * Math.min(d, temp)));
    }
  }
  return pos;
}

/**
 * Every note as a dot and every [[link]] as a line. The picture is for sighted pointer users; the
 * list beside it carries the same notes and link counts for keyboard and screen-reader users.
 */
/** A viewBox that frames every node with some margin. */
function fit(nodes: Placed[]) {
  if (!nodes.length) return { x: 0, y: 0, w: W, h: H };
  const xs = nodes.map((n) => n.x);
  const ys = nodes.map((n) => n.y);
  const pad = 70;
  const w = Math.max(Math.max(...xs) - Math.min(...xs) + pad * 2, 240);
  const h = Math.max(Math.max(...ys) - Math.min(...ys) + pad * 2, 160);
  const cx = (Math.max(...xs) + Math.min(...xs)) / 2;
  const cy = (Math.max(...ys) + Math.min(...ys)) / 2;
  return { x: cx - w / 2, y: cy - h / 2, w, h };
}

export default function NotesGraph({
  current,
  onOpen,
  onClose,
}: {
  current: string | null;
  onOpen: (name: string) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [graph, setGraph] = useState<Graph | null>(null);
  const [view, setView] = useState({ x: 0, y: 0, w: W, h: H });
  const [box, setBox] = useState({ w: W, h: H });
  const canvas = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<string | null>(null);
  const drag = useRef<{ x: number; y: number; moved: boolean } | null>(null);

  useEffect(() => {
    let live = true;
    apiFetch(apiUrl("/api/notes/_graph"))
      .then((r) => (r.ok ? r.json() : { nodes: [], edges: [] }))
      .then((g) => live && setGraph(g))
      .catch(() => live && setGraph({ nodes: [], edges: [] }));
    return () => {
      live = false;
    };
  }, []);

  const placed = useMemo(() => (graph ? layout(graph) : []), [graph]);
  useEffect(() => setView(fit(placed)), [placed]);
  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const watch = new ResizeObserver(() => setBox({ w: el.clientWidth || W, h: el.clientHeight || H }));
    watch.observe(el);
    return () => watch.disconnect();
  }, []);
  // viewBox units per screen pixel: sizes below are in pixels, so dots and labels stay readable at any zoom.
  const unit = Math.max(view.w / box.w, view.h / box.h);
  const byId = useMemo(() => new Map(placed.map((p) => [p.id, p])), [placed]);
  const linked = useMemo(() => {
    const map = new Map<string, Set<string>>();
    for (const e of graph?.edges ?? []) {
      (map.get(e.source) ?? map.set(e.source, new Set()).get(e.source)!).add(e.target);
      (map.get(e.target) ?? map.set(e.target, new Set()).get(e.target)!).add(e.source);
    }
    return map;
  }, [graph]);
  const showAllLabels = placed.length <= 60;
  const list = useMemo(() => [...placed].sort((a, b) => b.links - a.links || a.id.localeCompare(b.id)), [placed]);

  const zoom = (factor: number, cx = view.x + view.w / 2, cy = view.y + view.h / 2) =>
    setView((v) => {
      const w = Math.min(W * 4, Math.max(60, v.w * factor));
      const h = (w / v.w) * v.h;
      return { w, h, x: cx - ((cx - v.x) / v.w) * w, y: cy - ((cy - v.y) / v.h) * h };
    });

  // Screen point -> viewBox point. The SVG is letterboxed ("meet"), so measure from its centre.
  const point = (event: React.PointerEvent | React.WheelEvent, svg: SVGSVGElement) => {
    const rect = svg.getBoundingClientRect();
    return {
      x: view.x + view.w / 2 + (event.clientX - (rect.left + rect.width / 2)) * unit,
      y: view.y + view.h / 2 + (event.clientY - (rect.top + rect.height / 2)) * unit,
    };
  };

  return (
    <section aria-label={t("Notes graph")} className="flex min-h-0 flex-1 flex-col md:flex-row">
      <div ref={canvas} className="relative min-h-[50vh] min-w-0 flex-1 border-b border-[var(--border)] md:border-b-0 md:border-r">
        <div className="absolute right-2 top-2 z-10 flex gap-1 rounded-xl border border-[var(--border)] bg-[var(--background)] p-1">
          <IconButton size="sm" label={t("Zoom in")} onClick={() => zoom(0.8)} icon={<Plus size={14} />} />
          <IconButton size="sm" label={t("Zoom out")} onClick={() => zoom(1.25)} icon={<Minus size={14} />} />
          <IconButton size="sm" label={t("Reset view")} onClick={() => setView(fit(placed))} icon={<RotateCcw size={14} />} />
          <IconButton size="sm" label={t("Close graph")} onClick={onClose} icon={<X size={14} />} />
        </div>
        {!placed.length ? (
          <p className="p-6 text-sm text-[var(--muted-foreground)]">
            {graph ? t("No links yet. Link notes with [[ to see them here.") : t("Loading…")}
          </p>
        ) : (
          <svg
            aria-hidden="true"
            viewBox={`${view.x} ${view.y} ${view.w} ${view.h}`}
            className="h-full w-full touch-none select-none"
            onWheel={(event) => {
              const p = point(event, event.currentTarget);
              zoom(event.deltaY > 0 ? 1.1 : 0.9, p.x, p.y);
            }}
            onPointerDown={(event) => {
              drag.current = { x: event.clientX, y: event.clientY, moved: false };
            }}
            onPointerMove={(event) => {
              const d = drag.current;
              if (!d) return;
              const scale = unit;
              const moveX = (event.clientX - d.x) * scale;
              const moveY = (event.clientY - d.y) * scale;
              if (!d.moved) {
                if (Math.hypot(event.clientX - d.x, event.clientY - d.y) < 4) return;
                // Capture only once it is a drag: capturing on press would turn every node click into a click on the canvas.
                d.moved = true;
                event.currentTarget.setPointerCapture(event.pointerId);
              }
              d.x = event.clientX;
              d.y = event.clientY;
              setView((v) => ({ ...v, x: v.x - moveX, y: v.y - moveY }));
            }}
            onPointerUp={() => {
              window.setTimeout(() => (drag.current = null), 0);
            }}
          >
            {graph!.edges.map((e) => {
              const a = byId.get(e.source);
              const b = byId.get(e.target);
              if (!a || !b) return null;
              const lit = hover === e.source || hover === e.target || current === e.source || current === e.target;
              return (
                <line
                  key={`${e.source}>${e.target}`}
                  x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                  stroke={lit ? "var(--primary)" : "var(--border)"}
                  strokeWidth={lit ? 1.6 : 1}
                  vectorEffect="non-scaling-stroke"
                />
              );
            })}
            {placed.map((node) => {
              const isCurrent = node.id === current;
              const near = hover === node.id || (hover !== null && linked.get(hover)?.has(node.id));
              const showLabel = showAllLabels || isCurrent || near || node.links >= 4;
              return (
                <g
                  key={node.id}
                  className="cursor-pointer"
                  onPointerEnter={() => setHover(node.id)}
                  onPointerLeave={() => setHover(null)}
                  onClick={() => {
                    if (!drag.current?.moved) onOpen(node.id);
                  }}
                >
                  <circle
                    cx={node.x} cy={node.y} r={(6 + Math.min(node.links, 8)) * unit}
                    fill={isCurrent ? "var(--primary)" : node.read_only ? "var(--muted-foreground)" : "var(--foreground)"}
                    opacity={hover && !near && hover !== node.id ? 0.35 : 0.85}
                  />
                  {showLabel && (
                    <text x={node.x} y={node.y - (10 + Math.min(node.links, 8)) * unit} textAnchor="middle" fontSize={13 * unit} fill="var(--foreground)" paintOrder="stroke" stroke="var(--background)" strokeWidth={3 * unit}>
                      {node.id.split("/").pop()}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
        )}
      </div>
      <div className="max-h-[40vh] w-full shrink-0 overflow-y-auto p-3 md:max-h-none md:w-64">
        <h2 className="mb-2 text-sm font-medium text-[var(--muted-foreground)]">{t("Notes by connections")}</h2>
        <ul>
          {list.map((node) => (
            <li key={node.id}>
              <button
                type="button"
                onClick={() => onOpen(node.id)}
                aria-current={node.id === current ? "page" : undefined}
                className={`flex w-full items-baseline justify-between gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-[var(--muted)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] ${node.id === current ? "bg-[var(--muted)] font-medium" : ""}`}
              >
                <span className="truncate">{node.id}</span>
                <span className="shrink-0 text-xs tabular-nums text-[var(--muted-foreground)]">{t("{{count}} links", { count: node.links })}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
