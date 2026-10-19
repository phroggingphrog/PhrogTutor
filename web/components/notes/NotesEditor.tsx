"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { Markdown } from "@tiptap/markdown";
import Placeholder from "@tiptap/extension-placeholder";
import TaskList from "@tiptap/extension-task-list";
import TaskItem from "@tiptap/extension-task-item";
import Mathematics from "@tiptap/extension-mathematics";
import "katex/dist/katex.min.css";
import { ChevronDown, ChevronRight, FilePlus, Folder, MessageCircleQuestion, SquareTerminal, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import dynamic from "next/dynamic";

const TerminalPanel = dynamic(() => import("./TerminalPanel"), { ssr: false });

import { apiFetch, apiUrl } from "@/lib/api";
import { RunnableCodeBlock, cellRunners, notebook } from "./RunnableCodeBlock";

type NoteMeta = { name: string; updated_at: number; read_only?: boolean };

const api = (path = "") => apiUrl(`/api/notes${path}`);
const json = { "Content-Type": "application/json" };
const encPath = (name: string) => name.split("/").map(encodeURIComponent).join("/");

const BUTTONS: [string, string, (e: any) => void, (e: any) => boolean][] = [
  ["H1", "Heading 1", (e) => e.chain().focus().toggleHeading({ level: 1 }).run(), (e) => e.isActive("heading", { level: 1 })],
  ["H2", "Heading 2", (e) => e.chain().focus().toggleHeading({ level: 2 }).run(), (e) => e.isActive("heading", { level: 2 })],
  ["B", "Bold", (e) => e.chain().focus().toggleBold().run(), (e) => e.isActive("bold")],
  ["I", "Italic", (e) => e.chain().focus().toggleItalic().run(), (e) => e.isActive("italic")],
  ["•", "Bullet list", (e) => e.chain().focus().toggleBulletList().run(), (e) => e.isActive("bulletList")],
  ["1.", "Numbered list", (e) => e.chain().focus().toggleOrderedList().run(), (e) => e.isActive("orderedList")],
  ["☑", "Task list", (e) => e.chain().focus().toggleTaskList().run(), (e) => e.isActive("taskList")],
  ["∑", "Maths (LaTeX)", (e) => e.chain().focus().insertInlineMath({ latex: "x^2" }).run(), (e) => e.isActive("inlineMath")],
  ["</>", "Code block", (e) => e.chain().focus().toggleCodeBlock({ language: "python" }).run(), (e) => e.isActive("codeBlock")],
];

// compact: the chat side panel's view (note list folds away behind a button, toolbar wraps).
export default function NotesEditor({ compact = false }: { compact?: boolean }) {
  const [showList, setShowList] = useState(!compact);
  const [notes, setNotes] = useState<NoteMeta[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [readOnly, setReadOnly] = useState(false);
  const [showTerminal, setShowTerminal] = useState(false);
  const [query, setQuery] = useState("");
  const router = useRouter();

  // Open chat with this note in the message box; the learner adds their question.
  const askTutor = () => {
    if (!editor || !current) return;
    const { from, to } = editor.state.selection;
    const quote = from === to ? "" : editor.state.doc.textBetween(from, to, "\n");
    const text = quote
      ? `In my note "${current}" I highlighted:\n> ${quote.slice(0, 2000).replace(/\n/g, "\n> ")}\n\nMy question: `
      : `About my note "${current}":\n\n${editor.getMarkdown().slice(0, 8000)}\n\nMy question: `;
    if (compact) {  // already beside a chat: annotate into it, no new session
      window.dispatchEvent(new CustomEvent("pi-tutor:prefill-composer", { detail: { text } }));
      return;
    }
    try {
      sessionStorage.setItem("pi-tutor:ask-note", text);
      sessionStorage.setItem("pi-tutor:open-note", current);
    } catch {}
    router.push("/");
  };
  const [menu, setMenu] = useState(false);
  const [showFormat, setShowFormat] = useState(false);
  // Co-Writer inside Notes: rewrite the selection (or the whole note) by an instruction.
  const aiEdit = async () => {
    if (!editor || readOnly) return;
    const { from, to } = editor.state.selection;
    const whole = from === to;
    const text = whole ? editor.getMarkdown() : editor.state.doc.textBetween(from, to, "\n");
    const instruction = window.prompt(whole ? "AI edit the whole note — what should change?" : "AI edit the selection — what should change?");
    if (!instruction) return;
    setStatus("AI editing…");
    const r = await apiFetch(apiUrl("/api/documents/actions/edit"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text.slice(0, 50000), instruction, action: "rewrite" }),
    });
    if (!r.ok) return setStatus("AI edit failed");
    const edited = (await r.json()).edited_text as string;
    if (whole) editor.commands.setContent(edited, { contentType: "markdown" });
    else editor.chain().focus().insertContentAt({ from, to }, edited, { contentType: "markdown" }).run();
    setStatus("AI edited — Ctrl+Z to undo");
  };
  const [hits, setHits] = useState<{ name: string; snippet: string }[] | null>(null);

  // Typing filters by name; Enter also searches inside every note's text (own notes + extra sources).
  const searchText = async () => {
    if (!query.trim()) return setHits(null);
    const r = await apiFetch(api(`/search?q=${encodeURIComponent(query)}`));
    setHits(r.ok ? (await r.json()).hits : []);
  };
  const [openFolders, setOpenFolders] = useState<Set<string>>(new Set());
  const openRef = useRef<((name: string) => Promise<void>) | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const loading = useRef(false);
  const currentRef = useRef<string | null>(null);
  currentRef.current = current;

  const refresh = useCallback(async () => {
    const r = await apiFetch(api());
    if (r.ok) setNotes((await r.json()).notes);
  }, []);

  const save = useCallback(async (name: string, md: string) => {
    setStatus("Saving…");
    const r = await apiFetch(api(`/${encPath(name)}`), {
      method: "PUT",
      headers: json,
      body: JSON.stringify({ content: md }),
    });
    setStatus(r.ok ? "Saved" : "Save failed");
  }, []);

  const editor = useEditor({
    immediatelyRender: false,
    extensions: [
      StarterKit.configure({ codeBlock: false }),
      RunnableCodeBlock,
      TaskList,
      TaskItem.configure({ nested: true }),
      Placeholder.configure({ placeholder: "Write something…" }),
      Mathematics,
      Markdown,
    ],
    editorProps: {
      attributes: { class: "notes-prose min-h-[60vh] px-8 py-6 outline-none" },
    },
    onUpdate: ({ editor }) => {
      const name = currentRef.current;
      if (loading.current || !name) return;
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => save(name, tidyMarkdown(editor.getMarkdown())).then(refresh), 800);
    },
  });

  useEffect(() => {
    refresh();
  }, [refresh]);
  // Opened from a full-page "Ask": show that note here beside the chat.
  useEffect(() => {
    if (!compact || !editor) return;
    let name: string | null = null;
    try {
      name = sessionStorage.getItem("pi-tutor:open-note");
      sessionStorage.removeItem("pi-tutor:open-note");
    } catch {}
    if (name) void openRef.current?.(name);
  }, [compact, editor]);

  const open = useCallback(
    async (name: string) => {
      if (!editor) return;
      const r = await apiFetch(api(`/${encPath(name)}`));
      if (!r.ok) return;
      const { content, read_only } = await r.json();
      loading.current = true;
      editor.commands.setContent(content, { contentType: "markdown" });
      editor.setEditable(!read_only);
      loading.current = false;
      setReadOnly(Boolean(read_only));
      setCurrent(name);
      setStatus("");
    },
    [editor],
  );
  openRef.current = open;

  const create = async () => {
    // "folder/name" puts the note in a folder (created if needed).
    const typed = prompt("New note name (use folder/name for a folder)", "Untitled")?.trim().replace(/^\/+|\/+$/g, "");
    if (!typed || typed.startsWith("@")) return;
    let n = typed;
    for (let i = 2; notes.some((x) => x.name === n); i++) n = `${typed} ${i}`;
    await save(n, "");
    await refresh();
    await open(n);
  };

  const remove = async () => {
    if (!current || !confirm(`Delete "${current}"? (moved to .trash)`)) return;
    await apiFetch(api(`/${encPath(current)}`), { method: "DELETE" });
    setCurrent(null);
    editor?.commands.clearContent();
    refresh();
  };

  const rename = async (newName: string) => {
    const name = newName.trim();
    if (!current || !name || name === current) return;
    const r = await apiFetch(api(`/${encPath(current)}/rename`), {
      method: "POST",
      headers: json,
      body: JSON.stringify({ new_name: name }),
    });
    if (r.ok) {
      setCurrent(name);
      refresh();
    } else alert("Could not rename");
  };

  notebook.note = current ?? "";

  // Jupyter-style: run every code cell top to bottom in the note's shared session.
  const runAll = async () => {
    const cells = [...cellRunners.entries()].sort(([a], [b]) =>
      a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1,
    );
    for (const [, run] of cells) await run(true);
  };

  // Forget all variables: stop this note's Python and R sessions and clear outputs.
  const restartKernels = async () => {
    for (const language of ["python", "r"])
      await apiFetch(apiUrl("/api/notebook-kernel/restart"), {
        method: "POST",
        headers: json,
        body: JSON.stringify({ note: current, language }),
      });
    window.dispatchEvent(new Event("notebook-restart"));
  };

  return (
    <div className={`relative flex ${compact ? "h-full" : "h-[calc(100vh-4rem)]"} text-[var(--foreground)]`}>
      {showList && (
      <aside className={`flex w-64 shrink-0 flex-col border-r border-[var(--border)] ${compact ? "absolute inset-y-0 left-0 z-10 bg-[var(--background)] shadow-lg" : ""}`}>
        <div className="flex items-center justify-between p-3">
          <span className="font-medium">Notes</span>
          <span className="flex gap-1">
            <button
              onClick={() => setShowTerminal((v) => !v)}
              title="Terminal (sandboxed, no internet)"
              className={`rounded p-1 hover:bg-[var(--muted)] ${showTerminal ? "bg-[var(--muted)]" : ""}`}
            >
              <SquareTerminal size={16} />
            </button>
            <button onClick={create} title="New note" className="rounded p-1 hover:bg-[var(--muted)]">
              <FilePlus size={16} />
            </button>
          </span>
        </div>
        <input
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setHits(null);
          }}
          onKeyDown={(e) => e.key === "Enter" && searchText()}
          placeholder="Search notes (Enter = search text)"
          className="mx-3 mb-2 rounded border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none"
        />
        {query.trim() ? (
          <ul className="flex-1 overflow-y-auto pb-2">
            {[
              ...notes
                .filter((n) => n.name.toLowerCase().includes(query.trim().toLowerCase()))
                .slice(0, 50)
                .map((n) => ({ name: n.name, snippet: "" })),
              ...(hits ?? []),
            ].map((h, i) => (
              <li key={`${h.name}:${i}`}>
                <button
                  onClick={() => { open(h.name); if (compact) setShowList(false); }}
                  className={`w-full px-3 py-1.5 text-left text-sm hover:bg-[var(--muted)] ${h.name === current ? "bg-[var(--muted)]" : ""}`}
                >
                  <div className="truncate font-medium">{h.name}</div>
                  {h.snippet && <div className="line-clamp-2 text-xs text-[var(--muted-foreground)]">{h.snippet}</div>}
                </button>
              </li>
            ))}
            {hits && !hits.length && <li className="px-3 text-sm text-[var(--muted-foreground)]">No text matches.</li>}
          </ul>
        ) : (
        <ul className="flex-1 overflow-y-auto pb-2">
          {treeRows(notes, openFolders).map((row) =>
            row.kind === "folder" ? (
              <li key={`f:${row.path}`}>
                <button
                  onClick={() =>
                    setOpenFolders((prev) => {
                      const next = new Set(prev);
                      if (next.has(row.path)) next.delete(row.path);
                      else next.add(row.path);
                      return next;
                    })
                  }
                  style={{ paddingLeft: 12 + row.depth * 14 }}
                  className="flex w-full items-center gap-1 truncate py-1 pr-3 text-left text-sm text-[var(--muted-foreground)] hover:bg-[var(--muted)]"
                >
                  {openFolders.has(row.path) ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  <Folder size={14} /> {row.label}
                </button>
              </li>
            ) : (
              <li key={row.path}>
                <button
                  onClick={() => { open(row.path); if (compact) setShowList(false); }}
                  style={{ paddingLeft: 30 + row.depth * 14 }}
                  className={`w-full truncate py-1.5 pr-3 text-left text-sm hover:bg-[var(--muted)] ${row.path === current ? "bg-[var(--muted)] font-medium" : ""}`}
                >
                  {row.label}
                </button>
              </li>
            ),
          )}
          {!notes.length && <li className="px-3 text-sm text-[var(--muted-foreground)]">No notes yet.</li>}
        </ul>
        )}
        <Link href="/notebooks" className="border-t border-[var(--border)] p-3 text-sm text-[var(--muted-foreground)] hover:underline">
          Saved from chat →
        </Link>
      </aside>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
      <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto">
        {current ? (
          <>
            <div className="flex flex-wrap items-center gap-2 whitespace-nowrap border-b border-[var(--border)] px-4 py-2">
              {compact && (
                <button onClick={() => setShowList((v) => !v)} title="All notes" className="rounded px-2 py-0.5 text-sm hover:bg-[var(--muted)]">
                  ☰
                </button>
              )}
              <input
                key={current}
                readOnly={readOnly}
                defaultValue={current}
                onBlur={(e) => rename(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
                className="min-w-0 flex-1 bg-transparent text-lg font-semibold outline-none"
              />
              <span className="text-xs text-[var(--muted-foreground)]">{status}</span>
              <button onClick={askTutor} title="Ask the tutor about the selection (or the whole note)" className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-sm hover:bg-[var(--muted)]">
                <MessageCircleQuestion size={14} /> Ask
              </button>
              {!readOnly && (
                <button onClick={aiEdit} title="AI edit: select text first, or edit the whole note" className="rounded px-2 py-0.5 text-sm hover:bg-[var(--muted)]">
                  ✨ Edit
                </button>
              )}
              <span className="relative">
                <button onClick={() => setMenu((v) => !v)} title="More" className="rounded px-2 py-0.5 text-sm hover:bg-[var(--muted)]">⋯</button>
                {menu && (
                  <div onClick={() => setMenu(false)} className="absolute right-0 z-20 mt-1 flex w-44 flex-col rounded-md border border-[var(--border)] bg-[var(--background)] py-1 text-sm shadow-lg">
                    <button onClick={() => setShowFormat((v) => !v)} className="px-3 py-1.5 text-left hover:bg-[var(--muted)]">{showFormat ? "Hide" : "Show"} formatting bar</button>
                    <button onClick={runAll} className="px-3 py-1.5 text-left hover:bg-[var(--muted)]">▶▶ Run all cells</button>
                    <button onClick={restartKernels} className="px-3 py-1.5 text-left hover:bg-[var(--muted)]">↻ Restart cells</button>
                    {!readOnly && <button onClick={remove} className="px-3 py-1.5 text-left text-red-500 hover:bg-[var(--muted)]">Delete note</button>}
                    {readOnly && <span className="px-3 py-1.5 text-xs text-[var(--muted-foreground)]">Read-only note</span>}
                  </div>
                )}
              </span>
            </div>
            {showFormat && (
            <div className="flex flex-wrap gap-1 border-b border-[var(--border)] px-4 py-1">
              {editor && !readOnly &&
                BUTTONS.map(([label, title, fn, active]) => (
                  <button
                    key={title}
                    title={title}
                    onClick={() => fn(editor)}
                    className={`rounded px-2 py-0.5 text-sm hover:bg-[var(--muted)] ${active(editor) ? "bg-[var(--muted)] font-bold" : ""}`}
                  >
                    {label}
                  </button>
                ))}
            </div>
            )}
            <EditorContent editor={editor} />
          </>
        ) : (
          <div className="m-auto flex flex-col items-center gap-2 text-[var(--muted-foreground)]">
            Pick a note or create one.
            {compact && <button onClick={() => setShowList(true)} className="rounded border border-[var(--border)] px-3 py-1 text-sm">Open notes</button>}
          </div>
        )}
      </main>
      {showTerminal && <TerminalPanel key={current ?? ""} note={current ?? ""} />}
      </div>
    </div>
  );
}

// The editor pads blocks with empty paragraphs; keep saved files as plain Markdown would be written by hand.
function tidyMarkdown(md: string): string {
  return md.replace(/\n+```(\n|$)/g, "\n```$1").replace(/\n{3,}/g, "\n\n").trimEnd() + "\n";
}

type TreeRow = { kind: "folder" | "note"; path: string; label: string; depth: number };

// Notes named "a/b/c" become folders a and a/b; only folders the user opened show their contents.
// "@wiki/..." notes sit under a top folder named "@wiki".
function treeRows(notes: NoteMeta[], open: Set<string>): TreeRow[] {
  const folders = new Set<string>();
  for (const n of notes) {
    const parts = n.name.split("/");
    for (let i = 1; i < parts.length; i++) folders.add(parts.slice(0, i).join("/"));
  }
  const entries: TreeRow[] = [
    ...[...folders].map((f) => ({ kind: "folder" as const, path: f, label: f.split("/").pop()!, depth: f.split("/").length - 1 })),
    ...notes.map((n) => ({ kind: "note" as const, path: n.name, label: n.name.split("/").pop()!, depth: n.name.split("/").length - 1 })),
  ];
  const key = (r: TreeRow) => {
    const parts = r.path.split("/");
    // Folders before notes at the same level; own notes before read-only sources.
    return parts.map((p, i) => (i === parts.length - 1 ? (r.kind === "folder" ? "0" : "1") : "0") + (p.startsWith("@") ? "~" : "") + p.toLowerCase()).join("/");
  };
  entries.sort((a, b) => key(a).localeCompare(key(b)));
  return entries.filter((r) => {
    const parts = r.path.split("/");
    for (let i = 1; i < parts.length; i++) if (!open.has(parts.slice(0, i).join("/"))) return false;
    return true;
  });
}
