"use client";

import { useEffect, useRef, useState } from "react";
import CodeBlock from "@tiptap/extension-code-block";
import {
  NodeViewContent,
  NodeViewWrapper,
  ReactNodeViewRenderer,
  type NodeViewProps,
} from "@tiptap/react";
import { Play } from "lucide-react";

import { apiFetch, apiUrl } from "@/lib/api";

const RUNNABLE = new Set(["python", "py", "python3", "r", "sql", "sqlite"]);
const KERNEL = new Set(["python", "py", "python3", "r"]); // these share one session per note, like Jupyter

// The open note (set by NotesEditor) and every cell's run function, so "Run all" can run them in order.
export const notebook = { note: "" };
export const cellRunners = new Map<HTMLElement, () => Promise<void>>();

type RunResult = {
  stdout: string;
  stderr: string;
  error?: string;
  images?: string[];
};

function CodeBlockView({ node, updateAttributes }: NodeViewProps) {
  const lang = (node.attrs.language || "").toLowerCase();
  const [result, setResult] = useState<RunResult | null>(null);
  const [running, setRunning] = useState(false);
  const wrapper = useRef<HTMLDivElement>(null);

  const run = async () => {
    setRunning(true);
    try {
      const code = node.textContent;
      const r = KERNEL.has(lang)
        ? await apiFetch(apiUrl("/api/notebook-kernel/cell"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ note: notebook.note, language: lang, code }),
          })
        : await apiFetch(apiUrl("/api/run-code"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ language: lang, code, original: code }),
          });
      setResult(
        r.ok
          ? await r.json()
          : { stdout: "", stderr: "", error: `HTTP ${r.status}` },
      );
    } catch (e) {
      setResult({ stdout: "", stderr: "", error: String(e) });
    } finally {
      setRunning(false);
    }
  };

  useEffect(() => {
    const el = wrapper.current;
    if (!el || !RUNNABLE.has(lang)) return;
    cellRunners.set(el, run);
    const clear = () => setResult(null);
    window.addEventListener("notebook-restart", clear);
    return () => {
      cellRunners.delete(el);
      window.removeEventListener("notebook-restart", clear);
    };
  });

  return (
    <NodeViewWrapper ref={wrapper} className="my-3 overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--muted)]">
      <div
        contentEditable={false}
        className="flex items-center gap-2 border-b border-[var(--border)] px-3 py-1 text-xs text-[var(--muted-foreground)]"
      >
        <input
          value={node.attrs.language || ""}
          onChange={(e) => updateAttributes({ language: e.target.value })}
          placeholder="language"
          className="w-24 bg-transparent outline-none"
        />
        {RUNNABLE.has(lang) && (
          <button
            type="button"
            onClick={run}
            disabled={running}
            className="ml-auto inline-flex items-center gap-1 rounded px-2 py-0.5 hover:bg-[var(--background)]"
          >
            <Play size={12} /> {running ? "Running…" : "Run"}
          </button>
        )}
      </div>
      <pre className="m-0 overflow-x-auto p-3 font-mono text-sm">
        <NodeViewContent as={"code" as "div"} />
      </pre>
      {result && (
        <div
          contentEditable={false}
          className="border-t border-[var(--border)] p-3 font-mono text-xs"
        >
          {result.stdout && <pre className="whitespace-pre-wrap">{result.stdout}</pre>}
          {(result.stderr || result.error) && (
            <pre className="whitespace-pre-wrap text-red-500">
              {result.stderr || result.error}
            </pre>
          )}
          {result.images?.map((src, i) => (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              key={i}
              alt="plot"
              className="mt-2 max-w-full"
              src={src.startsWith("data:") ? src : `data:image/png;base64,${src}`}
            />
          ))}
        </div>
      )}
    </NodeViewWrapper>
  );
}

export const RunnableCodeBlock = CodeBlock.extend({
  addNodeView() {
    return ReactNodeViewRenderer(CodeBlockView);
  },
});
