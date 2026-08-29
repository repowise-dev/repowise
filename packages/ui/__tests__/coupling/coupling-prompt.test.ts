import { describe, it, expect } from "vitest";
import { buildCouplingAiPrompt } from "../../src/health/ai-prompt-builder.js";

const edge = {
  source: "core/answer.py",
  target: "core/config.py",
  strength: 4.16,
  last_co_change: "2026-06-01",
  support: 27,
  confidence_ab: 0.47,
  confidence_ba: 1.0,
  structural: "unexplained",
};

describe("buildCouplingAiPrompt", () => {
  it("hands the model the evidence it is asked to judge", () => {
    const prompt = buildCouplingAiPrompt({
      edge,
      repoName: "repowise",
      nodes: {
        "core/answer.py": { module: "core", score: 3.2, nloc: 900 },
        "core/config.py": { module: "core", score: 8.1, nloc: 40 },
      },
    });
    // The question is whether the coupling is accidental; the graph's verdict
    // was the one fact the prompt used to withhold.
    expect(prompt).toContain("nothing connects them");
    expect(prompt).toContain("Shared commits: **27**");
    expect(prompt).toContain("47% of A's own commits also touched B");
    expect(prompt).toContain("100% of B's also touched A");
    expect(prompt).toContain("module `core`, health 3.2/10, 900 lines");
    // `repoName` was never threaded from the call site, so this line never
    // rendered in production.
    expect(prompt).toContain("(`repowise`)");
  });

  it("says nothing about the graph when the index carries no verdict", () => {
    const prompt = buildCouplingAiPrompt({
      edge: { source: "a.py", target: "b.py", strength: 2, last_co_change: null },
    });
    expect(prompt).not.toContain("Dependency graph:");
    expect(prompt).toContain("File A: `a.py`");
  });
});
