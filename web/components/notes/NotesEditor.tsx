"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { Markdown } from "@tiptap/markdown";
import Placeholder from "@tiptap/extension-placeholder";
import TaskList from "@tiptap/extension-task-list";
import TaskItem from "@tiptap/extension-task-item";
import Mathematics from "@tiptap/extension-mathematics";
import { TableKit } from "@tiptap/extension-table";
import "katex/dist/katex.min.css";
import { ChevronDown, ChevronRight, FilePlus, Folder, MessageCircleQuestion,
  MoreHorizontal,
  PanelLeft,
  WandSparkles, SquareTerminal } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useRouter } from "next/navigation";
import Link from "next/link";
import dynamic from "next/dynamic";

const TerminalPanel = dynamic(() => import("./TerminalPanel"), { ssr: false });

import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { apiFetch, apiUrl } from "@/lib/api";
import { IconButton } from "@/shared/ui/IconButton";
import BacklinksPanel from "./BacklinksPanel";
import NoteNameDialog from "./NoteNameDialog";
import { NoteImage, altFromFile, isImageFile, uploadNoteImage } from "./noteImage";
import { RunnableCodeBlock, cellRunners, notebook } from "./RunnableCodeBlock";
import { SlashMenu, slashItems } from "./slashMenu";
import { Wikilink, WikilinkSuggest, refreshWikilinks } from "./wikilinks";

type NoteMeta = { name: string; updated_at: number; read_only?: boolean };
type Dialog = "new" | "rename" | "ai" | "delete" | null;

const api = (path = "") => apiUrl(`/api/notes${path}`);
const json = { "Content-Type": "application/json" };
const encPath = (name: string) => name.split("/").map(encodeURIComponent).join("/");

const menuItem =
  "w-full px-3 py-2 text-left hover:bg-[var(--muted)] focus-visible:bg-[var(--muted)] focus-visible:outline-none";

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

// Below this width the note list folds away behind a button, like the chat side panel's view.
const NARROW = "(max-width: 767px)";

// compact: the chat side panel's view (note list folds away behind a button, toolbar wraps).
export default function NotesEditor({ compact = false }: { compact?: boolean }) {
  const { t } = useTranslation();
  const tRef = useRef(t);
  tRef.current = t;
  const [narrow, setNarrow] = useState(false);
  const [showList, setShowList] = useState(!compact);
  const [notes, setNotes] = useState<NoteMeta[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [readOnly, setReadOnly] = useState(false);
  const [showTerminal, setShowTerminal] = useState(false);
  const [query, setQuery] = useState("");
  const [dialog, setDialog] = useState<Dialog>(null);
  const [linksVersion, setLinksVersion] = useState(0);
  const [renameNonce, setRenameNonce] = useState(0);
  const imageInput = useRef<HTMLInputElement>(null);
  const insertImagesRef = useRef<((files: File[], at?: number) => Promise<void>) | null>(null);
  const aiRange = useRef<{ from: number; to: number; whole: boolean } | null>(null);
  const router = useRouter();
  const overlay = compact || narrow;

  useEffect(() => {
    const media = window.matchMedia(NARROW);
    const sync = () => {
      setNarrow(media.matches);
      if (media.matches) setShowList(false);
    };
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);

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
  const menuRef = useRef<HTMLSpanElement>(null);
  const [showFormat, setShowFormat] = useState(false);

  // The "More" menu closes on Escape and on any click outside it.
  useEffect(() => {
    if (!menu) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setMenu(false);
    const onDown = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenu(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [menu]);

  // Co-Writer inside Notes: rewrite the selection (or the whole note) by an instruction.
  const startAiEdit = () => {
    if (!editor || readOnly) return;
    const { from, to } = editor.state.selection;
    aiRange.current = { from, to, whole: from === to };
    setDialog("ai");
  };
  const aiEdit = async (instruction: string) => {
    const range = aiRange.current;
    setDialog(null);
    if (!editor || readOnly || !range) return;
    const { from, to, whole } = range;
    const text = whole ? editor.getMarkdown() : editor.state.doc.textBetween(from, to, "\n");
    setStatus(t("AI editing…"));
    const r = await apiFetch(apiUrl("/api/documents/actions/edit"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text.slice(0, 50000), instruction, action: "rewrite" }),
    });
    if (!r.ok) return setStatus(t("AI edit failed"));
    const edited = (await r.json()).edited_text as string;
    if (whole) editor.commands.setContent(edited, { contentType: "markdown" });
    else editor.chain().focus().insertContentAt({ from, to }, edited, { contentType: "markdown" }).run();
    setStatus(t("AI edited — Ctrl+Z to undo"));
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
  const openLinkRef = useRef<((target: string, resolved: string | null) => void) | null>(null);
  const namesRef = useRef<string[]>([]);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const loading = useRef(false);
  const currentRef = useRef<string | null>(null);
  currentRef.current = current;
  namesRef.current = notes.map((n) => n.name);

  const refresh = useCallback(async () => {
    const r = await apiFetch(api());
    if (r.ok) setNotes((await r.json()).notes);
  }, []);

  const save = useCallback(async (name: string, md: string) => {
    setStatus(tRef.current("Saving…"));
    try {
      const r = await apiFetch(api(`/${encPath(name)}`), {
        method: "PUT",
        headers: json,
        body: JSON.stringify({ content: md }),
      });
      setStatus(r.ok ? tRef.current("Saved") : tRef.current("Save failed"));
    } catch {
      setStatus(tRef.current("Save failed"));
    }
  }, []);

  const editor = useEditor({
    immediatelyRender: false,
    extensions: [
      StarterKit.configure({ codeBlock: false }),
      RunnableCodeBlock,
      TaskList,
      TaskItem.configure({ nested: true }),
      Placeholder.configure({ placeholder: () => tRef.current("Write something… type / for blocks, [[ to link a note") }),
      Mathematics,
      // Without this a markdown table is dropped on the first edit of the note.
      TableKit,
      NoteImage,
      Wikilink.configure({
        getNames: () => namesRef.current,
        onOpen: (target, resolved) => openLinkRef.current?.(target, resolved),
      }),
      WikilinkSuggest.configure({
        getNames: () => namesRef.current,
        createLabel: (name) => tRef.current("Create note “{{name}}”", { name }),
        menuLabel: tRef.current("Link to a note"),
        emptyLabel: tRef.current("No matching notes"),
      }),
      SlashMenu.configure({
        getItems: () => slashItems((key) => tRef.current(key), { pickImage: () => imageInput.current?.click() }),
        menuLabel: tRef.current("Insert a block"),
        emptyLabel: tRef.current("No matching blocks"),
      }),
      Markdown,
    ],
    editorProps: {
      attributes: { class: "notes-prose min-h-[60vh] px-4 py-6 outline-none sm:px-8", "aria-label": tRef.current("Note content"), "aria-multiline": "true" },
      // Pasted or dropped images are uploaded and inserted; everything else keeps its default handling.
      handlePaste: (view, event) => {
        const files = [...(event.clipboardData?.files ?? [])].filter(isImageFile);
        if (!files.length || !view.editable) return false;
        void insertImagesRef.current?.(files);
        return true;
      },
      handleDrop: (view, event) => {
        const files = [...(event.dataTransfer?.files ?? [])].filter(isImageFile);
        if (!files.length || !view.editable) return false;
        const at = view.posAtCoords({ left: event.clientX, top: event.clientY })?.pos;
        void insertImagesRef.current?.(files, at);
        return true;
      },
    },
    onUpdate: ({ editor }) => {
      const name = currentRef.current;
      if (loading.current || !name) return;
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => save(name, tidyMarkdown(editor.getMarkdown())).then(refresh), 800);
    },
  });

  insertImagesRef.current = async (files, at) => {
    if (!editor) return;
    setStatus(t("Uploading image…"));
    try {
      for (const file of files) {
        const path = await uploadNoteImage(file);
        const image = { type: "image", attrs: { src: path, alt: altFromFile(file, t("Pasted image")) } };
        if (at === undefined) editor.chain().focus().insertContent(image).run();
        else editor.chain().focus().insertContentAt(at, image).run();
      }
      setStatus("");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : t("Could not add the image"));
    }
  };

  // Links to notes that do not exist yet are styled differently; re-check when the note list changes.
  useEffect(() => {
    if (editor) refreshWikilinks(editor);
  }, [editor, notes]);

  // The debounced save reads the editor when it fires, so it must run (or be dropped) before the
  // editor shows a different note or the note changes name: otherwise it writes one note's text
  // into another note's file.
  const flushPendingSave = useCallback(async () => {
    if (!timer.current) return;
    clearTimeout(timer.current);
    timer.current = null;
    const name = currentRef.current;
    if (editor && name && !loading.current) await save(name, tidyMarkdown(editor.getMarkdown()));
  }, [editor, save]);

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
      await flushPendingSave();
      const r = await apiFetch(api(`/${encPath(name)}`));
      if (!r.ok) return setStatus(tRef.current("Could not open note"));
      const { content, read_only } = await r.json();
      loading.current = true;
      editor.commands.setContent(content, { contentType: "markdown" });
      editor.setEditable(!read_only);
      loading.current = false;
      setReadOnly(Boolean(read_only));
      setCurrent(name);
      setStatus("");
      setLinksVersion((v) => v + 1);
    },
    [editor, flushPendingSave],
  );
  openRef.current = open;

  const createNote = async (typed: string) => {
    let n = typed;
    for (let i = 2; notes.some((x) => x.name === n); i++) n = `${typed} ${i}`;
    await flushPendingSave();
    await save(n, "");
    await refresh();
    await open(n);
  };

  // Follow a [[link]]: open the note, or create it first when it does not exist yet.
  openLinkRef.current = (target, resolved) => {
    if (resolved) void open(resolved);
    else if (target && !target.startsWith("@")) void createNote(target.replace(/^\/+|\/+$/g, ""));
  };

  const remove = async () => {
    setDialog(null);
    if (!current) return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    await apiFetch(api(`/${encPath(current)}`), { method: "DELETE" });
    setCurrent(null);
    editor?.commands.clearContent();
    refresh();
  };

  const rename = async (newName: string) => {
    const name = newName.trim().replace(/^\/+|\/+$/g, "");
    if (!current || !name || name === current) return;
    await flushPendingSave();
    const r = await apiFetch(api(`/${encPath(current)}/rename`), {
      method: "POST",
      headers: json,
      body: JSON.stringify({ new_name: name }),
    });
    if (r.ok) {
      await refresh();
      // The server also rewrote [[links]] to this note; reload so the next save does not undo that.
      await open(name);
    } else {
      setStatus(r.status === 409 ? t("A note with that name already exists") : t("Could not rename"));
      setRenameNonce((n) => n + 1); // put the old name back in the field
    }
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

  const pick = (name: string) => {
    void open(name);
    if (overlay) setShowList(false);
  };

  const noteNameProblem = (value: string) => {
    const name = value.replace(/^\/+|\/+$/g, "");
    if (name.startsWith("@")) return t("Names starting with @ are read-only sources.");
    if (name.split("/").some((part) => part.startsWith("."))) return t("A name cannot start with a dot.");
    return "";
  };

  return (
    <div className={`relative flex ${compact ? "h-full" : "h-[calc(100vh-4rem)]"} text-[var(--foreground)]`}>
      {!compact && <h1 className="sr-only">{t("Notes")}</h1>}
      {showList && (
      <aside aria-label={t("Notes")} className={`flex w-64 shrink-0 flex-col border-r border-[var(--border)] ${overlay ? "absolute inset-y-0 left-0 z-10 bg-[var(--background)] shadow-lg" : ""}`}>
        <div className="flex items-center justify-between p-3">
          <span className="font-medium">{t("Notes")}</span>
          <span className="flex gap-1">
            <IconButton
              label={t("Terminal (sandboxed, no internet)")}
              aria-pressed={showTerminal}
              onClick={() => setShowTerminal((v) => !v)}
              className={showTerminal ? "bg-[var(--muted)]" : ""}
              icon={<SquareTerminal size={16} />}
            />
            <IconButton label={t("New note")} onClick={() => setDialog("new")} icon={<FilePlus size={16} />} />
          </span>
        </div>
        <input
          value={query}
          aria-label={t("Search notes")}
          onChange={(e) => {
            setQuery(e.target.value);
            setHits(null);
          }}
          onKeyDown={(e) => e.key === "Enter" && searchText()}
          placeholder={t("Search notes…")}
          className="mx-3 rounded-lg border border-[var(--border)] bg-transparent px-2 py-1.5 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
        />
        <p className="mx-3 mb-2 mt-1 text-xs text-[var(--muted-foreground)]">{t("Press Enter to search inside notes.")}</p>
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
                  onClick={() => pick(h.name)}
                  className={`w-full px-3 py-1.5 text-left text-sm hover:bg-[var(--muted)] focus-visible:bg-[var(--muted)] focus-visible:outline-none ${h.name === current ? "bg-[var(--muted)]" : ""}`}
                >
                  <div className="truncate font-medium">{h.name}</div>
                  {h.snippet && <div className="line-clamp-2 text-xs text-[var(--muted-foreground)]">{h.snippet}</div>}
                </button>
              </li>
            ))}
            {hits && !hits.length && <li className="px-3 text-sm text-[var(--muted-foreground)]">{t("No text matches.")}</li>}
          </ul>
        ) : (
        <ul className="flex-1 overflow-y-auto pb-2">
          {treeRows(notes, openFolders).map((row) =>
            row.kind === "folder" ? (
              <li key={`f:${row.path}`}>
                <button
                  aria-expanded={openFolders.has(row.path)}
                  onClick={() =>
                    setOpenFolders((prev) => {
                      const next = new Set(prev);
                      if (next.has(row.path)) next.delete(row.path);
                      else next.add(row.path);
                      return next;
                    })
                  }
                  style={{ paddingLeft: 12 + row.depth * 14 }}
                  className="flex w-full items-center gap-1 truncate py-1.5 pr-3 text-left text-sm text-[var(--muted-foreground)] hover:bg-[var(--muted)] focus-visible:bg-[var(--muted)] focus-visible:outline-none"
                >
                  {openFolders.has(row.path) ? <ChevronDown size={14} aria-hidden /> : <ChevronRight size={14} aria-hidden />}
                  <Folder size={14} aria-hidden /> {row.label}
                </button>
              </li>
            ) : (
              <li key={row.path}>
                <button
                  onClick={() => pick(row.path)}
                  aria-current={row.path === current ? "page" : undefined}
                  style={{ paddingLeft: 30 + row.depth * 14 }}
                  className={`w-full truncate py-1.5 pr-3 text-left text-sm hover:bg-[var(--muted)] focus-visible:bg-[var(--muted)] focus-visible:outline-none ${row.path === current ? "bg-[var(--muted)] font-medium" : ""}`}
                >
                  {row.label}
                </button>
              </li>
            ),
          )}
          {!notes.length && <li className="px-3 text-sm text-[var(--muted-foreground)]">{t("No notes yet.")}</li>}
        </ul>
        )}
        <Link href="/notebooks" className="border-t border-[var(--border)] p-3 text-sm text-[var(--muted-foreground)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]">
          {t("Saved from chat")} →
        </Link>
      </aside>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto">
        {current ? (
          <>
            <div className="flex flex-wrap items-center gap-2 whitespace-nowrap border-b border-[var(--border)] px-4 py-2">
              {overlay && (
                <IconButton label={t("All notes")} aria-expanded={showList} onClick={() => setShowList((v) => !v)} icon={<PanelLeft size={16} />} />
              )}
              <input
                key={`${current}:${renameNonce}`}
                aria-label={t("Note name")}
                readOnly={readOnly}
                defaultValue={current}
                onBlur={(e) => rename(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
                className="min-h-9 min-w-0 flex-1 bg-transparent text-lg font-semibold outline-none focus-visible:underline"
              />
              <span role="status" aria-live="polite" className="text-xs text-[var(--muted-foreground)]">{status}</span>
              <IconButton label={t("Ask the tutor about the selection (or the whole note)")} onClick={askTutor} icon={<MessageCircleQuestion size={16} />} />
              {!readOnly && (
                <IconButton label={t("AI edit: select text first, or edit the whole note")} onClick={startAiEdit} icon={<WandSparkles size={16} />} />
              )}
              <span ref={menuRef} className="relative">
                <IconButton label={t("More")} aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu((v) => !v)} icon={<MoreHorizontal size={16} />} />
                {menu && (
                  <div role="menu" onClick={() => setMenu(false)} className="absolute right-0 z-20 mt-1 flex w-52 flex-col rounded-lg border border-[var(--border)] bg-[var(--background)] py-1 text-sm shadow-lg">
                    <button role="menuitem" onClick={() => setShowFormat((v) => !v)} className={menuItem}>{showFormat ? t("Hide formatting bar") : t("Show formatting bar")}</button>
                    {!readOnly && <button role="menuitem" onClick={() => setDialog("rename")} className={menuItem}>{t("Rename or move…")}</button>}
                    <button role="menuitem" onClick={runAll} className={menuItem}>▶▶ {t("Run all cells")}</button>
                    <button role="menuitem" onClick={restartKernels} className={menuItem}>↻ {t("Restart cells")}</button>
                    {!readOnly && <button role="menuitem" onClick={() => setDialog("delete")} className={`${menuItem} text-destructive`}>{t("Delete note")}</button>}
                    {readOnly && <span className="px-3 py-1.5 text-xs text-[var(--muted-foreground)]">{t("Read-only note")}</span>}
                  </div>
                )}
              </span>
            </div>
            {showFormat && (
            <div role="toolbar" aria-label={t("Formatting")} className="flex flex-wrap gap-1 border-b border-[var(--border)] px-4 py-1">
              {editor && !readOnly &&
                BUTTONS.map(([label, title, fn, active]) => (
                  <button
                    key={title}
                    aria-label={t(title)}
                    aria-pressed={active(editor)}
                    onClick={() => fn(editor)}
                    className={`min-h-9 min-w-9 rounded-lg px-2 py-0.5 text-sm hover:bg-[var(--muted)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] ${active(editor) ? "bg-[var(--muted)] font-bold" : ""}`}
                  >
                    {label}
                  </button>
                ))}
            </div>
            )}
            <EditorContent editor={editor} />
            <input
              ref={imageInput}
              type="file"
              accept="image/png,image/jpeg,image/gif,image/webp"
              multiple
              hidden
              aria-label={t("Add an image")}
              onChange={(event) => {
                const files = [...(event.target.files ?? [])];
                event.target.value = "";
                if (files.length) void insertImagesRef.current?.(files);
              }}
            />
            <BacklinksPanel note={current} version={linksVersion} onOpen={(name) => void open(name)} />
          </>
        ) : (
          <div className="m-auto flex flex-col items-center gap-2 p-6 text-center text-[var(--muted-foreground)]">
            {t("Pick a note or create one.")}
            {overlay && <button onClick={() => setShowList(true)} className="rounded-lg border border-[var(--border)] px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]">{t("Open notes")}</button>}
          </div>
        )}
      </div>
      {showTerminal && <TerminalPanel key={current ?? ""} note={current ?? ""} />}
      </div>

      <NoteNameDialog
        open={dialog === "new"}
        title={t("New note")}
        label={t("Name (use folder/name for a folder)")}
        initial={t("Untitled")}
        confirmLabel={t("Create")}
        validate={noteNameProblem}
        onClose={() => setDialog(null)}
        onSubmit={(value) => {
          setDialog(null);
          void createNote(value.replace(/^\/+|\/+$/g, ""));
        }}
      />
      <NoteNameDialog
        open={dialog === "rename"}
        title={t("Rename or move")}
        label={t("New name (use folder/name to move it)")}
        initial={current ?? ""}
        confirmLabel={t("Rename")}
        validate={(value) =>
          noteNameProblem(value) ||
          (value !== current && notes.some((n) => n.name === value.replace(/^\/+|\/+$/g, ""))
            ? t("A note with that name already exists")
            : "")
        }
        onClose={() => setDialog(null)}
        onSubmit={(value) => {
          setDialog(null);
          void rename(value);
        }}
      />
      <NoteNameDialog
        open={dialog === "ai"}
        title={t("AI edit")}
        label={aiRange.current?.whole ? t("What should change in the whole note?") : t("What should change in the selection?")}
        initial=""
        multiline
        confirmLabel={t("Edit")}
        onClose={() => setDialog(null)}
        onSubmit={(value) => void aiEdit(value)}
      />
      <ConfirmDialog
        open={dialog === "delete"}
        title={t("Delete this note?")}
        confirmLabel={t("Delete")}
        tone="danger"
        onConfirm={() => void remove()}
        onCancel={() => setDialog(null)}
      >
        {t("“{{name}}” is moved to the trash folder, not erased.", { name: current ?? "" })}
      </ConfirmDialog>
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
