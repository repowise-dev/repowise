import { describe, it, expect } from "vitest";
import type { WorkspaceCoChangeEntry } from "@repowise-dev/types/workspace";
import {
  MAX_PROMPT_PAIRS,
  buildCoChangePairAiPrompt,
  buildCoChangeRepoPairAiPrompt,
} from "../../src/workspace/co-change-ai-prompt.js";
import { capRule, nearbyCommits } from "../../src/workspace/co-change-facts.js";

function pair(overrides: Partial<WorkspaceCoChangeEntry> = {}): WorkspaceCoChangeEntry {
  return {
    source_repo: "backend",
    source_file: "app/models/schemas/repos.py",
    target_repo: "frontend",
    target_file: "src/lib/api/types.ts",
    strength: 0.694,
    frequency: 3,
    last_date: "2026-09-01",
    evidence: null,
    ...overrides,
  };
}

describe("nearbyCommits", () => {
  const a = { sha: "aaaaaaaa1", date: "2026-09-01T10:00:00Z", author: "Ana", message: "feat: add field" };
  const b = { sha: "bbbbbbbb1", date: "2026-09-01T12:30:00Z", author: "ana", message: "feat: mirror field" };

  it("pairs commits by the same author inside the window", () => {
    const out = nearbyCommits([a], [b]);
    expect(out).toHaveLength(1);
    expect(out[0]!.gapHours).toBeCloseTo(2.5);
  });

  it("ignores other authors and commits outside the window", () => {
    expect(nearbyCommits([a], [{ ...b, author: "Bo" }])).toEqual([]);
    expect(nearbyCommits([a], [{ ...b, date: "2026-09-03T12:30:00Z" }])).toEqual([]);
  });

  it("keeps the closest partner and sorts newest first", () => {
    const later = { ...a, sha: "aaaaaaaa2", date: "2026-09-05T09:00:00Z" };
    const far = { ...b, sha: "bbbbbbbb2", date: "2026-09-01T20:00:00Z" };
    const near = { ...b, sha: "bbbbbbbb3", date: "2026-09-05T09:10:00Z" };
    const out = nearbyCommits([a, later], [far, b, near]);
    expect(out.map((p) => [p.source.sha, p.target.sha])).toEqual([
      ["aaaaaaaa2", "bbbbbbbb3"],
      ["aaaaaaaa1", "bbbbbbbb1"],
    ]);
  });
});

describe("buildCoChangePairAiPrompt", () => {
  it("carries both files, the counts with units, and the check-the-partner rule", () => {
    const p = buildCoChangePairAiPrompt({ pair: pair() });
    expect(p).toContain("`backend` `app/models/schemas/repos.py`");
    expect(p).toContain("`frontend` `src/lib/api/types.ts`");
    expect(p).toContain("Shared work sessions: **3**");
    expect(p).toContain("**69%**");
    expect(p).toContain("2026-09-01");
    expect(p).toContain("When you edit either file, check the other");
    expect(p).toContain("generated client");
    expect(p).toContain("not checked");
  });

  it("names a hidden coupling when no contract joins the files", () => {
    const p = buildCoChangePairAiPrompt({
      pair: pair(),
      structure: { pairLinks: [], repoLinksTotal: 180, repoLinksByType: { http: 178, code: 2 } },
    });
    expect(p).toContain("hidden coupling");
    expect(p).toContain("180 contract links (178 http, 2 code), none through these two files");
  });

  it("names the contract when one joins the files", () => {
    const p = buildCoChangePairAiPrompt({
      pair: pair(),
      structure: {
        pairLinks: [
          {
            contract_id: "http::GET::/repos/mine",
            contract_type: "http",
            provider_repo: "backend",
            provider_file: "app/models/schemas/repos.py",
            consumer_repo: "frontend",
            consumer_file: "src/lib/api/types.ts",
          },
        ],
        repoLinksTotal: 1,
        repoLinksByType: { http: 1 },
      },
    });
    expect(p).toContain("`http::GET::/repos/mine`");
    expect(p).not.toContain("hidden coupling");
  });

  it("lists reconstructed commits and file facts when given", () => {
    const p = buildCoChangePairAiPrompt({
      pair: pair(),
      sourceFacts: { commits90d: 6, churnPercentile: 73.2, priorFixes: 3, isHotspot: true },
      commits: [
        {
          source: { sha: "2b6e6c44ffff", date: "2026-08-30T19:54:46Z", message: "fix: scope\nbody", author: "Ana" },
          target: { sha: "d71154cdffff", date: "2026-08-30T19:39:33Z", message: "feat: narrow", author: "Ana" },
          gapHours: 0.25,
        },
      ],
    });
    expect(p).toContain("6 commits in 90 days, churn percentile 73, 3 prior bug fixes, a hotspot");
    expect(p).toContain('2b6e6c44 "fix: scope"');
    expect(p).toContain("under an hour apart");
    expect(p).toContain("does not record which sessions");
  });

  it("points the MCP flavor at repowise tools", () => {
    const p = buildCoChangePairAiPrompt({ pair: pair(), flavor: "claude-code-mcp" });
    expect(p).toContain("get_context(['app/models/schemas/repos.py'])");
    expect(p).toContain("get_blast_radius");
  });
});

describe("buildCoChangeRepoPairAiPrompt", () => {
  it("ranks the evidence, caps the list and names recurring files", () => {
    const pairs = Array.from({ length: MAX_PROMPT_PAIRS + 3 }, (_, i) =>
      pair({ target_file: `src/f${i}.ts`, strength: 0.3 + i / 100 }),
    );
    const p = buildCoChangeRepoPairAiPrompt({
      repo1: "backend",
      repo2: "frontend",
      pairs,
      caps: { truncatedBy: "per_repo_pair", perRepoPairCap: 18, totalCap: 200 },
    });
    expect(p).toContain("File pairs: **18** (the miner keeps the strongest 18 for each repository pair");
    expect(p).toContain("...and 3 more file pairs not listed.");
    // Strongest first.
    expect(p.indexOf("src/f17.ts")).toBeLessThan(p.indexOf("src/f16.ts"));
    expect(p).toContain("`backend` `app/models/schemas/repos.py` appears in 18 pairs");
  });

  it("does not claim a cap the list did not hit", () => {
    const p = buildCoChangeRepoPairAiPrompt({
      repo1: "a",
      repo2: "b",
      pairs: [pair()],
      caps: { truncatedBy: "per_repo_pair", perRepoPairCap: 50, totalCap: 200 },
    });
    expect(p).toContain("File pairs: **1**");
    expect(p).not.toContain("the miner keeps");
  });
});

describe("capRule", () => {
  it("names the cap the server says applied", () => {
    expect(capRule({ truncatedBy: null, perRepoPairCap: 50, totalCap: 200 })).toBeNull();
    expect(capRule({ truncatedBy: "per_repo_pair", perRepoPairCap: 50, totalCap: 200 })).toBe(
      "the miner keeps the strongest 50 for each repository pair",
    );
    expect(capRule({ truncatedBy: "total", perRepoPairCap: 50, totalCap: 200 })).toBe(
      "the miner keeps the strongest 200 across the workspace, and at most 50 for any one repository pair",
    );
  });

  it("says a workspace-wide cut may have trimmed a repository pair below its own cap", () => {
    const p = buildCoChangeRepoPairAiPrompt({
      repo1: "a",
      repo2: "b",
      pairs: [pair()],
      caps: { truncatedBy: "total", perRepoPairCap: 50, totalCap: 200 },
    });
    expect(p).toContain("File pairs: **1** (the miner keeps the strongest 200 across the workspace");
  });
});
