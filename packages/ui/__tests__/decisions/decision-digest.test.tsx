import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { DecisionRecord } from "@repowise-dev/types/decisions";
import { DecisionDigest } from "../../src/decisions/decision-digest";
import { DecisionRecordActions } from "../../src/decisions/decision-review-lanes";

function record(overrides: Partial<DecisionRecord> = {}): DecisionRecord {
  return {
    id: "d1",
    repository_id: "r1",
    title: "Change review is a top-level page",
    status: "active",
    currency: "active",
    context: "",
    decision: "Change review lives at the top level",
    rationale: "it is visited daily",
    alternatives: [],
    consequences: [],
    affected_files: ["src/nav.ts"],
    affected_modules: [],
    tags: [],
    source: "cli",
    evidence_commits: ["e4aa4dafb1"],
    evidence_file: null,
    evidence_line: null,
    confidence: 1,
    staleness_score: 0,
    superseded_by: null,
    last_code_change: null,
    created_at: "2026-11-11T00:00:00Z",
    updated_at: "2026-11-11T00:00:00Z",
    recent: {
      at: "2026-11-11T10:00:00Z",
      signer: "claude_code",
      signer_kind: "agent",
      commit: "e4aa4dafb1234",
      landed_on: "main",
    },
    ...overrides,
  };
}

describe("DecisionDigest", () => {
  it("draws nothing for a repository that needs nobody", () => {
    const { container } = render(
      <DecisionDigest needsReview={0} ungovernedHotspots={[]} recent={[]} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("states what needs attention with measured counts, and links into the filtered list", () => {
    const onReviewNeedsReview = vi.fn();
    render(
      <DecisionDigest
        needsReview={222}
        onReviewNeedsReview={onReviewNeedsReview}
        ungovernedHotspots={["a/persistence.py", "b/http.ts", "c.ts"]}
        ungovernedAction={<button type="button">Record missed decisions</button>}
      />,
    );

    const section = within(screen.getByRole("region", { name: "Needs attention" }));
    expect(section.getByText(/decisions in force name code that changed/)).toBeInTheDocument();
    expect(section.getByText("222")).toBeInTheDocument();
    expect(section.getByText("3")).toBeInTheDocument();
    expect(section.getByText("a/persistence.py")).toBeInTheDocument();
    expect(section.getByText("+1")).toBeInTheDocument();
    expect(section.getByRole("button", { name: "Record missed decisions" })).toBeInTheDocument();

    fireEvent.click(section.getByRole("button", { name: "Review 222" }));
    expect(onReviewNeedsReview).toHaveBeenCalled();
  });

  it("leaves out a row the host could not measure", () => {
    render(<DecisionDigest needsReview={4} ungovernedHotspots={null} />);

    expect(screen.queryByText(/churn hotspot/)).toBeNull();
    expect(screen.getByText(/decisions in force name code/)).toBeInTheDocument();
  });

  it("lists this week's decisions with who put each in force and the commit", () => {
    const onShowAllRecent = vi.fn();
    render(
      <DecisionDigest
        recent={[record()]}
        recentTotal={3}
        onShowAllRecent={onShowAllRecent}
        recentHref={(d) => `/repos/r1/decisions/${d.id}`}
        renderRecentActions={(d) => <span>actions for {d.id}</span>}
      />,
    );

    const section = within(screen.getByRole("region", { name: "This week" }));
    expect(
      section.getByRole("link", { name: "Change review is a top-level page" }),
    ).toHaveAttribute("href", "/repos/r1/decisions/d1");
    expect(section.getByText("claude_code (agent)")).toBeInTheDocument();
    expect(section.getByText("e4aa4dafb")).toBeInTheDocument();
    expect(section.getByText("actions for d1")).toBeInTheDocument();

    fireEvent.click(section.getByRole("button", { name: "See all 3 in Recent" }));
    expect(onShowAllRecent).toHaveBeenCalled();
  });
});

describe("DecisionRecordActions", () => {
  it("gives a listed decision the table row's prompt and menu", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    const onPin = vi.fn();
    render(
      <DecisionRecordActions
        decision={record()}
        section="recent"
        onPin={onPin}
        loadRecordPrompt={async (_d, kind) => `${kind} prompt`}
        href="/repos/r1/decisions/d1"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Copy prompt to change it/ }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("edit prompt"));

    fireEvent.click(
      screen.getByRole("button", { name: "More actions for Change review is a top-level page" }),
    );
    const menu = within(screen.getByRole("menu"));
    // No drawer outside the table: the menu leads to the full page instead.
    expect(menu.queryByRole("menuitem", { name: "Read the record here" })).toBeNull();
    fireEvent.click(menu.getByRole("menuitem", { name: /^Pin/ }));
    expect(onPin).toHaveBeenCalledWith(expect.objectContaining({ id: "d1" }), true);
  });
});
