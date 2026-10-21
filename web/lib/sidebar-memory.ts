import type { AppUpdateStatus } from "@/lib/app-update";
import type { StudyCourse } from "@/lib/courses-api";
import type { MasteryTopicLabel } from "@/lib/learning-api";
import type { ReadingCollectionLabel } from "@/lib/reading-workspace-api";
import type { SessionSummary } from "@/lib/session-api";
import type { ChatWorkspaceRegistration } from "@/lib/workspaces-api";
import type { SidebarNavLayout } from "@/lib/sidebar-layout";

/**
 * What the sidebar knew when it was last on screen.
 *
 * The app has two route groups, each with its own layout and so its own sidebar instance: moving
 * between a workspace page (Home, Task Board) and a utility page (Notes, Terminal) unmounts one
 * sidebar and mounts the other. A fresh instance used to start from the shipped defaults and an
 * empty list, then redraw itself — the "refresh" a reader sees on every such click. Seeding the new
 * instance from this module keeps it looking the same, and `isFresh` lets it skip the reload.
 *
 * Browser-only on purpose: it is written from effects, never during a server render, and it is
 * read only for the initial state of a component that is mounting on the client.
 */
export interface SidebarData {
  sessions: SessionSummary[];
  courses: StudyCourse[];
  masteryTopics: MasteryTopicLabel[];
  readingCollections: ReadingCollectionLabel[];
  /** When the lists were last fetched. Local edits do not move it. */
  at: number;
}

export interface SidebarMemory {
  data?: SidebarData;
  nav?: { layout: SidebarNavLayout | null; moreExpanded: boolean };
  sessionOrder?: string[];
  workspaces?: ChatWorkspaceRegistration[];
  update?: AppUpdateStatus;
  width?: number;
  recentsScroll: number;
}

export const sidebarMemory: SidebarMemory = { recentsScroll: 0 };

/** A list fetched this recently is shown without asking the server again. */
export const SIDEBAR_FRESH_MS = 20_000;

export function isFresh(data: SidebarData | undefined, now = Date.now()): boolean {
  return Boolean(data) && now - (data as SidebarData).at < SIDEBAR_FRESH_MS;
}
