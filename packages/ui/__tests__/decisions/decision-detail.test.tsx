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
