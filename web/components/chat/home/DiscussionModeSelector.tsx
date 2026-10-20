"use client";

// Discussion mode picker in the chat composer: how the tutor behaves as a thinking partner.

import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, MessagesSquare } from "lucide-react";
import { useOutsideClick } from "@/hooks/use-outside-click";
import Tooltip from "@/shared/ui/Tooltip";
import {
  DISCUSSION_MODES,
  type DiscussionMode,
  getModePref,
  onModePrefChange,
  setModePref,
} from "@/lib/discussion-mode-pref";

const LABELS: Record<DiscussionMode, string> = {
  "": "Auto",
  think_first: "Think-first",
  critic: "Critic",
  teach_back: "Teach-back",
};

const HINTS: Record<DiscussionMode, string> = {
  "": "Adapts to how you use it",
  think_first: "You try first, then it helps",
  critic: "It argues the other side",
  teach_back: "You explain, it plays a novice",
};

export default function DiscussionModeSelector() {
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState<DiscussionMode>("");

  useEffect(() => {
    setValue(getModePref());
    return onModePrefChange(setValue);
  }, []);
  useOutsideClick(rootRef, open, () => setOpen(false));

  return (
    <div ref={rootRef} className="relative">
      <Tooltip label={`Discussion mode: ${LABELS[value]}`} suppressed={open} side="top">
        <button
          type="button"
          onClick={() => setOpen((current) => !current)}
          aria-label="Discussion mode"
          aria-expanded={open}
          className={`inline-flex h-8 shrink-0 items-center gap-1 rounded-lg px-2 text-[13px] font-medium transition-[background-color,color,transform] duration-150 active:scale-[0.97] ${
            open
              ? "bg-[var(--muted)] text-[var(--foreground)]"
              : value
                ? "text-[var(--primary)] hover:bg-[var(--muted)]/55"
                : "text-[var(--muted-foreground)] hover:bg-[var(--muted)]/55 hover:text-[var(--foreground)]"
          }`}
        >
          <MessagesSquare size={16} strokeWidth={1.7} className="shrink-0" />
          <span className="max-sm:hidden">{LABELS[value]}</span>
          <ChevronDown size={13} className={`shrink-0 transition-transform ${open ? "rotate-180" : ""}`} />
        </button>
      </Tooltip>

      {open && (
        <div className="absolute right-0 bottom-full z-50 mb-1.5 w-[230px] overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--popover)] py-1 shadow-lg backdrop-blur-md">
          <div className="px-3 py-1 text-[11px] text-[var(--muted-foreground)]">Discussion mode</div>
          {DISCUSSION_MODES.map((mode) => (
            <button
              key={mode || "auto"}
              type="button"
              onClick={() => {
                setModePref(mode);
                setOpen(false);
              }}
              className={`flex w-full items-center gap-2 px-3 py-1.5 text-left transition-colors ${
                value === mode ? "bg-[var(--primary)]/[0.06]" : "hover:bg-[var(--muted)]/45"
              }`}
            >
              <span className="min-w-0 flex-1">
                <span className="block text-[12.5px] font-medium text-[var(--foreground)]">{LABELS[mode]}</span>
                <span className="block text-[11px] text-[var(--muted-foreground)]">{HINTS[mode]}</span>
              </span>
              {value === mode && <Check size={14} strokeWidth={2} className="shrink-0 text-[var(--primary)]" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
