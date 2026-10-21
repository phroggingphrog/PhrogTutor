import { Editor } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import { Markdown } from "@tiptap/markdown";
import { expect, it } from "vitest";
import { Wikilink, linkTargetFor, resolveWikilink } from "@/components/notes/wikilinks";

// The same cases as tests/api/test_notes_router.py::test_resolve_link_rules: the editor and the
// backend must agree on which note a link points at.
it("resolves links like the backend does", () => {
  const names = ["Physics/Waves", "Waves", "@wiki/Waves", "Math/Fourier"];
  expect(resolveWikilink("Waves", names)).toBe("Waves");
  expect(resolveWikilink("physics/waves.md", names)).toBe("Physics/Waves");
  expect(resolveWikilink("fourier", names)).toBe("Math/Fourier");
  expect(resolveWikilink("Nope", names)).toBeNull();
  expect(resolveWikilink("@wiki/Waves", names)).toBe("@wiki/Waves");
  expect(resolveWikilink("Waves", ["@wiki/Waves", "a/b/Waves"])).toBe("a/b/Waves");
  expect(resolveWikilink("Folder/Missing", ["Missing"])).toBeNull();
});

it("links by bare name only when it is unambiguous", () => {
  expect(linkTargetFor("Physics/Waves", ["Physics/Waves", "Math/Fourier"])).toBe("Waves");
  expect(linkTargetFor("Physics/Waves", ["Physics/Waves", "Other/Waves"])).toBe("Physics/Waves");
});

it("round-trips wikilinks, aliases and headings through markdown", () => {
  const source = "See [[Physics/Waves]], [[Waves|the waves]] and [[Fourier#Intro]].\n\nNot a link: `[[code]]` and [plain](http://x.test).\n";
  const editor = new Editor({
    extensions: [StarterKit, Wikilink, Markdown],
    content: source,
    contentType: "markdown",
  });
  const links = editor.getJSON().content?.[0].content?.filter((n) => n.type === "wikilink") ?? [];
  const saved = editor.getMarkdown();
  editor.destroy();
  expect(links.map((n) => (n as { attrs?: unknown }).attrs)).toEqual([
    { target: "Physics/Waves", heading: "", alias: "" },
    { target: "Waves", heading: "", alias: "the waves" },
    { target: "Fourier", heading: "Intro", alias: "" },
  ]);
  expect(saved).toContain("[[Physics/Waves]]");
  expect(saved).toContain("[[Waves|the waves]]");
  expect(saved).toContain("[[Fourier#Intro]]");
  expect(saved).toContain("`[[code]]`");
  expect(saved).toContain("[plain](http://x.test)");
});
