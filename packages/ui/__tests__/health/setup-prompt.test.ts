import { describe, expect, it } from "vitest";
import { buildSetupAiPrompt, type SetupPromptRow } from "../../src/health/ai-prompt-builder.js";

const SYNC: SetupPromptRow = {
  repoName: "orbit-api",
  title: "Keep the index in sync",
  state: "The post-commit hook is not installed, so the index updates only on `repowise update`.",
  command: "repowise hook install",
  verify: "status",
};

describe("buildSetupAiPrompt", () => {
  it("carries the repo, the detected state, the exact command and the status check", () => {
    const prompt = buildSetupAiPrompt(SYNC);
    expect(prompt).toContain("## Setup: Keep the index in sync (`orbit-api`)");
    expect(prompt).toContain(`What repowise detected: ${SYNC.state}`);
    expect(prompt).toContain("```\nrepowise hook install\n```");
    expect(prompt).toContain("`repowise status`");
    expect(prompt).not.toContain("Confirm with me before changing anything");
  });

  it("asks a fix to confirm before changing anything and verifies with doctor", () => {
    const prompt = buildSetupAiPrompt({
      ...SYNC,
      title: "Index",
      state: "3 files could not be parsed.",
      command: "repowise doctor --format json",
      verify: "doctor",
      fix: true,
      items: ["src/legacy/report.py", "scripts/gen.py"],
    });
    expect(prompt).toContain("Confirm with me before changing anything");
    expect(prompt).toContain("Find out why first");
    expect(prompt).toContain("`repowise doctor --format json` afterwards");
    expect(prompt).toContain("- `src/legacy/report.py`\n- `scripts/gen.py`");
  });

  it("lists at most 20 items and counts the rest", () => {
    const items = Array.from({ length: 23 }, (_, i) => `f${i}.py`);
    const prompt = buildSetupAiPrompt({ ...SYNC, items });
    expect(prompt).toContain("- `f19.py`");
    expect(prompt).not.toContain("`f20.py`");
    expect(prompt).toContain("- and 3 more");
  });

  it("opens with the flavor's role", () => {
    expect(buildSetupAiPrompt(SYNC, "claude-code").startsWith("You are Claude Code")).toBe(true);
  });
});
