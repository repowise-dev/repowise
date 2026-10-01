/**
 * The web prompt builders and core's `repowise.core.agent_prompts` render the
 * same bytes. Both sides check `tests/fixtures/agent_prompts/golden.json`; this
 * side also writes it.
 *
 * Regenerate after a deliberate wording change (from packages/ui):
 *   UPDATE_AGENT_PROMPT_GOLDENS=1 npx vitest run __tests__/health/agent-prompt-parity.test.ts
 * then run tests/unit/agent_prompts/test_parity.py, which must pass unchanged.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { NextAction } from "@repowise-dev/types/actions";
import type { FixItem } from "@repowise-dev/types/fix-first";

import { buildActionPrompt } from "../../src/health/ai-prompts/action-prompt";
import { buildFixItemPrompt, fixVerifyLines } from "../../src/health/ai-prompts/fix-first-prompt";
import type { AiPromptFlavor } from "../../src/health/ai-prompts/shared";

const DIR = join(__dirname, "../../../../tests/fixtures/agent_prompts");
const FLAVORS: AiPromptFlavor[] = ["generic", "claude-code", "claude-code-mcp", "cursor"];

interface Case {
  name: string;
  repo_name?: string;
}

function read<T>(file: string): T {
  return JSON.parse(readFileSync(join(DIR, file), "utf8")) as T;
}

const actions = read<{ cases: (Case & { action: NextAction })[] }>("actions.json").cases;
const items = read<{ cases: (Case & { item: FixItem })[] }>("fix_items.json").cases;

function perFlavor(render: (flavor: AiPromptFlavor) => string): Record<string, string> {
  return Object.fromEntries(FLAVORS.map((f) => [f, render(f)]));
}

const repo = (c: Case) => (c.repo_name ? { repoName: c.repo_name } : {});

const rendered = {
  action: Object.fromEntries(
    actions.map((c) => [
      c.name,
      perFlavor((flavor) => buildActionPrompt({ action: c.action, flavor, ...repo(c) })),
    ]),
  ),
  fix_item: Object.fromEntries(
    items.map((c) => [
      c.name,
      perFlavor((flavor) => buildFixItemPrompt({ item: c.item, flavor, ...repo(c) })),
    ]),
  ),
  verify: Object.fromEntries(items.map((c) => [c.name, fixVerifyLines(c.item)])),
};

describe("agent prompt goldens", () => {
  it("the web builders render golden.json", () => {
    if (process.env.UPDATE_AGENT_PROMPT_GOLDENS) {
      writeFileSync(join(DIR, "golden.json"), `${JSON.stringify(rendered, null, 2)}\n`, "utf8");
    }
    expect(rendered).toEqual(read("golden.json"));
  });
});
