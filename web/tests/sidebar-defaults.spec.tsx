import { beforeEach, expect, it } from "vitest";
import {
  readNavLayout,
  writeNavLayout,
  resolveNavLayout,
} from "@/lib/sidebar-layout";
import { NAV_LAYOUT_MIGRATION_KEY, NAV_LAYOUT_STORAGE_KEY } from "@/lib/sidebar-layout";
import {
  PRIMARY_NAV_HREFS,
  DEFAULT_COLLAPSED_NAV,
  SECONDARY_NAV,
} from "@/components/sidebar/nav-entries";

beforeEach(() => localStorage.clear());

it("new profiles see Home, Learning, Notes and the Task Board; the rest is under More", () => {
  expect(readNavLayout()).toBeNull();
  const resolved = resolveNavLayout(
    PRIMARY_NAV_HREFS,
    readNavLayout(),
    DEFAULT_COLLAPSED_NAV
  );
  expect(resolved.visible).toEqual([
    "/chat",
    "/learning",
    "/notes",
    "/kanban",
  ]);
  expect(resolved.collapsed).toEqual(["/partners", "/space", "/terminal", "/co-writer", "/agents"]);
  expect(resolved.customized).toBe(false);
  expect(SECONDARY_NAV.map(item => item.href)).toEqual(["/settings"]);
});

it("storage round trips an intentionally expanded layout without reapplying defaults", () => {
  writeNavLayout({ order: PRIMARY_NAV_HREFS, collapsed: [] });
  expect(readNavLayout()).toEqual({ order: PRIMARY_NAV_HREFS, collapsed: [] });
  expect(
    resolveNavLayout(PRIMARY_NAV_HREFS, readNavLayout(), DEFAULT_COLLAPSED_NAV)
      .collapsed
  ).toEqual([]);
});

it("takes the Task Board out of More once for a sidebar arranged before it became a main entry", () => {
  localStorage.setItem(
    NAV_LAYOUT_STORAGE_KEY,
    JSON.stringify({ order: PRIMARY_NAV_HREFS, collapsed: ["/partners", "/kanban", "/terminal"] }),
  );
  expect(readNavLayout()?.collapsed).toEqual(["/partners", "/terminal"]);
  expect(localStorage.getItem(NAV_LAYOUT_MIGRATION_KEY)).toContain("1");

  // After the one-time fix, folding it again is the reader's own choice and sticks.
  writeNavLayout({ order: PRIMARY_NAV_HREFS, collapsed: ["/kanban"] });
  expect(readNavLayout()?.collapsed).toEqual(["/kanban"]);
});

it("records the migration even when nothing was arranged, so a later fold is not undone", () => {
  expect(readNavLayout()).toBeNull();
  writeNavLayout({ order: PRIMARY_NAV_HREFS, collapsed: ["/kanban"] });
  expect(readNavLayout()?.collapsed).toEqual(["/kanban"]);
});
