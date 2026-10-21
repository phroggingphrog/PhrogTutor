"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ExternalLink, ShieldAlert } from "lucide-react";
import { apiFetch, apiUrl } from "@/lib/api";

interface PlanSeed {
  plan_id: string;
  version: number;
  sha256: string;
  workspace_key: string;
  content: string;
}

// Owner-facing modes (design option A). Read-only is left out: this host has no read-only preset yet.
const MODES = [
  { id: "ask", name: "Ask", help: "DSH can change files in this workspace and asks before each step." },
  { id: "auto", name: "Auto", help: "DSH changes files without asking for each step." },
  { id: "bypass", name: "Bypass", help: "DSH has full access with no sandbox. Needs an extra confirm." },
] as const;
type ModeId = (typeof MODES)[number]["id"];

const STATUS_COPY: Record<string, string> = {
  pending: "Waiting for DSH to pick this up.",
  admitted: "DSH accepted the plan. It will start shortly.",
  running: "DSH is working. Open the session to watch it.",
  completed: "DSH finished. The report is in this conversation.",
  failed: "DSH stopped with an error. Open the session to see why.",
  blocked: "DSH is not reachable. Your approval is kept; nothing ran anywhere else.",
};
const FINAL = new Set(["completed", "failed"]);

/**
 * Approve card for a ```dsh-plan fence: owner-click approval with a permission mode, then the DSH
 * session link and live status. Approval is only ever a click here; the model cannot approve.
 */
export default function DshPlanCard({ raw, className = "" }: { raw: string; className?: string }) {
  let seed: PlanSeed | null = null;
  try {
    seed = JSON.parse(raw) as PlanSeed;
  } catch {
    seed = null;
  }
  const planId = seed?.plan_id ?? "";
  const [planState, setPlanState] = useState("proposed");
  const [latest, setLatest] = useState<number>(seed?.version ?? 1);
  const [mode, setMode] = useState<ModeId>("ask");
  const [approvedMode, setApprovedMode] = useState<string>("");
  const [approvalId, setApprovalId] = useState("");
  const [state, setState] = useState("");
  const [link, setLink] = useState("");
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState("");
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    if (!planId) return;
    const r = await apiFetch(apiUrl(`/api/dsh-plan/${planId}`));
    if (!r.ok) return;
    const p = await r.json();
    if (!p.plan_id) return;
    setLatest(p.version);
    setPlanState(p.version === seed?.version ? p.state : "superseded");
    if (p.approval_id && p.version === seed?.version) {
      setApprovalId(p.approval_id);
      setState(p.handoff_state || "");
      setLink(p.link || "");
      setReply(p.result || "");
    }
  }, [planId, seed?.version]);

  useEffect(() => {
    void load();
  }, [load]);

  // Poll live status until DSH finishes (the wake-up also posts the result into the chat).
  useEffect(() => {
    if (!approvalId || FINAL.has(state) || state === "blocked") return;
    timer.current = setInterval(async () => {
      try {
        const r = await apiFetch(apiUrl(`/api/dsh-plan/${planId}/handoff/${approvalId}`));
        if (!r.ok) return;
        const h = await r.json();
        if (h.state) setState(h.state);
        if (h.link) setLink(h.link);
        if (h.result) setReply(h.result);
      } catch {
        /* keep the last known status */
      }
    }, 5000);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [approvalId, state, planId]);

  const approve = async (confirmBypass: boolean) => {
    if (!seed) return;
    setBusy(true);
    setError("");
    try {
      const r = await apiFetch(apiUrl(`/api/dsh-plan/${planId}/approve`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ version: seed.version, sha256: seed.sha256, mode, confirm_bypass: confirmBypass }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "Approval failed");
      setApprovedMode(mode);
      setPlanState("approved");
      setApprovalId(d.approval_id);
      setState(d.state || "pending");
      setLink(d.link || "");
      if (d.error) setError(d.error);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setConfirming(false);
      setTyped("");
    }
  };

  const reject = async () => {
    if (!seed) return;
    setBusy(true);
    try {
      await apiFetch(apiUrl(`/api/dsh-plan/${planId}/reject?version=${seed.version}`), { method: "POST" });
      setPlanState("rejected");
    } finally {
      setBusy(false);
    }
  };

  if (!seed) return null;
  const proposed = planState === "proposed";
  const chip =
    planState === "proposed" ? "Proposed" : planState === "approved" ? "Approved" : planState === "rejected" ? "Rejected" : "Superseded";

  return (
    <div className={`my-3 ${planState === "proposed" ? "mb-28" : ""} rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 ${className}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-sm font-semibold text-[var(--foreground)]">{planId}</span>
        <span className="rounded-full border border-[var(--border)] px-2 py-0.5 text-xs">v{seed.version}</span>
        <span className="ml-auto rounded-full border border-[var(--border)] px-2.5 py-0.5 text-xs">{chip}</span>
      </div>
      <div className="mt-1 text-xs text-[var(--muted-foreground)]">
        Hash {seed.sha256.slice(0, 12)}… · Workspace <span className="font-mono">{seed.workspace_key}</span>
      </div>
      <div className="mt-3 whitespace-pre-wrap rounded-lg bg-[var(--muted)] px-3 py-2.5 text-sm text-[var(--foreground)]">{seed.content}</div>

      {planState === "superseded" && (
        <p className="mt-3 text-sm text-[var(--muted-foreground)]">
          A newer version (v{latest}) replaced this plan. This version can no longer be approved.
        </p>
      )}

      {proposed && (
        <>
          <fieldset className="mt-4">
            <legend className="mb-2 text-sm font-medium text-[var(--foreground)]">Permission mode</legend>
            <div className="space-y-1.5">
              {MODES.map((m) => (
                <label
                  key={m.id}
                  className={`flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2 ${
                    mode === m.id ? "border-[var(--foreground)]/60 bg-[var(--muted)]/50" : "border-transparent hover:bg-[var(--muted)]/30"
                  }`}
                >
                  <input type="radio" name={`mode-${planId}`} className="mt-1" checked={mode === m.id} onChange={() => setMode(m.id)} />
                  <span>
                    <span className="block text-sm font-medium">
                      {m.name}
                      {m.id === "ask" && <span className="ml-2 rounded-full border border-[var(--border)] px-1.5 text-[11px]">Default</span>}
                    </span>
                    <span className="block text-xs text-[var(--muted-foreground)]">{m.help}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <div className="mt-4 flex gap-2 border-t border-[var(--border)] pt-4">
            <button
              type="button"
              disabled={busy}
              onClick={() => (mode === "bypass" ? setConfirming(true) : approve(false))}
              className="rounded-lg bg-[var(--primary)] px-4 py-2 text-sm font-medium text-[var(--primary-foreground)] disabled:opacity-50"
            >
              Approve in {MODES.find((m) => m.id === mode)?.name} mode
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={reject}
              className="rounded-lg border border-[var(--border)] px-4 py-2 text-sm font-medium hover:bg-[var(--muted)]/40 disabled:opacity-50"
            >
              Reject
            </button>
          </div>
        </>
      )}

      {confirming && (
        <div role="alertdialog" aria-labelledby={`bp-${planId}`} className="mt-3 rounded-lg border border-[var(--border)] bg-[var(--muted)]/50 p-3">
          <div id={`bp-${planId}`} className="flex items-center gap-2 text-sm font-medium">
            <ShieldAlert size={16} /> Approve with bypass?
          </div>
          <p className="mt-1 text-xs text-[var(--muted-foreground)]">
            Bypass gives DSH full access with no sandbox. This approves {planId} v{seed.version} in the {seed.workspace_key} workspace. Type{" "}
            <code>bypass</code> to continue.
          </p>
          <div className="mt-2 flex gap-2">
            <input
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              className="w-40 rounded-md border border-[var(--border)] bg-[var(--background)] px-2 py-1 text-sm"
              aria-label="Type bypass to confirm"
            />
            <button
              type="button"
              disabled={typed.trim().toLowerCase() !== "bypass" || busy}
              onClick={() => approve(true)}
              className="rounded-lg bg-[var(--primary)] px-3 py-1.5 text-sm font-medium text-[var(--primary-foreground)] disabled:opacity-40"
            >
              Approve with bypass
            </button>
            <button type="button" onClick={() => setConfirming(false)} className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm">
              Cancel
            </button>
          </div>
        </div>
      )}

      {approvedMode && <p className="mt-3 text-xs text-[var(--muted-foreground)]">Approved in {approvedMode} mode.</p>}

      {state && (
        <section aria-label="DSH status" className="mt-3 rounded-lg border border-[var(--border)] p-3" role="status" aria-live="polite">
          <div className="text-sm font-medium capitalize">{state}</div>
          <p className="text-xs text-[var(--muted-foreground)]">{STATUS_COPY[state] ?? ""}</p>
          {link && (
            <a href={link} target="_blank" rel="noopener noreferrer" className="mt-2 inline-flex items-center gap-1 text-sm text-[var(--primary)] underline">
              Open the DSH session <ExternalLink size={13} />
            </a>
          )}
          {reply && (
            <div className="mt-2">
              <h4 className="text-sm font-medium text-[var(--foreground)]">Last reply</h4>
              <p className="whitespace-pre-wrap text-sm">{reply}</p>
            </div>
          )}
        </section>
      )}
      {error && <p className="mt-2 text-sm text-destructive">{error}</p>}
    </div>
  );
}
