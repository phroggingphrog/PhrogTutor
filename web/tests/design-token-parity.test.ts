import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

const css = fs.readFileSync(
  path.resolve(process.cwd(), "app/globals.css"),
  "utf8",
);
const selectors = [":root", ".dark", ".theme-snow", ".theme-glass"];
const requiredTokens = [
  "--background",
  "--foreground",
  "--card",
  "--primary",
  "--muted",
  "--destructive",
  "--success",
  "--success-surface",
  "--warning",
  "--warning-surface",
  "--info",
  "--info-surface",
  "--surface-raised",
  "--shadow-raised",
];

test("every visual theme publishes the complete semantic token contract", () => {
  for (const selector of selectors) {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const block =
      css.match(new RegExp(`${escaped}\\s*\\{([^}]+)\\}`))?.[1] ?? "";
    assert.ok(block, `missing theme block ${selector}`);
    for (const token of requiredTokens) {
      assert.match(
        block,
        new RegExp(`${token}\\s*:`),
        `${selector} is missing ${token}`,
      );
    }
  }
});

test("reduced-motion and keyboard focus policies are global", () => {
  assert.match(css, /prefers-reduced-motion:\s*reduce/);
  assert.match(css, /:focus-visible/);
  assert.match(css, /--motion-fast:\s*120ms/);
  assert.match(css, /--motion-base:\s*180ms/);
  assert.match(css, /--motion-slow:\s*240ms/);
});

test("globals.css has balanced braces (an unclosed block drops the whole stylesheet)", () => {
  const stripped = css.replace(/\/\*[\s\S]*?\*\//g, "");
  let depth = 0;
  for (const ch of stripped) {
    if (ch === "{") depth += 1;
    if (ch === "}") depth -= 1;
    assert.ok(depth >= 0, "closing brace without an opener");
  }
  assert.equal(depth, 0, "unclosed block in globals.css");
});
