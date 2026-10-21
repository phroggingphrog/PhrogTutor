import { Extension } from "@tiptap/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";

// Mirrors `parse_tags` in deeptutor/services/notes_links.py: `#tag` outside code; `# Heading` is not a tag.
const TAG = /(?<![\w&/#])#([A-Za-z][\w/-]*)/g;

export interface NoteTagsOptions {
  onTag: (tag: string) => void;
}

/** `#tags` in the text are shown as chips; clicking one filters the note list by that tag. */
export const NoteTags = Extension.create<NoteTagsOptions>({
  name: "noteTags",

  addOptions() {
    return { onTag: () => {} };
  },

  addProseMirrorPlugins() {
    const { onTag } = this.options;
    return [
      new Plugin({
        key: new PluginKey("noteTags"),
        props: {
          decorations: (state) => {
            const found: Decoration[] = [];
            state.doc.descendants((node, pos, parent) => {
              if (!node.isText || !node.text || parent?.type.spec.code) return;
              if (node.marks.some((mark) => mark.type.name === "code")) return;
              for (const match of node.text.matchAll(TAG)) {
                const text = match[0].replace(/[/-]+$/, "");
                const from = pos + (match.index ?? 0);
                found.push(Decoration.inline(from, from + text.length, { class: "note-tag", "data-tag": text.slice(1).toLowerCase() }));
              }
            });
            return DecorationSet.create(state.doc, found);
          },
          handleClick: (_view, _pos, event) => {
            const tag = (event.target as HTMLElement | null)?.closest<HTMLElement>(".note-tag")?.dataset.tag;
            if (!tag) return false;
            onTag(tag);
            return true;
          },
        },
      }),
    ];
  },
});
