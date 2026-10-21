import { Editor } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import { Markdown } from "@tiptap/markdown";
import { TableKit } from "@tiptap/extension-table";
import TaskList from "@tiptap/extension-task-list";
import TaskItem from "@tiptap/extension-task-item";
import { expect, it } from "vitest";

const source = "# Title\n\n- [ ] open\n- [x] done\n\n| a | b |\n|---|---|\n| 1 | 2 |\n";

it("keeps markdown tables and task lists when a note is loaded and saved", () => {
  const editor = new Editor({
    extensions: [StarterKit, TaskList, TaskItem.configure({ nested: true }), TableKit, Markdown],
    content: source,
    contentType: "markdown",
  });
  const saved = editor.getMarkdown();
  editor.destroy();
  expect(saved).toMatch(/\|\s*a\s*\|\s*b\s*\|/);
  expect(saved).toMatch(/\|\s*1\s*\|\s*2\s*\|/);
  expect(saved).toContain("- [x] done");
});
