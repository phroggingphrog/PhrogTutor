import { expect, it } from "vitest";
import { SIDEBAR_FRESH_MS, isFresh, type SidebarData } from "@/lib/sidebar-memory";

const data = (at: number): SidebarData => ({ sessions: [], courses: [], masteryTopics: [], readingCollections: [], at });

it("treats a list fetched moments ago as fresh and an old or missing one as stale", () => {
  const now = 1_000_000;
  expect(isFresh(undefined, now)).toBe(false);
  expect(isFresh(data(now - 1000), now)).toBe(true);
  expect(isFresh(data(now - SIDEBAR_FRESH_MS - 1), now)).toBe(false);
});
