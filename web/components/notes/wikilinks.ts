import { Extension, Node, mergeAttributes } from "@tiptap/core";
import { NodeSelection, Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import Suggestion from "@tiptap/suggestion";
import { menuRenderer, type MenuItem } from "./suggestionMenu";

const key = (value: string) => value.trim().replace(/^\/+|\/+$/g, "").replace(/\.md$/i, "").toLowerCase();
const tail = (value: string) => key(value).split("/").pop() ?? "";

/**
 * The note a link target points at, or null. Mirrors `resolve_link` in
 * `deeptutor/services/notes_links.py`: full path or bare name, case-insensitive; among bare-name
 * matches the shallowest wins, own notes before `@source/` notes, then alphabetical.
 */
export function resolveWikilink(target: string, names: readonly string[]): string | null {
  const wanted = key(target);
  if (!wanted) return null;
  const exact = names.filter((name) => key(name) === wanted);
  const pool = exact.length || wanted.includes("/") ? exact : names.filter((name) => tail(name) === wanted);
  if (!pool.length) return null;
  const rank = (name: string) => [name.startsWith("@") ? 1 : 0, name.split("/").length, name.toLowerCase()] as const;
  return [...pool].sort((a, b) => {
    const [x, y] = [rank(a), rank(b)];
    return x[0] - y[0] || x[1] - y[1] || x[2].localeCompare(y[2]);
  })[0];
}

/** The shortest text that still names `name` unambiguously: its last segment when that is unique. */
export function linkTargetFor(name: string, names: readonly string[]): string {
  const bare = name.split("/").pop() ?? name;
  return names.filter((other) => tail(other) === tail(bare)).length === 1 ? bare : name;
}

export interface WikilinkOptions {
  getNames: () => readonly string[];
  /** `resolved` is the existing note, or null when the link points at a note that does not exist yet. */
  onOpen: (target: string, resolved: string | null) => void;
}

const REFRESH = "wikilink-refresh";
const TOKEN = /^\[\[([^\[\]|#\n]+?)(#[^\[\]|\n]*)?(\|[^\[\]\n]*)?\]\]/;

/** `[[target]]`, `[[target|alias]]`, `[[target#heading]]` as an inline atom that round-trips to Markdown. */
export const Wikilink = Node.create<WikilinkOptions>({
  name: "wikilink",
  group: "inline",
  inline: true,
  atom: true,
  selectable: true,

  addOptions() {
    return { getNames: () => [], onOpen: () => {} };
  },

  addAttributes() {
    return { target: { default: "" }, heading: { default: "" }, alias: { default: "" } };
  },

  parseHTML() {
    return [
      {
        tag: 'a[data-type="wikilink"]',
        getAttrs: (el) => ({
          target: (el as HTMLElement).dataset.target ?? "",
          heading: (el as HTMLElement).dataset.heading ?? "",
          alias: (el as HTMLElement).dataset.alias ?? "",
        }),
      },
    ];
  },

  renderHTML({ node, HTMLAttributes }) {
    const { target, heading, alias } = node.attrs;
    return [
      "a",
      mergeAttributes(HTMLAttributes, {
        "data-type": "wikilink",
        "data-target": target,
        "data-heading": heading,
        "data-alias": alias,
        class: "wikilink",
        role: "link",
        "aria-label": target,
      }),
      alias || target,
    ];
  },

  parseMarkdown: (token) => ({
    type: "wikilink",
    attrs: { target: token.target, heading: token.heading ?? "", alias: token.alias ?? "" },
  }),

  renderMarkdown: (node) => {
    const { target = "", heading = "", alias = "" } = node.attrs ?? {};
    return `[[${target}${heading ? `#${heading}` : ""}${alias ? `|${alias}` : ""}]]`;
  },

  markdownTokenizer: {
    name: "wikilink",
    level: "inline",
    start: (src) => src.indexOf("[["),
    tokenize: (src) => {
      const match = TOKEN.exec(src);
      if (!match) return;
      return {
        type: "wikilink",
        raw: match[0],
        target: match[1].trim(),
        heading: match[2]?.slice(1) ?? "",
        alias: match[3]?.slice(1) ?? "",
      };
    },
  },

  addProseMirrorPlugins() {
    const { getNames, onOpen } = this.options;
    const nodeName = this.name;
    const open = (target: string) => onOpen(target, resolveWikilink(target, getNames()));
    return [
      new Plugin({
        key: new PluginKey("wikilink"),
        props: {
          // Links to notes that do not exist yet look different, so a typo is visible.
          decorations: (state) => {
            const names = getNames();
            const found: Decoration[] = [];
            state.doc.descendants((node, pos) => {
              if (node.type.name === nodeName && !resolveWikilink(node.attrs.target, names))
                found.push(Decoration.node(pos, pos + node.nodeSize, { class: "wikilink-missing" }));
            });
            return DecorationSet.create(state.doc, found);
          },
          handleClick: (_view, _pos, event) => {
            const el = (event.target as HTMLElement | null)?.closest<HTMLElement>('[data-type="wikilink"]');
            if (!el) return false;
            open(el.dataset.target ?? "");
            return true;
          },
          // Arrow onto a link, then Enter: the keyboard way to follow it.
          handleKeyDown: (view, event) => {
            const sel = view.state.selection;
            if (event.key !== "Enter" || !(sel instanceof NodeSelection) || sel.node.type.name !== nodeName) return false;
            open(sel.node.attrs.target);
            return true;
          },
        },
      }),
    ];
  },
});

/** Re-evaluate which links resolve, after the note list changed. Does not touch the document. */
export function refreshWikilinks(editor: { view: { state: any; dispatch: (tr: any) => void } }) {
  editor.view.dispatch(editor.view.state.tr.setMeta(REFRESH, true));
}

interface LinkItem extends MenuItem {
  target: string;
  create?: boolean;
}

export interface WikilinkSuggestOptions {
  getNames: () => readonly string[];
  createLabel: (name: string) => string;
  menuLabel: string;
  emptyLabel: string;
}

/** Typing `[[` opens a note picker; picking inserts a wikilink. */
export const WikilinkSuggest = Extension.create<WikilinkSuggestOptions>({
  name: "wikilinkSuggest",

  addOptions() {
    return { getNames: () => [], createLabel: (name) => name, menuLabel: "Notes", emptyLabel: "" };
  },

  addProseMirrorPlugins() {
    const { getNames, createLabel, menuLabel, emptyLabel } = this.options;
    return [
      Suggestion<LinkItem, LinkItem>({
        editor: this.editor,
        pluginKey: new PluginKey("wikilinkSuggest"),
        char: "[[",
        allowSpaces: true,
        allowedPrefixes: null,
        allow: ({ state, range }) => !state.doc.resolve(range.from).parent.type.spec.code,
        items: ({ query }) => {
          const names = getNames();
          const q = query.trim().toLowerCase();
          const scored = names
            .map((name) => {
              const low = name.toLowerCase();
              const base = low.split("/").pop() ?? low;
              const score = !q ? 1 : base.startsWith(q) ? 3 : low.includes(q) ? 2 : 0;
              return { name, score };
            })
            .filter((entry) => entry.score > 0)
            .sort((a, b) => b.score - a.score || a.name.length - b.name.length || a.name.localeCompare(b.name))
            .slice(0, 8)
            .map<LinkItem>(({ name }) => ({
              id: name,
              title: name.split("/").pop() ?? name,
              hint: name.includes("/") ? name : undefined,
              target: linkTargetFor(name, names),
            }));
          const typed = query.trim();
          const exists = names.some((name) => key(name) === key(typed));
          if (typed && !typed.startsWith("@") && !exists)
            scored.push({ id: `create:${typed}`, title: createLabel(typed), target: typed, create: true });
          return scored;
        },
        command: ({ editor, range, props }) => {
          editor
            .chain()
            .focus()
            .insertContentAt(range, [{ type: "wikilink", attrs: { target: props.target } }, { type: "text", text: " " }])
            .run();
        },
        render: menuRenderer<LinkItem>({ label: menuLabel, empty: emptyLabel }),
      }),
    ];
  },
});
