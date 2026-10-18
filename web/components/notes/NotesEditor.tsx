"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { Markdown } from "@tiptap/markdown";
import Placeholder from "@tiptap/extension-placeholder";
import TaskList from "@tiptap/extension-task-list";
import TaskItem from "@tiptap/extension-task-item";
import { FilePlus, Trash2 } from "lucide-react";
import Link from "next/link";

import { apiFetch, apiUrl } from "@/lib/api";
import { RunnableCodeBlock, cellRunners, notebook } from "./RunnableCodeBlock";

type NoteMeta = { name: string; updated_at: number };

const api = (path = "") => apiUrl(`/api/notes${path}`);
const json = { "Content-Type": "application/json" };

const BUTTONS: [string, string, (e: any) => void, (e: any) => boolean][] = [
  ["H1", "Heading 1", (e) => e.chain().focus().toggleHeading({ level: 1 }).run(), (e) => e.isActive("heading", { level: 1 })],
  ["H2", "Heading 2", (e) => e.chain().focus().toggleHeading({ level: 2 }).run(), (e) => e.isActive("heading", { level: 2 })],
  ["B", "Bold", (e) => e.chain().focus().toggleBold().run(), (e) => e.isActive("bold")],
  ["I", "Italic", (e) => e.chain().focus().toggleItalic().run(), (e) => e.isActive("italic")],
  ["•", "Bullet list", (e) => e.chain().focus().toggleBulletList().run(), (e) => e.isActive("bulletList")],
  ["1.", "Numbered list", (e) => e.chain().focus().toggleOrderedList().run(), (e) => e.isActive("orderedList")],
  ["☑", "Task list", (e) => e.chain().focus().toggleTaskList().run(), (e) => e.isActive("taskList")],
  ["</>", "Code block", (e) => e.chain().focus().toggleCodeBlock({ language: "python" }).run(), (e) => e.isActive("codeBlock")],
];

export default function NotesEditor() {
  const [notes, setNotes] = useState<NoteMeta[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [status, setStatus] = useState("");
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
    const r = await apiFetch(api(`/${encodeURIComponent(name)}`), {
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

  const open = useCallback(
    async (name: string) => {
      if (!editor) return;
      const r = await apiFetch(api(`/${encodeURIComponent(name)}`));
      if (!r.ok) return;
      const { content } = await r.json();
      loading.current = true;
      editor.commands.setContent(content, { contentType: "markdown" });
      loading.current = false;
      setCurrent(name);
      setStatus("");
    },
    [editor],
  );

  const create = async () => {
    let n = "Untitled";
    for (let i = 2; notes.some((x) => x.name === n); i++) n = `Untitled ${i}`;
    await save(n, "");
    await refresh();
    await open(n);
  };

  const remove = async () => {
    if (!current || !confirm(`Delete "${current}"? (moved to .trash)`)) return;
    await apiFetch(api(`/${encodeURIComponent(current)}`), { method: "DELETE" });
    setCurrent(null);
    editor?.commands.clearContent();
    refresh();
  };

  const rename = async (newName: string) => {
    const name = newName.trim();
    if (!current || !name || name === current) return;
    const r = await apiFetch(api(`/${encodeURIComponent(current)}/rename`), {
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
    for (const [, run] of cells) await run();
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
    <div className="flex h-[calc(100vh-4rem)] text-[var(--foreground)]">
      <aside className="flex w-64 shrink-0 flex-col border-r border-[var(--border)]">
        <div className="flex items-center justify-between p-3">
          <span className="font-medium">Notes</span>
          <button onClick={create} title="New note" className="rounded p-1 hover:bg-[var(--muted)]">
            <FilePlus size={16} />
          </button>
        </div>
        <ul className="flex-1 overflow-y-auto">
          {notes.map((n) => (
            <li key={n.name}>
              <button
                onClick={() => open(n.name)}
                className={`w-full truncate px-3 py-1.5 text-left text-sm hover:bg-[var(--muted)] ${n.name === current ? "bg-[var(--muted)] font-medium" : ""}`}
              >
                {n.name}
              </button>
            </li>
          ))}
          {!notes.length && <li className="px-3 text-sm text-[var(--muted-foreground)]">No notes yet.</li>}
        </ul>
        <Link href="/notebooks" className="border-t border-[var(--border)] p-3 text-sm text-[var(--muted-foreground)] hover:underline">
          Saved from chat →
        </Link>
      </aside>
      <main className="flex min-w-0 flex-1 flex-col overflow-y-auto">
        {current ? (
          <>
            <div className="flex items-center gap-2 border-b border-[var(--border)] px-4 py-2">
              <input
                key={current}
                defaultValue={current}
                onBlur={(e) => rename(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
                className="min-w-0 flex-1 bg-transparent text-lg font-semibold outline-none"
              />
              <span className="text-xs text-[var(--muted-foreground)]">{status}</span>
              <button onClick={runAll} title="Run every code cell, top to bottom" className="rounded px-2 py-0.5 text-sm hover:bg-[var(--muted)]">
                ▶▶ Run all
              </button>
              <button onClick={restartKernels} title="Forget all variables and clear outputs" className="rounded px-2 py-0.5 text-sm hover:bg-[var(--muted)]">
                ↻ Restart
              </button>
              <button onClick={remove} title="Delete" className="rounded p-1 hover:bg-[var(--muted)]">
                <Trash2 size={16} />
              </button>
            </div>
            <div className="flex flex-wrap gap-1 border-b border-[var(--border)] px-4 py-1">
              {editor &&
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
            <EditorContent editor={editor} />
          </>
        ) : (
          <div className="m-auto text-[var(--muted-foreground)]">Pick a note or create one.</div>
        )}
      </main>
    </div>
  );
}

// The editor pads blocks with empty paragraphs; keep saved files as plain Markdown would be written by hand.
function tidyMarkdown(md: string): string {
  return md.replace(/\n+```(\n|$)/g, "\n```$1").replace(/\n{3,}/g, "\n\n").trimEnd() + "\n";
}
