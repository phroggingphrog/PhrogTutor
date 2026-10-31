"use client";

import { useEffect, useState } from "react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";

import { apiFetch, apiUrl } from "@/lib/api";
import Tooltip from "@/shared/ui/Tooltip";

import {
  getCodeBlockTheme,
  getCodeBlockThemeBackground,
} from "./code-block-themes";
import { useAppShell } from "../../context/AppShellContext";

const MONOSPACE =
  'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace';

const PLAIN_LANGS = new Set(["", "text", "txt", "plain", "plaintext", "none"]);
const DEFAULT_CODE_BLOCK_BACKGROUND = "#1f2937";
const DEFAULT_CODE_BLOCK_FOREGROUND = "#e5e7eb";

function getCodeBlockThemeForeground(
  style: Record<string, React.CSSProperties>,
): string | undefined {
  const codeStyle = style['code[class*="language-"]'];
  if (codeStyle && typeof codeStyle.color === "string") {
    return codeStyle.color;
  }

  const preStyle = style['pre[class*="language-"]'];
  if (preStyle && typeof preStyle.color === "string") {
    return preStyle.color;
  }

  return undefined;
}

const RUNNABLE = new Set(["python", "py", "python3", "r", "sql", "sqlite"]);

type RunResult = {
  stdout: string;
  stderr: string;
  exit_code: number;
  timed_out: boolean;
  error: string;
  images: string[];
};

export default function RichCodeBlock({
  raw,
  lang,
  className,
}: {
  raw: string;
  lang: string;
  className?: string;
}) {
  const learner = useAppShell();
  const { codeBlockTheme, codeBlockShowLineNumbers, codeBlockWrapLongLines } = learner;
  const normalizedLang = (lang || "").toLowerCase();
  const isPlain = PLAIN_LANGS.has(normalizedLang);
  const syntaxTheme = getCodeBlockTheme(codeBlockTheme);
  const backgroundColor =
    getCodeBlockThemeBackground(syntaxTheme) ?? DEFAULT_CODE_BLOCK_BACKGROUND;
  const textColor =
    getCodeBlockThemeForeground(syntaxTheme) ?? DEFAULT_CODE_BLOCK_FOREGROUND;
  const syntaxLanguage = isPlain ? "text" : normalizedLang;
  const runnable = RUNNABLE.has(normalizedLang);
  const [code, setCode] = useState(raw);
  const [editing, setEditing] = useState(false);
  const [running, setRunning] = useState(false);
  const [saved, setSaved] = useState("");
  const saveToDictionary = async () => {
    setSaved("Saving…");
    const r = await apiFetch(apiUrl("/api/dictionary"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, language: normalizedLang }),
    });
    setSaved(r.ok ? `Saved to ${(await r.json()).topic}` : "Save failed");
  };
  const [result, setResult] = useState<RunResult | null>(null);
  // Follow the streamed answer until the learner starts editing.
  useEffect(() => {
    if (!editing) setCode(raw);
  }, [raw, editing]);

  const run = async () => {
    setRunning(true);
    try {
      const r = await apiFetch(apiUrl("/api/run-code"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language: normalizedLang, code, original: raw }),
      });
      setResult(
        r.ok
          ? await r.json()
          : { stdout: "", stderr: "", exit_code: 1, timed_out: false, error: `HTTP ${r.status}`, images: [] },
      );
    } catch (e) {
      setResult({ stdout: "", stderr: "", exit_code: 1, timed_out: false, error: String(e), images: [] });
    } finally {
      setRunning(false);
    }
  };

  return (
    <div
      className={`md-code-block overflow-hidden rounded-xl border border-[var(--border)] ${
        className || ""
      }`}
      style={{ backgroundColor, color: textColor }}
    >
      {!isPlain ? (
        <div
          className="flex items-center justify-between border-b border-[var(--border)] px-3 py-2 text-[11px] font-medium uppercase tracking-wider"
          style={{ color: textColor }}
        >
          <span style={{ opacity: 0.8 }}>{normalizedLang}</span>
          <span className="flex gap-2 normal-case">
            {normalizedLang === "html" ? (
              <Tooltip label="Open this as a live, sandboxed page in the side panel" side="top">
                <button
                  type="button"
                  aria-label="Open in panel"
                  onClick={() => window.dispatchEvent(new CustomEvent("pi-tutor:open-html", { detail: { html: code } }))}
                  className="rounded bg-violet-600 px-2 py-0.5 text-[11px] font-semibold text-white hover:bg-violet-500"
                >
                  ▶ Open in panel
                </button>
              </Tooltip>
            ) : null}
            <Tooltip label="Save this command to your coding dictionary (wiki cheatsheets)" side="top">
              <button
                type="button"
                aria-label={saved ? "Saved to dictionary" : "Save to dictionary"}
                onClick={saveToDictionary}
                disabled={!!saved}
                className="rounded px-2 py-0.5 text-[11px] hover:bg-white/10 disabled:opacity-70"
              >
                {saved || "📖 Save to dictionary"}
              </button>
            </Tooltip>
          {runnable ? (
            <span className="flex gap-2 normal-case">
              <button
                type="button"
                onClick={() => setEditing((v) => !v)}
                className="rounded px-2 py-0.5 text-[11px] hover:bg-white/10"
              >
                {editing ? "Done" : "Edit"}
              </button>
              <button
                type="button"
                onClick={run}
                disabled={running}
                className="rounded bg-success px-2 py-0.5 text-[11px] font-semibold text-[var(--background)] hover:bg-success disabled:opacity-60"
              >
                {running ? "Running…" : "▶ Run"}
              </button>
            </span>
          ) : null}
          </span>
        </div>
      ) : null}
      {editing ? (
        <textarea
          value={code}
          onChange={(e) => setCode(e.target.value)}
          spellCheck={false}
          rows={Math.min(24, code.split("\n").length + 1)}
          className="block w-full resize-y bg-transparent p-4 text-sm outline-none"
          style={{ fontFamily: MONOSPACE, color: textColor, lineHeight: 1.7 }}
        />
      ) : (
      <SyntaxHighlighter
        language={syntaxLanguage}
        style={syntaxTheme}
        PreTag="pre"
        customStyle={{
          margin: 0,
          borderRadius: 0,
          background: backgroundColor,
          color: textColor,
          padding: "1rem",
          fontSize: "0.875rem",
          lineHeight: "1.7",
          overflowX: codeBlockWrapLongLines ? "hidden" : "auto",
          whiteSpace: codeBlockWrapLongLines ? "pre-wrap" : "pre",
          wordWrap: codeBlockWrapLongLines ? "break-word" : "normal",
        }}
        codeTagProps={{
          className: "md-code-block__code",
          style: {
            fontFamily: MONOSPACE,
            ...(codeBlockWrapLongLines ? { wordWrap: "break-word" } : {}),
          },
        }}
        showLineNumbers={codeBlockShowLineNumbers}
        wrapLongLines={codeBlockWrapLongLines}
        // react-syntax-highlighter's highlight.js forces `display:flex` onto
        // each per-line span when BOTH wrapLongLines and showLineNumbers are
        // on (so the line-number gutter can align in a column). That flex
        // layout makes each token span a flex item with the default
        // `min-width:auto`, which cannot shrink below content size — so a
        // long unbreakable token overflows the line wrapper and defeats
        // `overflow-wrap:break-word` set on <pre>/<code>. Overriding the
        // per-line wrapper back to `display:block` lets the tokens flow as
        // normal inline content, where `overflow-wrap:break-word` can
        // actually break long tokens.
        lineProps={
          codeBlockWrapLongLines && codeBlockShowLineNumbers
            ? { style: { display: "block" } }
            : undefined
        }
      >
        {code}
      </SyntaxHighlighter>
      )}
      {result ? (
        <div className="border-t border-[var(--border)] bg-black/30 p-3 text-xs" style={{ fontFamily: MONOSPACE }}>
          {result.stdout ? <pre className="whitespace-pre-wrap">{result.stdout}</pre> : null}
          {result.stderr ? <pre className="whitespace-pre-wrap text-destructive">{result.stderr}</pre> : null}
          {result.error ? <pre className="whitespace-pre-wrap text-destructive">{result.error}</pre> : null}
          {result.timed_out ? <pre className="text-warning">Timed out.</pre> : null}
          {result.images.map((src, i) => (
            // eslint-disable-next-line @next/next/no-img-element
            <img key={i} src={src} alt={`plot ${i + 1}`} className="mt-2 max-w-full rounded bg-white" />
          ))}
          {!result.stdout && !result.stderr && !result.error && !result.images.length ? (
            <span className="opacity-60">(no output)</span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
