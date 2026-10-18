"use client";

import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Trash2 } from "lucide-react";

import { apiFetch, apiUrl } from "@/lib/api";

type Rec = { id: number; ts: number; kind: string; quote: string };
type Concept = { concept: string; mastery: number; evidence: number; records: Rec[] };

const KIND: Record<string, string> = {
  explained_correctly: "explained it right",
  applied_correctly: "used it right",
  self_correction: "fixed own mistake",
  misconception: "misconception",
  hint_seeking: "got stuck",
  confusion: "confused",
};

// What the tutor learned from your chats and code runs. Delete a wrong record and the score updates.
export default function MasteryRecords() {
  const [concepts, setConcepts] = useState<Concept[]>([]);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(async () => {
    const r = await apiFetch(apiUrl("/api/mastery-records"));
    if (r.ok) setConcepts((await r.json()).concepts);
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const remove = async (id: number) => {
    await apiFetch(apiUrl(`/api/mastery-records/${id}`), { method: "DELETE" });
    load();
  };

  return (
    <div className="mx-auto max-w-3xl px-6 py-8 text-[var(--foreground)]">
      <h1 className="text-2xl font-semibold">What the tutor knows about you</h1>
      <p className="mt-1 text-sm text-[var(--muted-foreground)]">
        Learned from your chats and code runs. 0 = shaky, 1 = solid; older signals count less. Wrong? Delete the record.
      </p>
      <ul className="mt-6 divide-y divide-[var(--border)] rounded-lg border border-[var(--border)]">
        {concepts.map((c) => (
          <li key={c.concept}>
            <button
              onClick={() => setOpen(open === c.concept ? null : c.concept)}
              className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-[var(--muted)]"
            >
              {open === c.concept ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
              <span className="flex-1 font-medium">{c.concept}</span>
              <span className="h-2 w-28 overflow-hidden rounded bg-[var(--muted)]">
                <span
                  className={`block h-full ${c.mastery < 0.45 ? "bg-red-500" : c.mastery < 0.7 ? "bg-amber-500" : "bg-emerald-500"}`}
                  style={{ width: `${Math.round(c.mastery * 100)}%` }}
                />
              </span>
              <span className="w-10 text-right text-sm tabular-nums">{c.mastery.toFixed(2)}</span>
              <span className="w-20 text-right text-xs text-[var(--muted-foreground)]">{c.evidence} signals</span>
            </button>
            {open === c.concept && (
              <ul className="bg-[var(--muted)]/40 px-4 pb-3">
                {c.records.map((r) => (
                  <li key={r.id} className="flex items-start gap-3 py-2 text-sm">
                    <span className="w-24 shrink-0 text-xs text-[var(--muted-foreground)]">
                      {new Date(r.ts * 1000).toLocaleDateString()}
                    </span>
                    <span className="w-32 shrink-0 text-xs font-medium">{KIND[r.kind] ?? r.kind}</span>
                    <span className="flex-1 italic">“{r.quote}”</span>
                    <button onClick={() => remove(r.id)} title="Delete this record" className="rounded p-1 hover:bg-[var(--background)]">
                      <Trash2 size={14} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
        {!concepts.length && <li className="px-4 py-6 text-sm text-[var(--muted-foreground)]">Nothing learned yet — just chat.</li>}
      </ul>
    </div>
  );
}
