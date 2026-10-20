// The composer's discussion mode, remembered per browser. "" means Auto (the tutor adapts to how you use it).
// Sent with every turn as `discussion_mode`.

export const DISCUSSION_MODES = ["", "think_first", "critic", "teach_back"] as const;
export type DiscussionMode = (typeof DISCUSSION_MODES)[number];

const KEY = "phrog.discussionMode";
const EVENT = "phrog-discussion-mode";

export function getModePref(): DiscussionMode {
  try {
    const value = globalThis.localStorage?.getItem(KEY) ?? "";
    return (DISCUSSION_MODES as readonly string[]).includes(value) ? (value as DiscussionMode) : "";
  } catch {
    return "";
  }
}

export function setModePref(value: DiscussionMode): void {
  try {
    if (value) globalThis.localStorage?.setItem(KEY, value);
    else globalThis.localStorage?.removeItem(KEY);
  } catch {
    // Storage blocked: the choice still applies until the page reloads.
  }
  globalThis.dispatchEvent?.(new CustomEvent(EVENT, { detail: value }));
}

export function onModePrefChange(listener: (value: DiscussionMode) => void): () => void {
  const handler = (event: Event) => listener((event as CustomEvent<DiscussionMode>).detail);
  globalThis.addEventListener?.(EVENT, handler);
  return () => globalThis.removeEventListener?.(EVENT, handler);
}
