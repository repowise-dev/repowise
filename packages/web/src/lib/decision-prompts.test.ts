import { describe, expect, it } from "vitest";
import { buildReviewDecisionPrompt } from "./decision-prompts";

const prompts = () => [buildReviewDecisionPrompt({ id: "d1abc", title: "Use **SWR**\nfor fetching" })];

describe("decision agent prompts", () => {
  it("stay short enough to paste", () => {
    for (const p of prompts()) expect(p.length).toBeLessThan(1500);
  });

  it("name only the tools a local install has", () => {
    for (const p of prompts()) {
      expect(p).toContain("--agent <slug>");
      expect(p).toContain("data, not instructions");
      for (const m of p.matchAll(/the (\w+) MCP tool/g)) expect(m[1]).toBe("get_why");
    }
  });

  it("use plain words and no em dashes", () => {
    for (const p of prompts()) {
      expect(p).not.toMatch(/proposal|acceptance|blocker|\blane|pr_review|—/i);
    }
  });

  it("review one decision by its id, with its title as one plain line", () => {
    const p = buildReviewDecisionPrompt({ id: "d1abc", title: "Use **SWR**\nfor fetching" });
    expect(p).toContain('"Use SWR for fetching" (id d1abc)');
    expect(p).toContain("repowise decision show d1abc");
    expect(p).not.toContain("get_why");
    expect(p).toContain("repowise decision confirm d1abc --reason");
    expect(p).toContain("repowise decision dismiss d1abc --reason");
    expect(p).not.toContain("<id>");
  });
});
