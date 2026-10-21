"use client";

// PhrogVPS patch: thinking-effort picker in the chat composer, next to the model picker.

import { useEffect, useRef, useState } from "react";
import { Brain, Check, ChevronDown } from "lucide-react";
import { useOutsideClick } from "@/hooks/use-outside-click";
import Tooltip from "@/shared/ui/Tooltip";
import {
  EFFORT_LEVELS,
  type EffortLevel,
  getEffortPref,
  onEffortPrefChange,
  setEffortPref,
} from "@/lib/reasoning-effort-pref";

const LABELS: Record<EffortLevel, string> = {
  "": "Auto",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra high",
};

const HINTS: Record<EffortLevel, string> = {
  "": "Model default from Settings",
  low: "Fastest replies",
  medium: "Balanced",
  high: "Thinks harder",
  xhigh: "Deepest thinking, slowest",
};

export default function EffortSelector() {
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState<EffortLevel>("");

  useEffect(() => {
    setValue(getEffortPref());
    return onEffortPrefChange(setValue);
  }, []);
  useOutsideClick(rootRef, open, () => setOpen(false));

  return (
    <div ref={rootRef} className="relative">
      <Tooltip label={`Thinking effort: ${LABELS[value]}`} suppressed={open} side="top">
        <button
          type="button"
          onClick={() => setOpen((current) => !current)}
          aria-label={`Thinking effort: ${LABELS[value]}`}
          aria-expanded={open}
          className={`inline-flex h-8 shrink-0 items-center gap-1 rounded-lg px-2 text-[13px] font-medium transition-[background-color,color,transform] duration-150 active:scale-[0.97] ${
            open
              ? "bg-[var(--muted)] text-[var(--foreground)]"
              : value
                ? "text-[var(--primary)] hover:bg-[var(--muted)]/55"
                : "text-[var(--muted-foreground)] hover:bg-[var(--muted)]/55 hover:text-[var(--foreground)]"
          }`}
        >
          <Brain size={16} strokeWidth={1.7} className="shrink-0" />
          {/* Auto is the tutor deciding: icon only. A name shows only once the learner has set one. */}
          {value ? <span className="max-sm:hidden">{LABELS[value]}</span> : null}
          <ChevronDown size={13} className={`shrink-0 transition-transform ${open ? "rotate-180" : ""}`} />
        </button>
      </Tooltip>

      {open && (
        <div className="absolute right-0 bottom-full z-50 mb-1.5 w-[200px] overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--popover)] py-1 shadow-lg backdrop-blur-md">
          <div className="px-3 py-1 text-[11px] text-[var(--muted-foreground)]">Thinking effort</div>
          {EFFORT_LEVELS.map((level) => (
            <button
              key={level || "auto"}
              type="button"
              onClick={() => {
                setEffortPref(level);
                setOpen(false);
              }}
              className={`flex w-full items-center gap-2 px-3 py-1.5 text-left transition-colors ${
                value === level ? "bg-[var(--primary)]/[0.06]" : "hover:bg-[var(--muted)]/45"
              }`}
            >
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-medium text-[var(--foreground)]">{LABELS[level]}</span>
                <span className="block text-[11px] text-[var(--muted-foreground)]">{HINTS[level]}</span>
              </span>
              {value === level && <Check size={14} strokeWidth={2} className="shrink-0 text-[var(--primary)]" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
