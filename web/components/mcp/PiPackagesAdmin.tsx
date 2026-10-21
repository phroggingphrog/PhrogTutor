"use client";

import { useCallback, useEffect, useState } from "react";
import { ChevronDown, Loader2, Puzzle, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  installPiPackage,
  listPiPackages,
  removePiPackage,
  type PiPackage,
} from "@/lib/pi-packages-api";

/**
 * Admin-only: pi packages (extensions, skills, prompts) for the tutor engine.
 * A package runs code on the host as the app user, so the warning is part of
 * the form, not a tooltip. New packages are live on the next turn.
 */
export default function PiPackagesAdmin() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [packages, setPackages] = useState<PiPackage[]>([]);
  const [source, setSource] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");

  const run = useCallback(
    async (key: string, action: () => Promise<PiPackage[]>) => {
      setBusy(key);
      setError("");
      try {
        setPackages(await action());
        return true;
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : t("Could not update the packages"));
        return false;
      } finally {
        setBusy(null);
      }
    },
    [t],
  );

  useEffect(() => {
    if (open) void run("load", listPiPackages);
  }, [open, run]);

  const install = async () => {
    if (await run("install", () => installPiPackage(source.trim()))) setSource("");
  };

  return (
    <section
      className={`overflow-hidden rounded-xl border transition-colors ${
        open
          ? "border-[var(--border)] bg-[var(--card)] shadow-sm"
          : "border-[var(--border)]/60 bg-[var(--card)]/40"
      }`}
    >
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition-colors hover:bg-[var(--muted)]/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
      >
        <span className="flex min-w-0 items-center gap-2 text-[13px] font-medium text-[var(--foreground)]">
          <Puzzle size={14} strokeWidth={1.7} className="shrink-0 text-[var(--muted-foreground)]" />
          {t("Tutor extensions")}
          <span className="rounded-full border border-[var(--border)] px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
            {t("Admin")}
          </span>
        </span>
        <ChevronDown
          size={14}
          className={`shrink-0 text-[var(--muted-foreground)] transition-transform duration-200 ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <div className="space-y-4 border-t border-[var(--border)] p-4">
          <p className="text-[12px] text-[var(--muted-foreground)]">
            {t("Install pi packages that add tools, skills or prompts to the tutor. A package runs code on this machine, so install only sources you trust. It is active from the next message.")}
          </p>
          <div className="flex gap-2">
            <label className="min-w-0 flex-1 text-[12px] font-medium">
              {t("Package source")}
              <input
                value={source}
                onChange={(event) => { setSource(event.target.value); setError(""); }}
                placeholder="npm:@scope/pi-package"
                className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-[13px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
              />
            </label>
            <button
              type="button"
              onClick={() => void install()}
              disabled={!source.trim() || busy !== null}
              className="mt-5 shrink-0 rounded-lg bg-[var(--primary)] px-3 py-2 text-[13px] font-medium text-[var(--primary-foreground)] disabled:opacity-50"
            >
              {busy === "install" && <Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" />}
              {t("Install")}
            </button>
          </div>
          {error && <p role="alert" className="whitespace-pre-wrap text-[12px] text-destructive">{error}</p>}
          {packages.length === 0 ? (
            <p className="text-[12px] text-[var(--muted-foreground)]">{busy === "load" ? t("Loading…") : t("No extensions installed.")}</p>
          ) : (
            <ul className="divide-y divide-[var(--border)] rounded-lg border border-[var(--border)]">
              {packages.map((pkg) => (
                <li key={pkg.source} className="flex items-center justify-between gap-3 px-3 py-2">
                  <span className="min-w-0 truncate font-mono text-[12px]">{pkg.source}</span>
                  <button
                    type="button"
                    aria-label={`${t("Remove")} ${pkg.source}`}
                    onClick={() => void run(pkg.source, () => removePiPackage(pkg.source))}
                    disabled={busy !== null}
                    className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[var(--muted-foreground)] hover:bg-[var(--muted)] hover:text-destructive disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
                  >
                    {busy === pkg.source ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
