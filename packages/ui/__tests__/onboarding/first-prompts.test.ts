import { describe, expect, it } from "vitest";
import { FIRST_PROMPTS, firstPrompts, workspacePrompt } from "../../src/onboarding/first-prompts.js";

describe("firstPrompts", () => {
  it("names the repo and the facts it was given", () => {
    const [tour, risk, why] = firstPrompts("orbit", {
      hotspot: "src/api.py",
      decision: "Use SQLite",
    });
    expect(tour).toBe(
      "Use Repowise to give me a tour of orbit: what it does, its main modules, and where I should start reading.",
    );
    expect(risk).toContain("Before I change src/api.py, use Repowise");
    expect(why).toBe(
      'Use Repowise to explain the decision "Use SQLite" in orbit: why it was made and which code follows it.',
    );
  });

  it("falls back to the worst file, then to questions any repo can answer", () => {
    expect(firstPrompts("orbit", { worst: "src/legacy.py" })[1]).toContain(
      "src/legacy.py is the least healthy file in orbit",
    );
    const [, risk, why] = firstPrompts("orbit");
    expect(risk).toBe(FIRST_PROMPTS.risky.replace("{repo}", "orbit"));
    expect(why).toBe(FIRST_PROMPTS.unused.replace("{repo}", "orbit"));
  });

  it("names Repowise in every prompt, so the agent reaches for its tools", () => {
    for (const template of Object.values(FIRST_PROMPTS)) expect(template).toContain("Repowise");
  });
});

describe("workspacePrompt", () => {
  it("names up to three repos, counts more, and tours a lone repo", () => {
    expect(workspacePrompt(["a", "b"])).toContain("show me how a and b depend on each other");
    expect(workspacePrompt(["a", "b", "c", "d"])).toContain("the 4 repos in this workspace");
    expect(workspacePrompt(["a"])).toContain("give me a tour of a");
  });
});
