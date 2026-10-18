# Notebook editor recon (TASK-99)

## pi-tutor today
- UI: `web/components/notebook/NotebookConsole.tsx` (list of notebooks, records, filters), `NotebookRecord*`, `SaveToNotebookModal` (save from chat). Route `web/app/(utility)/notebooks`.
- API: `deeptutor/api/routers/notebook.py` (`/api/notebooks/...`, records CRUD, export). Storage: `NotebookManager` (`deeptutor/services/notebook/service.py`): one `<id>.json` per notebook + `notebooks_index.json` under the user's workspace `notebook/` dir. Records are typed AI outputs (solve, chat, research...), not freely written text.
- Code Run: `web/components/common/RichCodeBlock.tsx` posts `{language, code, original}` to `/api/run-code` and shows stdout/stderr/images. The editor must reuse this.
- Web deps: Next 16, React 19, react-markdown, rehype-katex, no editor library yet.

## Markra (AGPL-3.0) - study only
- Tauri desktop app, pnpm monorepo (`packages/editor`, `editor-react`, `ai`).
- Engine: CodeMirror 6 (`@codemirror/*`, lang-markdown), live-preview style (source with rendered blocks), plus katex, mermaid, lowlight.
- File model: a folder of plain `.md` files; no database.
- AI: separate package; inline suggestions previewed as diffs the user accepts.

## NoteGen (GPL-3.0) - study only
- Tauri + Next.js. Editor: Tiptap (ProseMirror) with many extensions (task list, table, code block, link, image), CodeMirror for source mode.
- File model: workspace folder of `.md` files plus a left file tree and tabs; sync options.
- AI: chat/"record" capture, inline completion and suggestion highlights.

## What we take (ideas only, no code)
Folder of `.md` files; left file list; one rich editor; AI as optional buttons. Both are copyleft, so nothing is copied.

## Plan (smallest editor that works)
1. Backend: `notes` router, files `*.md` under `<workspace>/notes/` (list, read, write, create, delete, rename); path-safe.
2. Editor: Tiptap (MIT) + StarterKit, task list, link, markdown I/O via `tiptap-markdown` (MIT); KaTeX math if cheap.
3. Code blocks: custom node view that renders pi-tutor's `RichCodeBlock` (Run button) read/edit.
4. UI: new `/notes` page: file list sidebar, title, editor, debounce autosave.
5. Existing chat-saved records stay in Notebooks; listed read-only in the sidebar as "Saved from chat".
