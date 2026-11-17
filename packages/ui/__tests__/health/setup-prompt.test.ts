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
  it("names the repo, what was found, the command and the status check", () => {
    const prompt = buildSetupAiPrompt(SYNC);
    expect(prompt).toContain(
      "repowise reports for orbit-api: Keep the index in sync. The post-commit hook is not installed, so the index updates only on `repowise update`.",
    );
    expect(prompt).toContain(
      "Run `repowise hook install` from the repository root, then run `repowise status` and tell me what it reports.",
    );
    expect(prompt).not.toContain("once I agree");
  });

  it("words a doctor finding with a fix as one ask that does not contradict itself", () => {
    const prompt = buildSetupAiPrompt({
      repoName: "orbit-api",
      title: "Post-commit hook",
      state: "not installed.",
      command: "repowise hook install",
      verify: "doctor",
      fix: true,
      fromDoctor: true,
    });
    expect(prompt).toContain(
      "`repowise doctor` in orbit-api reports: Post-commit hook, not installed. The fix is `repowise hook install`. Tell me what it changes, run it once I agree, then run `repowise doctor` again to check.",
    );
    expect(prompt).not.toMatch(/to confirm|until I confirm/);
  });

  it("asks for the cause when no single command fixes it, and lists the items", () => {
    const prompt = buildSetupAiPrompt({
      repoName: "orbit-api",
      title: "Files that could not be parsed",
      state: "3 files could not be parsed.",
      verify: "doctor",
      fix: true,
      items: ["src/legacy/report.py", "scripts/gen.py"],
    });
    expect(prompt).toContain("No single command fixes this. Find the cause");
    expect(prompt).toContain("then run `repowise doctor` to check.");
    expect(prompt).not.toContain("The fix is");
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
