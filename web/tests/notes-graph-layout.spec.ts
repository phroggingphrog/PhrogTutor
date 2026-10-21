import { expect, it } from "vitest";
import { layout } from "@/components/notes/NotesGraph";

const node = (id: string, links = 0) => ({ id, links, read_only: false });

it("places every note inside the canvas and keeps linked notes closer than unlinked ones", () => {
  const graph = {
    nodes: [node("a", 2), node("b", 2), node("c", 2), node("lonely")],
    edges: [
      { source: "a", target: "b" },
      { source: "b", target: "c" },
      { source: "c", target: "a" },
    ],
  };
  const placed = layout(graph);
  expect(placed).toHaveLength(4);
  for (const p of placed) {
    expect(p.x).toBeGreaterThanOrEqual(20);
    expect(p.x).toBeLessThanOrEqual(980);
    expect(p.y).toBeGreaterThanOrEqual(20);
    expect(p.y).toBeLessThanOrEqual(680);
  }
  const at = Object.fromEntries(placed.map((p) => [p.id, p]));
  const dist = (a: string, b: string) => Math.hypot(at[a].x - at[b].x, at[a].y - at[b].y);
  expect(dist("a", "b")).toBeLessThan(dist("a", "lonely"));
});

it("handles an empty graph and is deterministic", () => {
  expect(layout({ nodes: [], edges: [] })).toEqual([]);
  const g = { nodes: [node("x", 1), node("y", 1)], edges: [{ source: "x", target: "y" }] };
  expect(layout(g)).toEqual(layout(g));
});
