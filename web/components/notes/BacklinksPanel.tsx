"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { apiFetch, apiUrl } from "@/lib/api";

type Backlink = { name: string; snippet: string };

/** Notes that link here with `[[wikilinks]]`. Re-read when the open note or `version` changes. */
export default function BacklinksPanel({
  note,
  version,
  onOpen,
}: {
  note: string;
  version: number;
  onOpen: (name: string) => void;
}) {
  const { t } = useTranslation();
  const [links, setLinks] = useState<Backlink[]>([]);

  useEffect(() => {
    let live = true;
    apiFetch(apiUrl(`/api/notes/_backlinks?name=${encodeURIComponent(note)}`))
      .then((r) => (r.ok ? r.json() : { backlinks: [] }))
      .then((body) => live && setLinks(body.backlinks ?? []))
      .catch(() => live && setLinks([]));
    return () => {
      live = false;
    };
  }, [note, version]);

  return (
    <section aria-label={t("Linked mentions")} className="border-t border-[var(--border)] px-8 py-4">
      <h2 className="text-sm font-medium text-[var(--muted-foreground)]">
        {t("Linked mentions")} <span className="tabular-nums">({links.length})</span>
      </h2>
      {links.length === 0 ? (
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">{t("No other note links here yet. Type [[ in a note to link it.")}</p>
      ) : (
        <ul className="mt-2 space-y-1">
          {links.map((link) => (
            <li key={link.name}>
              <button
                type="button"
                onClick={() => onOpen(link.name)}
                className="w-full rounded-lg px-2 py-1.5 text-left hover:bg-[var(--muted)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
              >
                <span className="block truncate text-sm font-medium">{link.name}</span>
                <span className="line-clamp-2 text-xs text-[var(--muted-foreground)]">{link.snippet}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
