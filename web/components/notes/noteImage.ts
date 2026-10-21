import Image from "@tiptap/extension-image";
import { mergeAttributes } from "@tiptap/core";
import { apiFetch, apiUrl } from "@/lib/api";

const PREFIX = "_assets/";
const served = (src: string) => apiUrl(`/api/notes/${src}`);

/**
 * Images in a note are stored as `![alt](_assets/<name>)` so the Markdown stays portable; the
 * editor shows them through the notes API and maps the path back when HTML is pasted.
 */
export const NoteImage = Image.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      src: {
        default: null,
        parseHTML: (el: HTMLElement) => {
          const src = el.getAttribute("src") ?? "";
          const base = served("");
          return src.startsWith(base) ? src.slice(base.length) : src;
        },
      },
      // Always rendered, so a screen reader never meets an image with no alt attribute.
      alt: { default: "", parseHTML: (el: HTMLElement) => el.getAttribute("alt") ?? "" },
    };
  },
  renderHTML({ HTMLAttributes }) {
    const src = typeof HTMLAttributes.src === "string" && HTMLAttributes.src.startsWith(PREFIX) ? served(HTMLAttributes.src) : HTMLAttributes.src;
    return ["img", mergeAttributes(HTMLAttributes, { src, alt: HTMLAttributes.alt ?? "", loading: "lazy" })];
  },
}).configure({ allowBase64: false, inline: false });

export const isImageFile = (file: File) => /^image\/(png|jpeg|gif|webp)$/.test(file.type);

/** Upload one image; resolves to its `_assets/…` path, or throws with the server's message. */
export async function uploadNoteImage(file: File): Promise<string> {
  const body = new FormData();
  body.append("file", file, file.name || "image");
  const r = await apiFetch(apiUrl("/api/notes/_assets"), { method: "POST", body });
  const data = (await r.json().catch(() => ({}))) as { path?: string; detail?: string };
  if (!r.ok || !data.path) throw new Error(data.detail || `HTTP ${r.status}`);
  return data.path;
}

/** Alt text from the file name: "Screenshot 2026-01-01.png" -> "Screenshot 2026-01-01". */
export const altFromFile = (file: File, fallback: string) => file.name.replace(/\.[a-z0-9]+$/i, "").trim() || fallback;
