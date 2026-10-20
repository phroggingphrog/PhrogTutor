"use client";

import { useCallback, useEffect, useState } from "react";
import { Trash2 } from "lucide-react";

import { apiFetch, apiUrl } from "@/lib/api";

type StyleRec = { id: number; ts: number; style: string; quote: string };
type StyleProfile = { signals: number; shares: Record<string, number>; dominant: string | null; records: StyleRec[] };

const STYLE: Record<string, string> = {
  tried_first: "try it first",
  asked_for_answer: "ask for the answer",
  pushed_back: "question the answer",
  accepted: "accept and move on",
  own_idea: "bring your own idea",
};

// How you use the tutor (not what you know). The tutor adapts to it; delete any record that is wrong.
export default function UsageStylePanel() {
  const [style, setStyle] = useState<StyleProfile | null>(null);
  const load = useCallback(async () => {
    const r = await apiFetch(apiUrl("/api/mastery-records/style"));
    if (r.ok) setStyle(await r.json());
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  return (
    <section className="mt-10 border-t border-[var(--border)] pt-6">
      <h2 className="text-base font-semibold">How the tutor adapts to you</h2>
      <p className="mt-1 text-sm text-[var(--muted-foreground)]">
        Learned from how you use it, not from what you know. Pick a discussion mode in the chat box to override it.
      </p>
      {!style || style.signals === 0 ? (
        <p className="mt-3 text-sm text-[var(--muted-foreground)]">Nothing learned yet.</p>
      ) : (
        <>
          <div className="mt-3 flex flex-wrap gap-2 text-sm">
            {Object.entries(style.shares).map(([k, v]) => (
              <span key={k} className="rounded-full bg-[var(--muted)] px-3 py-1">
                {STYLE[k] ?? k} {Math.round(v * 100)}%
              </span>
            ))}
          </div>
          <p className="mt-2 text-xs text-[var(--muted-foreground)]">
            {style.dominant
              ? `You mostly ${STYLE[style.dominant]}, so the tutor adapts to that.`
              : `Seen in ${style.signals} messages; the tutor adapts after 8 clear signals.`}
          </p>
          <details className="mt-2 text-sm">
            <summary className="cursor-pointer text-[var(--muted-foreground)]">Records</summary>
            <ul className="mt-2 space-y-1">
              {style.records.map((r) => (
                <li key={r.id} className="flex items-center gap-2">
                  <span className="shrink-0 text-xs text-[var(--muted-foreground)]">{STYLE[r.style] ?? r.style}</span>
                  <span className="flex-1 truncate">{r.quote}</span>
                  <button
                    aria-label="Delete record"
                    className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
                    onClick={async () => {
                      await apiFetch(apiUrl(`/api/mastery-records/style/${r.id}`), { method: "DELETE" });
                      load();
                    }}
                  >
                    <Trash2 size={14} />
                  </button>
                </li>
              ))}
            </ul>
          </details>
        </>
      )}
    </section>
  );
}
