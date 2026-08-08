import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import type { ReactElement } from "react";
import { DecisionDetail } from "../../src/decisions/decision-detail.js";
import type { DecisionDetailAdapter } from "../../src/decisions/decision-detail-adapter.js";
import type {
  DecisionLineageEntry,
  DecisionRecord,
} from "@repowise-dev/types/decisions";

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock("sonner", () => ({
  toast: {
    success: (...a: unknown[]) => toastSuccess(...a),
    error: (...a: unknown[]) => toastError(...a),
  },
}));

function makeDecision(over: Partial<DecisionRecord> = {}): DecisionRecord {
  return {
    id: "d1",
    repository_id: "repo-1",
    title: "Use SWR for client fetching",
    status: "proposed",
    context: "We need a caching client.",
    decision: "Adopt SWR across dashboards.",
    rationale: "Dedupes requests and revalidates cheaply.",
    alternatives: ["React Query"],
    consequences: ["One more peer dependency"],
    affected_files: ["src/app.tsx"],
    affected_modules: ["app"],
    tags: ["frontend"],
    source: "inline_marker",
    evidence_commits: ["abcdef123456"],
    evidence_file: null,
    evidence_line: null,
    confidence: 0.8,
    staleness_score: 0,
    superseded_by: null,
    last_code_change: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
    ...over,
  };
}

function makeAdapter(over: Partial<DecisionDetailAdapter> = {}): DecisionDetailAdapter {
  return {
    cacheKey: "repo-1:d1",
    repoId: "repo-1",
    getLineage: vi.fn(async () => [] as DecisionLineageEntry[]),
    getEvidence: vi.fn(async () => []),
    listSiblingIds: vi.fn(async () => ["d1"]),
    listModuleSuggestions: vi.fn(async () => []),
    patchDecision: vi.fn(async () => undefined),
    decisionsHref: () => "/repos/repo-1/decisions",
    decisionHref: (id) => `/repos/repo-1/decisions/${id}`,
    commitsHref: (opts) =>
      `/repos/repo-1/commits${opts?.commit ? `?commit=${opts.commit}` : ""}`,
    hotspotsHref: () => "/repos/repo-1/code-health?tab=hotspots",
    ...over,
  };
}

function renderView(node: ReactElement) {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      {node}
    </SWRConfig>,
  );
}

beforeEach(() => {
  toastSuccess.mockClear();
  toastError.mockClear();
});

describe("DecisionDetail", () => {
  it("renders the record body and governance affordances", () => {
    renderView(<DecisionDetail decision={makeDecision()} adapter={makeAdapter()} />);

    expect(screen.getByRole("heading", { name: "Use SWR for client fetching" })).toBeInTheDocument();
    expect(screen.getByText("Adopt SWR across dashboards.")).toBeInTheDocument();
    expect(screen.getByText("React Query")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /All decisions/ })).toHaveAttribute(
      "href",
      "/repos/repo-1/decisions",
    );
  });

  it("confirms a proposed decision through the dialog and patches via the adapter", async () => {
    const adapter = makeAdapter();
    renderView(<DecisionDetail decision={makeDecision()} adapter={adapter} />);

    // The action button opens a confirm dialog (shares its label) — scope to it.
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm" }));

    await waitFor(() =>
      expect(adapter.patchDecision).toHaveBeenCalledWith({ status: "active" }),
    );
    expect(toastSuccess).toHaveBeenCalled();
  });

  it("renders the evolution timeline when the lineage chain is non-trivial", async () => {
    const adapter = makeAdapter({
      getLineage: vi.fn(
        async (): Promise<DecisionLineageEntry[]> => [
          { id: "d0", title: "Original", status: "deprecated", source: "cli", relation: "supersedes" },
          { id: "d1", title: "Use SWR", status: "proposed", source: "inline_marker", relation: null },
        ],
      ),
    });
    renderView(<DecisionDetail decision={makeDecision()} adapter={adapter} />);

    expect(await screen.findByText("Evolution")).toBeInTheDocument();
  });

  it("renders a dash instead of 'Invalid Date' when created_at is missing", () => {
    renderView(
      <DecisionDetail
        decision={makeDecision({ created_at: undefined as unknown as string })}
        adapter={makeAdapter()}
      />,
    );
    // The meta ribbon is a <dl>: the label is the term, the value the
    // definition, so they are no longer one "Created: —" string.
    const term = screen.getByText("Recorded");
    expect(term.nextElementSibling).toHaveTextContent("—");
    expect(screen.queryByText(/Invalid Date/i)).not.toBeInTheDocument();
  });

  it("reports what moved as a count, not as a proportion", () => {
    // Was "Staleness 42%" in a meta ribbon. A proportion has no reading at a
    // glance; the count it is a proportion of does.
    renderView(
      <DecisionDetail
        decision={makeDecision({
          staleness_score: 0.42,
          affected_files: ["a.ts", "b.ts", "c.ts", "d.ts", "e.ts", "f.ts"],
        })}
        adapter={makeAdapter()}
      />,
    );
    expect(screen.queryByText("Staleness")).not.toBeInTheDocument();
    expect(screen.queryByText("42%")).not.toBeInTheDocument();
    expect(
      screen.getByText(/3 of its 6 files have changed since it was recorded/),
    ).toBeInTheDocument();
  });

  it("says outright when a record cannot be checked at all", () => {
    // An unscoped record scores 0.0 and used to render exactly like a record
    // whose code had not moved. Two meanings, one encoding.
    renderView(
      <DecisionDetail
        decision={makeDecision({ affected_files: [], staleness_score: 0 })}
        adapter={makeAdapter()}
      />,
    );
    expect(
      screen.getByText(/names no files, so whether the code moved/),
    ).toBeInTheDocument();
  });

  it("states the quiet case rather than leaving it blank", () => {
    renderView(
      <DecisionDetail
        decision={makeDecision({
          affected_files: ["a.ts", "b.ts"],
          staleness_score: 0,
        })}
        adapter={makeAdapter()}
      />,
    );
    expect(
      screen.getByText(
        "All 2 files it names are still tracked and unchanged since it was recorded.",
      ),
    ).toBeInTheDocument();
  });

  it("drops confidence from the ribbon, since it restated the source beside it", () => {
    renderView(
      <DecisionDetail
        decision={makeDecision({ confidence: 0.844 })}
        adapter={makeAdapter()}
      />,
    );
    expect(screen.queryByText("Confidence")).not.toBeInTheDocument();
    expect(screen.queryByText("84%")).not.toBeInTheDocument();
  });

  it("explains what Confirm/Dismiss do next to the buttons", () => {
    renderView(<DecisionDetail decision={makeDecision()} adapter={makeAdapter()} />);
    expect(
      screen.getByText(/Confirm marks this as an accurate, current decision/),
    ).toBeInTheDocument();
  });

  it("offers the enforcement prompt only for active decisions", () => {
    const { unmount } = renderView(
      <DecisionDetail decision={makeDecision({ status: "active" })} adapter={makeAdapter()} />,
    );
    expect(screen.getByRole("button", { name: /Enforce this decision/ })).toBeInTheDocument();
    unmount();

    renderView(<DecisionDetail decision={makeDecision()} adapter={makeAdapter()} />);
    expect(screen.queryByRole("button", { name: /Enforce this decision/ })).not.toBeInTheDocument();
  });

  it("renders a host-supplied linked-issues slot, and nothing when omitted", () => {
    const withSlot = makeAdapter({
      renderLinkedIssues: () => <div>JIRA-123 linked</div>,
    });
    const { unmount } = renderView(
      <DecisionDetail decision={makeDecision()} adapter={withSlot} />,
    );
    expect(screen.getByText("JIRA-123 linked")).toBeInTheDocument();
    unmount();

    renderView(<DecisionDetail decision={makeDecision()} adapter={makeAdapter()} />);
    expect(screen.queryByText("JIRA-123 linked")).not.toBeInTheDocument();
  });
});
