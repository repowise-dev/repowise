import { describe, expect, it } from "vitest";
import {
  buildAddDecisionsPrompt,
  buildReviewCandidatesPrompt,
  buildReviewDecisionPrompt,
} from "./decision-prompts";

const prompts = () => [
  buildReviewCandidatesPrompt(),
  buildReviewDecisionPrompt({ id: "d1abc", title: "Use **SWR**\nfor fetching" }),
  buildAddDecisionsPrompt(),
];

describe("decision agent prompts", () => {
  it("stay short enough to paste", () => {
    for (const p of prompts()) expect(p.length).toBeLessThan(1500);
  });

  it("name only the tools a local install has", () => {
    for (const p of prompts()) {
      expect(p).toContain("get_why");
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

  it("review candidates with the CLI verbs, 5 at a time, stopping at 20", () => {
    const p = buildReviewCandidatesPrompt();
    expect(p).toContain("repowise decision show <id>");
    expect(p).toContain('repowise decision confirm <id> --reason "');
    expect(p).toMatch(/repowise decision dismiss <id> --reason "[^"]+" --agent <slug> --yes/);
    expect(p).toContain("5 at a time");
    expect(p).toContain("after 20 decisions");
  });

  it("review one decision by its id, with its title as one plain line", () => {
    const p = buildReviewDecisionPrompt({ id: "d1abc", title: "Use **SWR**\nfor fetching" });
    expect(p).toContain('"Use SWR for fetching" (id d1abc)');
    expect(p).toContain("repowise decision show d1abc");
    expect(p).toContain('get_why MCP tool with id="d1abc"');
    expect(p).toContain("repowise decision confirm d1abc --reason");
    expect(p).toContain("repowise decision dismiss d1abc --reason");
    expect(p).not.toContain("<id>");
  });

  it("add decisions from the ungoverned hotspots, with evidence, at most 10", () => {
    const p = buildAddDecisionsPrompt();
    expect(p).toContain("ungoverned_hotspots");
    expect(p).toMatch(
      /repowise decision add --title "\.\.\." --decision "\.\.\." --rationale "\.\.\." --context "[^"]+" --affects <path> --evidence-commit <sha> --agent <slug>/,
    );
    expect(p).toContain("at most 10");
    expect(p).toContain("lands as a candidate");
  });
});
