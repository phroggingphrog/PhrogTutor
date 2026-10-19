"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { apiFetch, apiUrl } from "@/lib/api";

// What the tutor has learned about you, read back by the AI in a few plain lines. The raw records live on /what-i-know.
export default function MindAtlasDigest() {
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    apiFetch(apiUrl("/api/mastery-records/digest"))
      .then(async (r) => setText(r.ok ? (await r.json()).digest : ""))
      .catch(() => setText(""));
  }, []);
  return (
    <section className="mb-9 rounded-xl border border-[var(--border)] p-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-[13.5px] font-semibold">Mind Atlas — where you stand</h2>
        <Link href="/what-i-know" className="text-xs text-[var(--muted-foreground)] hover:underline">
          Study list and evidence →
        </Link>
      </div>
      {text === null ? (
        <p className="text-sm text-[var(--muted-foreground)]">Reading your progress…</p>
      ) : text ? (
        <div className="space-y-1 text-sm leading-6">{text.split("\n").filter(Boolean).map((l, i) => <p key={i}>{l}</p>)}</div>
      ) : (
        <p className="text-sm text-[var(--muted-foreground)]">Nothing learned yet — just chat with the tutor.</p>
      )}
    </section>
  );
}
