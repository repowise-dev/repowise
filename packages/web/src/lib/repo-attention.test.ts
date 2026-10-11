import { describe, expect, it } from "vitest";
import type { RepoSummaryRow } from "@repowise-dev/types/repos";
import { attentionSentence, byAttention } from "./repo-attention";

function repo(overrides: Partial<RepoSummaryRow> = {}): RepoSummaryRow {
  return {
    id: overrides.name ?? "id",
    name: "repo",
    local_path: "/tmp/repo",
    updated_at: null,
    status: "indexed",
    file_count: 100,
    symbol_count: 500,
    entry_point_count: 2,
    doc_page_count: 50,
    doc_fresh_page_count: 50,
    dead_export_count: 0,
    tracked_file_count: 100,
    hotspot_count: 0,
    average_health: 8,
    hotspot_health: 8,
    health_taken_at: null,
    indexed_commit: null,
    live_head: null,
    index_behind: false,
    ...overrides,
  };
}

describe("byAttention", () => {
  it("puts never-indexed repos first, then ones behind their checkout", () => {
    const rows = [
      repo({ name: "healthy" }),
      repo({ name: "behind", index_behind: true }),
      repo({ name: "unindexed", status: "needs_index", average_health: null }),
    ];

    expect(rows.sort(byAttention).map((r) => r.name)).toEqual([
      "unindexed",
      "behind",
      "healthy",
    ]);
  });

  it("orders the rest by health, worst first", () => {
    const rows = [
      repo({ name: "good", average_health: 9.1 }),
      repo({ name: "bad", average_health: 3.2 }),
      repo({ name: "middling", average_health: 6.4 }),
    ];

    expect(rows.sort(byAttention).map((r) => r.name)).toEqual(["bad", "middling", "good"]);
  });

  it("does not sort an unanalysed repo as if it scored zero", () => {
    // Absent is not zero. Ranking null at 0 would park every unanalysed repo
    // above the genuinely unhealthy one, which is the row that needs the
    // reader.
    const rows = [
      repo({ name: "unanalysed", average_health: null }),
      repo({ name: "unhealthy", average_health: 2.1 }),
    ];

    expect(rows.sort(byAttention).map((r) => r.name)).toEqual(["unhealthy", "unanalysed"]);
  });

  it("is stable on ties", () => {
    const rows = [
      repo({ name: "zebra", average_health: 7 }),
      repo({ name: "alpha", average_health: 7 }),
    ];

    expect(rows.sort(byAttention).map((r) => r.name)).toEqual(["alpha", "zebra"]);
  });
});

describe("attentionSentence", () => {
  it("names a single unindexed repo", () => {
    const text = attentionSentence([
      repo({ name: "fresh-clone", status: "needs_index", average_health: null }),
      repo({ name: "ok" }),
    ]);

    expect(text).toContain("fresh-clone has not been indexed yet.");
  });

  it("counts rather than names when several are behind", () => {
    const text = attentionSentence([
      repo({ name: "a", index_behind: true }),
      repo({ name: "b", index_behind: true }),
    ]);

    expect(text).toContain("2 are behind their working trees");
    expect(text).toContain("repowise update");
  });

  it("says so when nothing needs attention", () => {
    const text = attentionSentence([repo({ name: "a" }), repo({ name: "b" })]);

    expect(text).toContain("Every index is current with its checkout.");
  });

  it("reports the lowest score and which repo holds it", () => {
    const text = attentionSentence([
      repo({ name: "good", average_health: 9 }),
      repo({ name: "worst", average_health: 4.62 }),
    ]);

    expect(text).toContain("Lowest health score is 4.6 out of 10, in worst.");
  });

  it("never names an unanalysed repo as the lowest-scoring one", () => {
    const text = attentionSentence([
      repo({ name: "unanalysed", average_health: null }),
      repo({ name: "scored", average_health: 5.5 }),
    ]);

    expect(text).toContain("in scored.");
    expect(text).not.toContain("unanalysed.");
  });

  it("does not claim a comparison it cannot make", () => {
    const text = attentionSentence([
      repo({ name: "a", average_health: null }),
      repo({ name: "b", average_health: null }),
    ]);

    expect(text).toBe(
      "None of them has been analysed yet, so there are no health scores to compare.",
    );
  });

  it("treats an unknown freshness comparison as not behind", () => {
    // `index_behind: null` means the check could not run — no git checkout, an
    // unreadable HEAD. Reporting that as "behind" would send the reader to run
    // an update that nothing asked for.
    const text = attentionSentence([repo({ name: "a", index_behind: null })]);

    expect(text).toContain("Every index is current with its checkout.");
  });
});
