import { Extension, type Editor, type Range } from "@tiptap/core";
import { PluginKey } from "@tiptap/pm/state";
import Suggestion from "@tiptap/suggestion";
import { menuRenderer, type MenuItem } from "./suggestionMenu";

export interface SlashItem extends MenuItem {
  keywords: string;
  run: (editor: Editor, range: Range) => void;
}

/** Block commands offered after typing `/`. Titles are passed in already translated. */
export function slashItems(t: (key: string) => string, extra: { pickImage?: () => void } = {}): SlashItem[] {
  type Chain = ReturnType<Editor["chain"]>;
  const block = (id: string, title: string, keywords: string, apply: (chain: Chain) => void): SlashItem => ({
    id,
    title,
    keywords,
    run: (editor, range) => apply(editor.chain().focus().deleteRange(range)),
  });
  return [
    block("h1", t("Heading 1"), "title h1 #", (c) => c.setNode("heading", { level: 1 }).run()),
    block("h2", t("Heading 2"), "subtitle h2 ##", (c) => c.setNode("heading", { level: 2 }).run()),
    block("h3", t("Heading 3"), "h3 ###", (c) => c.setNode("heading", { level: 3 }).run()),
    block("bullet", t("Bullet list"), "ul list unordered", (c) => c.toggleBulletList().run()),
    block("numbered", t("Numbered list"), "ol list ordered", (c) => c.toggleOrderedList().run()),
    block("task", t("Task list"), "todo checkbox checklist", (c) => c.toggleTaskList().run()),
    block("quote", t("Quote"), "blockquote callout", (c) => c.toggleBlockquote().run()),
    block("code", t("Code block"), "python r run snippet", (c) => c.setCodeBlock({ language: "python" }).run()),
    block("table", t("Table"), "grid rows columns", (c) => c.insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()),
    block("math", t("Math block"), "latex equation formula", (c) => c.insertBlockMath({ latex: "x^2" }).run()),
    block("divider", t("Divider"), "hr rule line", (c) => c.setHorizontalRule().run()),
    ...(extra.pickImage
      ? [block("image", t("Image"), "picture photo upload screenshot", (c) => { c.run(); extra.pickImage?.(); })]
      : []),
  ];
}

export interface SlashMenuOptions {
  getItems: () => SlashItem[];
  menuLabel: string;
  emptyLabel: string;
}

export const SlashMenu = Extension.create<SlashMenuOptions>({
  name: "slashMenu",

  addOptions() {
    return { getItems: () => [], menuLabel: "Commands", emptyLabel: "" };
  },

  addProseMirrorPlugins() {
    const { getItems, menuLabel, emptyLabel } = this.options;
    return [
      Suggestion<SlashItem, SlashItem>({
        editor: this.editor,
        pluginKey: new PluginKey("slashMenu"),
        char: "/",
        // Not inside code, where a slash is just a slash.
        allow: ({ state, range }) => !state.doc.resolve(range.from).parent.type.spec.code,
        items: ({ query }) => {
          const q = query.trim().toLowerCase();
          return getItems().filter((item) => !q || `${item.title} ${item.keywords}`.toLowerCase().includes(q));
        },
        command: ({ editor, range, props }) => props.run(editor, range),
        render: menuRenderer<SlashItem>({ label: menuLabel, empty: emptyLabel }),
      }),
    ];
  },
});
