import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import type { ReactElement } from "react";
import { DecisionDetail } from "../../src/decisions/decision-detail.js";
import { DecisionReviewDrawer } from "../../src/decisions/decision-review-drawer.js";
import { refusedGaps } from "../../src/decisions/decision-confirm.js";
import { acceptedLine, appliedLine } from "../../src/decisions/decision-origin.js";
import type { DecisionDetailAdapter } from "../../src/decisions/decision-detail-adapter.js";
import type { DecisionLineageEntry, DecisionRecord } from "@repowise-dev/types/decisions";

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock("sonner", () => ({
  toast: {
    success: (...a: unknown[]) => toastSuccess(...a),
    error: (...a: unknown[]) => toastError(...a),
  },
}));

// A pr_review candidate as the bot saved it before it wrote a reason.
function prReview(over: Partial<DecisionRecord> = {}): DecisionRecord {
  return {
    id: "d1abcdef99",
    repository_id: "repo-1",
    title: "Keep fixes inline in review replies",
    status: "proposed",
    context: "",
    decision: "Reply with the fix inline rather than linking a commit.",
    rationale: "",
    alternatives: [],
    consequences: [],
    affected_files: ["src/review.py"],
    affected_modules: [],
    tags: [],
    source: "pr_review",
    source_url: "https://github.com/o/r/pull/5201#discussion_r1",
    evidence_commits: ["abc1234"],
    evidence_file: null,
    evidence_line: null,
    confidence: 0.5,
    staleness_score: 0,
    superseded_by: null,
    last_code_change: null,
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
    ...over,
  };
}

function makeAdapter(over: Partial<DecisionDetailAdapter> = {}): DecisionDetailAdapter {
  return {
    cacheKey: "repo-1:d1",
    repoId: "repo-1",
    getLineage: vi.fn(async () => [] as DecisionLineageEntry[]),
    getEvidence: vi.fn(async () => []),
    listSiblingIds: vi.fn(async () => ["d1abcdef99"]),
    listModuleSuggestions: vi.fn(async () => []),
    patchDecision: vi.fn(async () => undefined),
    decisionsHref: () => "/d",
    decisionHref: (id) => `/d/${id}`,
    commitsHref: () => "/c",
    hotspotsHref: () => "/h",
    ...over,
  };
}

function renderView(node: ReactElement) {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{node}</SWRConfig>,
  );
}

beforeEach(() => {
  toastSuccess.mockClear();
  toastError.mockClear();
});

describe("confirming a decision that only lacks a reason", () => {
  it("asks why it holds, and sends the answer as the reason", async () => {
    const adapter = makeAdapter();
    renderView(<DecisionDetail decision={prReview()} adapter={adapter} />);

    const confirm = screen.getByRole("button", { name: "Confirm" });
    expect(confirm).toBeDisabled();
    expect(screen.getByText("Add a reason above to confirm.")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Why does this hold?"), {
      target: { value: "  Reviewers read the fix in place.  " },
    });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);

    await waitFor(() =>
      expect(adapter.patchDecision).toHaveBeenCalledWith({
        status: "active",
        reason: "Reviewers read the fix in place.",
      }),
    );
  });
});

describe("confirming a decision with other gaps", () => {
  it("disables Confirm and says what to fix, in plain words", () => {
    renderView(
      <DecisionDetail
        decision={prReview({ affected_files: [], evidence_commits: [] })}
        adapter={makeAdapter()}
      />,
    );

    expect(screen.getByRole("button", { name: "Confirm" })).toBeDisabled();
    expect(screen.queryByLabelText("Why does this hold?")).not.toBeInTheDocument();
    expect(screen.getByText("Before you can confirm:")).toBeInTheDocument();
    expect(screen.getByText(/names no files or modules\. Add them under Governance scope\./)).toBeInTheDocument();
    expect(screen.getByText(/repowise decision confirm d1abcdef --evidence/)).toBeInTheDocument();
    // No internal terms reach the page.
    expect(screen.queryByText(/blocker|rationale or explicit|no evidence reference/i)).not.toBeInTheDocument();
    // Dismiss stays: a decision that cannot be confirmed is the one to clear.
    expect(screen.getByRole("button", { name: "Dismiss" })).toBeEnabled();
  });
});

describe("a refused confirm", () => {
  it("lists what the server named instead of a generic error", async () => {
    const refusal = Object.assign(new Error("API error 422"), {
      detail: { message: "This decision cannot be confirmed as it stands.", blockers: ["no evidence reference"] },
    });
    const adapter = makeAdapter({ patchDecision: vi.fn(async () => Promise.reject(refusal)) });
    renderView(<DecisionDetail decision={prReview({ rationale: "Because." })} adapter={adapter} />);

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Couldn't accept this decision", expect.anything()),
    );
    expect(screen.getByText(/doesn't point to a commit or file/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm" })).toBeDisabled();
  });

  it("never locks Confirm on a refusal it cannot explain", async () => {
    const refusal = Object.assign(new Error("Signer kind is not allowed"), {
      blockers: ["Signer kind is not allowed"],
    });
    const adapter = makeAdapter({ patchDecision: vi.fn(async () => Promise.reject(refusal)) });
    renderView(<DecisionDetail decision={prReview({ rationale: "Because." })} adapter={adapter} />);

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Couldn't update decision: Signer kind is not allowed"),
    );
    expect(screen.getByRole("button", { name: "Confirm" })).toBeEnabled();
  });

  it("reads the gaps from either client's error shape", () => {
    expect(refusedGaps({ blockers: ["a"] })).toEqual(["a"]);
    expect(refusedGaps({ detail: { blockers: ["b", 3] } })).toEqual(["b"]);
    expect(refusedGaps(new Error("x"))).toEqual([]);
  });
});

describe("a decision an agent accepted", () => {
  const byAgent = (over: Partial<DecisionRecord> = {}) =>
    prReview({
      source: "commit",
      status: "active",
      currency: "active",
      rationale: "Because.",
      accepter: "claude_code",
      accepter_kind: "agent",
      ...over,
    });

  it("says your agent accepted it, with the session when one was recorded", () => {
    expect(acceptedLine(byAgent())).toBe("Accepted by your agent");
    expect(acceptedLine(byAgent({ accepter_session: " CI key " }))).toBe("Accepted by your agent (CI key)");
    // An agent signing through a GitHub identity is still an agent.
    expect(acceptedLine(byAgent({ accepter: "github:octocat" }))).toBe("Accepted by your agent");
    expect(acceptedLine(byAgent({ currency: "dismissed" }))).toBeNull();
    expect(acceptedLine(byAgent({ currency: null, status: "proposed" }))).toBeNull();
  });

  it("names the agent beside Undo on the page, for any source, and only there", () => {
    renderView(
      <DecisionDetail decision={byAgent({ accepter_session: "sess-7" })} adapter={makeAdapter()} />,
    );
    const line = screen.getByText("Accepted by your agent (sess-7)");
    expect(line.parentElement).toContainElement(screen.getByRole("button", { name: "Undo" }));
    expect(screen.getAllByText(/Accepted by your agent/)).toHaveLength(1);
    expect(screen.queryByText(/From PR review/)).not.toBeInTheDocument();
  });

  it("names the agent in the review drawer", () => {
    render(
      <DecisionReviewDrawer
        decision={byAgent()}
        reviewable={false}
        onClose={() => undefined}
        href="/d/x"
        Link="a"
      />,
    );
    expect(within(screen.getByRole("dialog")).getByText("Accepted by your agent")).toBeInTheDocument();
  });
});

describe("a decision from a PR review", () => {
  it("links to the thread and names who accepted it", () => {
    renderView(
      <DecisionDetail
        decision={prReview({
          status: "active",
          currency: "active",
          rationale: "Because.",
          accepter: "github:octocat",
          auto_accepted: true,
          applied_count: 4,
          last_applied_pr: 5201,
        })}
        adapter={makeAdapter()}
      />,
    );

    expect(screen.getByRole("link", { name: /From PR review/ })).toHaveAttribute(
      "href",
      "https://github.com/o/r/pull/5201#discussion_r1",
    );
    expect(screen.getByText("Accepted automatically from @octocat's reply")).toBeInTheDocument();
    expect(screen.getByText("Applied in 4 reviews · last on PR #5201")).toBeInTheDocument();
  });

  it("renders no mark for other sources and no applied line when absent", () => {
    renderView(<DecisionDetail decision={prReview({ source: "commit", rationale: "x" })} adapter={makeAdapter()} />);
    expect(screen.queryByText(/From PR review/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Applied in/)).not.toBeInTheDocument();
  });

  it("words the accepter and applied facts", () => {
    const base = prReview();
    expect(acceptedLine({ ...base, status: "active", accepter: "github:a-b" })).toBe("Accepted by @a-b");
    expect(acceptedLine({ ...base, status: "active", accepter: "web" })).toBeNull();
    expect(acceptedLine({ ...base, accepter: "github:a" })).toBeNull();
    expect(appliedLine({ ...base, applied_count: 1, may_be_stale: true })).toBe(
      "Applied in 1 review · may be out of date, its code changed since",
    );
    expect(appliedLine(base)).toBeNull();
  });

  it("never links a non-https source", () => {
    renderView(
      <DecisionDetail
        decision={prReview({ rationale: "x", source_url: "javascript:alert(1)" })}
        adapter={makeAdapter()}
      />,
    );
    expect(screen.queryByRole("link", { name: /From PR review/ })).not.toBeInTheDocument();
    expect(screen.getByText("From PR review")).toBeInTheDocument();
  });
});

describe("undoing an accepted decision", () => {
  it("dismisses it after saying what that does", async () => {
    const adapter = makeAdapter();
    renderView(<DecisionDetail decision={prReview({ status: "active", rationale: "x" })} adapter={adapter} />);

    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/won't be suggested again/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Dismiss" }));

    await waitFor(() => expect(adapter.patchDecision).toHaveBeenCalledWith({ status: "dismissed" }));
  });
});

describe("the review drawer", () => {
  it("takes a reason in place and passes it with Accept", () => {
    const onAccept = vi.fn();
    render(
      <DecisionReviewDrawer
        decision={prReview()}
        reviewable
        onClose={() => undefined}
        onAccept={onAccept}
        onDismiss={vi.fn()}
        href="/d/d1"
        Link="a"
      />,
    );
    const dialog = screen.getByRole("dialog");
    const accept = within(dialog).getByRole("button", { name: "Accept" });
    expect(accept).toBeDisabled();

    fireEvent.change(within(dialog).getByLabelText("Why does this hold?"), {
      target: { value: "Keeps replies short." },
    });
    fireEvent.click(accept);
    expect(onAccept).toHaveBeenCalledWith(expect.objectContaining({ id: "d1abcdef99" }), {
      reason: "Keeps replies short.",
    });
  });
});
