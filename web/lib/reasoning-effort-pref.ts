// PhrogVPS patch: the composer's thinking-effort choice, remembered per browser.
// "" means Auto (the model's default from Settings). Sent with every turn as `reasoning_effort`.

export const EFFORT_LEVELS = ["", "low", "medium", "high", "xhigh"] as const;
export type EffortLevel = (typeof EFFORT_LEVELS)[number];

const KEY = "phrog.reasoningEffort";
const EVENT = "phrog-reasoning-effort";

export function getEffortPref(): EffortLevel {
  try {
    const value = globalThis.localStorage?.getItem(KEY) ?? "";
    return (EFFORT_LEVELS as readonly string[]).includes(value) ? (value as EffortLevel) : "";
  } catch {
    return "";
  }
}

export function setEffortPref(value: EffortLevel): void {
  try {
    if (value) globalThis.localStorage?.setItem(KEY, value);
    else globalThis.localStorage?.removeItem(KEY);
  } catch {
    // Storage blocked: the choice still applies until the page reloads.
  }
  globalThis.dispatchEvent?.(new CustomEvent(EVENT, { detail: value }));
}

export function onEffortPrefChange(listener: (value: EffortLevel) => void): () => void {
  const handler = (event: Event) => listener((event as CustomEvent<EffortLevel>).detail);
  globalThis.addEventListener?.(EVENT, handler);
  return () => globalThis.removeEventListener?.(EVENT, handler);
}
