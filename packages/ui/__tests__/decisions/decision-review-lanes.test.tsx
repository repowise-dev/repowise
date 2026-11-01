import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import {
  DECISION_LANES,
  DecisionReviewLanes,
  type DecisionLane,
} from "../../src/decisions/decision-review-lanes";
import type { DecisionRecord } from "@repowise-dev/types/decisions";

function record(overrides: Partial<DecisionRecord> = {}): DecisionRecord {
  return {
    id: "d1",
    repository_id: "r1",
    title: "Use JWT for authentication",
    status: "proposed",
    context: "",
    decision: "Issue signed JWTs",
    // Carries what the acceptance contract needs, because that is the ordinary
    // candidate: reason, scope and an evidence reference. The cases that are
    // missing one say so explicitly.
    rationale: "sessions did not survive a restart",
    alternatives: [],
    consequences: [],
    affected_files: ["src/auth/service.py"],
    affected_modules: [],
    tags: [],
    source: "pr",
    evidence_commits: ["abc1234"],
    evidence_file: null,
    evidence_line: null,
    confidence: 0.6,
    staleness_score: 0,
    superseded_by: null,
    last_code_change: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function renderLanes(
  lane: DecisionLane,
  props: Partial<React.ComponentProps<typeof DecisionReviewLanes>> = {},
) {
  return render(
    <DecisionReviewLanes
      lane={lane}
      onLaneChange={vi.fn()}
      decisions={[record()]}
      repoId="r1"
      {...props}
    />,
  );
}

describe("lane structure", () => {
  it("partitions the repository: five lanes, no overlap", () => {
    expect([...DECISION_LANES]).toEqual([
      "active",
      "candidates",
      "needs_review",
      "uncheckable",
      "history",
    ]);
    expect(new Set(DECISION_LANES).size).toBe(DECISION_LANES.length);
  });

  it("renders one tab per lane, with the measured counts", () => {
    renderLanes("active", {
      counts: {
        active: 122,
        candidates: 381,
        needs_review: 4,
        uncheckable: 57,
        history: 2,
      },
    });

    const tablist = screen.getByRole("tablist");
    expect(within(tablist).getAllByRole("tab")).toHaveLength(5);
    expect(within(tablist).getByText("381")).toBeInTheDocument();
    expect(within(tablist).getByText("57")).toBeInTheDocument();
  });

  it("omits a badge for a lane nobody counted", () => {
    renderLanes("active", { counts: { active: 3 } });

    const tablist = screen.getByRole("tablist");
    // One badge rendered, not five zeroes: a number nobody measured is worse
    // than no number.
    expect(within(tablist).queryByText("0")).not.toBeInTheDocument();
    expect(within(tablist).getByText("3")).toBeInTheDocument();
  });

  it("reports the lane change rather than switching itself", () => {
    const onLaneChange = vi.fn();
    renderLanes("active", { onLaneChange });

    fireEvent.click(screen.getByRole("tab", { name: /Candidates/ }));

    expect(onLaneChange).toHaveBeenCalledWith("candidates");
  });
});

describe("a candidate is a review request, not an instruction", () => {
  it("carries the evidence it was drawn from", () => {
    renderLanes("candidates", {
      decisions: [
        record({
          evidence_count: 3,
          evidence_preview: {
            source: "pr",
            source_quote: "we settled on JWT because sessions did not survive",
            verification: "exact",
            evidence_file: "docs/auth.md",
            evidence_line: 12,
          },
        }),
      ],
    });

    expect(
      screen.getByText(/we settled on JWT because sessions did not survive/),
    ).toBeInTheDocument();
    expect(screen.getByText(/docs\/auth\.md:12/)).toBeInTheDocument();
    expect(screen.getByText(/2 more/)).toBeInTheDocument();
  });

  it("writes where the quote came from as quiet meta, not a heading", () => {
    renderLanes("candidates", {
      decisions: [
        record({
          evidence_preview: {
            source: "pr",
            source_quote: "we settled on JWT",
            verification: "exact",
            evidence_file: "docs/auth.md",
            evidence_line: 12,
          },
        }),
      ],
    });

    const meta = screen.getByText(/docs\/auth\.md:12/);
    expect(meta).toHaveClass("text-2xs");
    expect(meta).not.toHaveClass("uppercase");
  });

  it("says it governs nothing and nobody has accepted it", () => {
    renderLanes("candidates");

    expect(screen.getByText(/nobody has accepted/i)).toBeInTheDocument();
    expect(screen.getByText(/govern nothing and reach no agent/i)).toBeInTheDocument();
  });

  it("offers the review verbs only in the candidates lane", () => {
    const onAccept = vi.fn();
    renderLanes("active", { onAccept, decisions: [record({ currency: "active" })] });
    expect(screen.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();

    renderLanes("candidates", { onAccept });
    expect(screen.getByRole("button", { name: "Accept" })).toBeInTheDocument();
  });

  it("hands the record back rather than writing", () => {
    const onAccept = vi.fn();
    const onDismiss = vi.fn();
    renderLanes("candidates", { onAccept, onDismiss });

    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    expect(onAccept).toHaveBeenCalledWith(expect.objectContaining({ id: "d1" }));

    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(onDismiss).toHaveBeenCalledWith(expect.objectContaining({ id: "d1" }));
  });

  it("disables the verbs while that row's write is in flight, and only that row", () => {
    renderLanes("candidates", {
      onAccept: vi.fn(),
      onDismiss: vi.fn(),
      pendingIds: new Set(["d1"]),
    });

    expect(screen.getByRole("button", { name: "Accepting…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Dismiss" })).toBeDisabled();
  });

  it("leaves a second row usable while the first is in flight", () => {
    renderLanes("candidates", {
      decisions: [record({ id: "d1" }), record({ id: "d2", title: "Other" })],
      onAccept: vi.fn(),
      pendingIds: new Set(["d1"]),
    });

    // One write in flight must not freeze the queue: a single pending id used
    // to disable every row, and re-enable them all when the first resolved.
    expect(screen.getByRole("button", { name: "Accepting…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Accept" })).toBeEnabled();
  });

  it("disables Accept, with the reason, on a candidate the engine would refuse", () => {
    renderLanes("candidates", {
      decisions: [
        record({
          affected_files: [],
          affected_modules: [],
          rationale: "",
          decision: "",
        }),
      ],
      onAccept: vi.fn(),
      onDismiss: vi.fn(),
    });

    // The engine refuses an acceptance with no scope, reason or evidence. The
    // row already knows all three, so the button says so before it is pressed
    // rather than reporting it in an error toast afterwards.
    expect(screen.getByRole("button", { name: "Accept" })).toBeDisabled();
    expect(screen.getByText(/no scope: name the files or modules it governs/))
      .toBeInTheDocument();
    // Dismissing stays available: an unacceptable candidate is exactly the one
    // worth tombstoning.
    expect(screen.getByRole("button", { name: "Dismiss" })).toBeEnabled();
  });

  it("disables Accept when the server filled a blank decision with the title", () => {
    renderLanes("candidates", {
      decisions: [record({ decision: "Use JWTs", decision_text_missing: true })],
      onAccept: vi.fn(),
      onDismiss: vi.fn(),
    });

    // The row reads the title fallback, but acceptance reads the stored text.
    expect(screen.getByRole("button", { name: "Accept" })).toBeDisabled();
    expect(screen.getByText(/no decision text: state what was chosen/))
      .toBeInTheDocument();
  });

  it("leaves Accept enabled on a candidate that carries what the contract needs", () => {
    renderLanes("candidates", {
      decisions: [
        record({
          rationale: "sessions did not survive a restart",
          affected_files: ["src/auth/service.py"],
          evidence_commits: ["abc1234"],
        }),
      ],
      onAccept: vi.fn(),
    });

    expect(screen.getByRole("button", { name: "Accept" })).toBeEnabled();
    expect(screen.queryByText(/Cannot accept/)).not.toBeInTheDocument();
  });

  it("explains itself instead of rendering a control it cannot use", () => {
    renderLanes("candidates", {
      onAccept: vi.fn(),
      readOnlyReason: "Read-only snapshot",
    });

    expect(screen.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
    expect(screen.getByText("Read-only snapshot")).toBeInTheDocument();
  });
});

describe("currency marks the exception, not the default", () => {
  // Scoped to the row list: every lane name is also a tab label, so an
  // unscoped query would find the tab and pass whatever the row rendered.
  const rows = () => within(screen.getByRole("table"));

  it("says nothing extra about a decision that still describes its code", () => {
    renderLanes("active", { decisions: [record({ currency: "active" })] });

    expect(rows().queryByText("Needs review")).not.toBeInTheDocument();
    expect(rows().queryByText("Uncheckable")).not.toBeInTheDocument();
    expect(rows().queryByText("Active")).not.toBeInTheDocument();
  });

  it("names a currency the reader cannot guess", () => {
    renderLanes("uncheckable", {
      decisions: [
        record({ currency: "uncheckable", affected_files: [], affected_modules: [] }),
      ],
    });

    expect(rows().getByText("Uncheckable")).toBeInTheDocument();
    // And the row says the same thing in its own words rather than only in a
    // colour: this record names nothing at all.
    expect(rows().getByText("names nothing")).toBeInTheDocument();
  });
});

describe("who signed is provenance, read in the drawer", () => {
  const rows = () => within(screen.getByRole("table"));

  it("puts no signer mark on a row, whoever signed", () => {
    renderLanes("active", {
      decisions: [
        record({ id: "a", currency: "active", accepter: "claude_code", accepter_kind: "agent" }),
        record({ id: "b", title: "Other", currency: "active", accepter: "Raghav", accepter_kind: "person" }),
        record({ id: "c", title: "Third", currency: "active", accepter: "", accepter_kind: "" }),
      ],
    });

    expect(rows().queryByText(/Signed by/)).not.toBeInTheDocument();
    expect(rows().queryByText(/Signer not recorded/)).not.toBeInTheDocument();
  });

  it("names the signer and session in the drawer, without a kind", () => {
    renderLanes("active", {
      decisions: [
        record({
          currency: "active",
          accepter: "claude_code",
          accepter_kind: "agent",
          accepter_session: "sess-42",
        }),
      ],
    });

    fireEvent.click(screen.getByText("Use JWT for authentication"));
    const drawer = within(screen.getByRole("dialog"));

    expect(drawer.getByText("Signed by claude_code, session sess-42")).toBeInTheDocument();
    expect(drawer.queryByText(/an agent|a person/)).not.toBeInTheDocument();
  });

  it("says nothing about a candidate, which nobody signed", () => {
    renderLanes("candidates", { decisions: [record({ currency: null })] });

    fireEvent.click(screen.getByText("Use JWT for authentication"));

    expect(within(screen.getByRole("dialog")).queryByText(/Signed by|Signer not recorded/))
      .not.toBeInTheDocument();
  });
});

describe("the source filter is a second axis, not a second lane control", () => {
  it("offers every live source and no retired one", () => {
    renderLanes("candidates", { onSourceChange: vi.fn() });
    const select = screen.getByLabelText("Filter by source");

    // Built from the shared list, so a source absent from this page's fifty
    // rows is still selectable.
    expect(within(select).getByRole("option", { name: "Commit" })).toBeTruthy();
    expect(within(select).getByRole("option", { name: "Model" })).toBeTruthy();
    expect(within(select).queryByRole("option", { name: "Docs" })).toBeNull();
  });

  it("reports the change rather than filtering itself", () => {
    const onSourceChange = vi.fn();
    renderLanes("candidates", { onSourceChange });

    fireEvent.change(screen.getByLabelText("Filter by source"), {
      target: { value: "pr" },
    });

    expect(onSourceChange).toHaveBeenCalledWith("pr");
  });

  it("renders no control when the host cannot act on it", () => {
    renderLanes("candidates");

    expect(screen.queryByLabelText("Filter by source")).not.toBeInTheDocument();
  });

  it("says an empty result may be the filter rather than the lane", () => {
    renderLanes("candidates", {
      decisions: [],
      source: "pr",
      onSourceChange: vi.fn(),
    });

    expect(screen.getByText(/clear the source filter/)).toBeInTheDocument();
    expect(screen.queryByText(/No candidates are waiting/)).not.toBeInTheDocument();
  });
});

describe("the queue is a table with a drawer", () => {
  it("draws the hotspot column only when the host sent the field", () => {
    const first = renderLanes("candidates");
    expect(screen.queryByRole("columnheader", { name: "Hotspots" })).toBeNull();
    first.unmount();

    renderLanes("candidates", {
      decisions: [record({ hotspot_files: ["src/auth/service.py"] })],
    });
    expect(screen.getByRole("columnheader", { name: "Hotspots" })).toBeInTheDocument();
  });

  it("states the review order only when the host fetched in it", () => {
    const first = renderLanes("candidates");
    expect(screen.queryByText(/Most in need of a look first/)).toBeNull();
    first.unmount();

    renderLanes("candidates", { order: "review" });
    expect(screen.getByText(/Most in need of a look first/)).toBeInTheDocument();
  });

  it("opens the record in a drawer and marks its hotspots", () => {
    renderLanes("candidates", {
      decisions: [record({ hotspot_files: ["src/auth/service.py"] })],
      onAccept: vi.fn(),
    });

    fireEvent.click(screen.getByText("Use JWT for authentication"));

    const drawer = screen.getByRole("dialog");
    expect(within(drawer).getByText("hotspot")).toBeInTheDocument();
    expect(
      within(drawer).getByRole("link", { name: /Open and edit the full record/ }),
    ).toHaveAttribute("href", "/repos/r1/decisions/d1");
  });

  it("moves the drawer to the next record after a verb", () => {
    const onAccept = vi.fn();
    renderLanes("candidates", {
      decisions: [record({ id: "d1" }), record({ id: "d2", title: "Second record" })],
      onAccept,
    });

    fireEvent.click(screen.getByText("Use JWT for authentication"));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Accept" }));

    expect(onAccept).toHaveBeenCalledWith(expect.objectContaining({ id: "d1" }));
    expect(
      within(screen.getByRole("dialog")).getByRole("heading", { name: "Second record" }),
    ).toBeInTheDocument();
  });

  it("does not open the drawer when a row verb is pressed", () => {
    renderLanes("candidates", { onDismiss: vi.fn() });

    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));

    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("reaffirming a decision whose files moved", () => {
  const drifted = () => record({ currency: "needs_review", staleness_score: 0.9 });

  it("offers Reaffirm on the row and hands the record back", () => {
    const onReaffirm = vi.fn();
    renderLanes("needs_review", { decisions: [drifted()], onReaffirm });

    fireEvent.click(screen.getByRole("button", { name: "Reaffirm" }));

    expect(onReaffirm).toHaveBeenCalledWith(expect.objectContaining({ id: "d1" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("offers it in the drawer too", () => {
    const onReaffirm = vi.fn();
    renderLanes("needs_review", { decisions: [drifted()], onReaffirm });

    fireEvent.click(screen.getByText("Use JWT for authentication"));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Reaffirm" }));

    expect(onReaffirm).toHaveBeenCalledTimes(1);
  });

  it("is not offered in any other lane", () => {
    renderLanes("active", { decisions: [record({ currency: "active" })], onReaffirm: vi.fn() });

    expect(screen.queryByRole("button", { name: "Reaffirm" })).toBeNull();
  });
});

describe("dismissing a selection of candidates", () => {
  const two = () => [record({ id: "d1" }), record({ id: "d2", title: "Second record" })];

  it("draws no selection without a bulk handler", () => {
    renderLanes("candidates", { decisions: two() });

    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("offers dismiss for the selection and never accept", async () => {
    const onDismissMany = vi.fn(async (ds: DecisionRecord[], onProgress: (n: number) => void) => {
      onProgress(ds.length);
      return { done: ds, failed: [] };
    });
    renderLanes("candidates", { decisions: two(), onDismissMany });

    fireEvent.click(screen.getByRole("checkbox", { name: "Select Second record" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("1 selected")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Accept 1/ })).toBeNull();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Dismiss 1" }));
    });

    expect(onDismissMany).toHaveBeenCalledWith(
      [expect.objectContaining({ id: "d2" })],
      expect.any(Function),
    );
    expect(screen.getByText("Dismissed 1 of 1.")).toBeInTheDocument();
  });

  it("names what was refused and keeps it selected for a retry", async () => {
    const onDismissMany = vi.fn(async (ds: DecisionRecord[]) => ({
      done: ds.slice(1),
      failed: [{ decision: ds[0] as DecisionRecord, message: "locked store" }],
    }));
    renderLanes("candidates", { decisions: two(), onDismissMany });

    fireEvent.click(screen.getByRole("checkbox", { name: "Select every candidate on this page" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Dismiss 2" }));
    });

    await waitFor(() =>
      expect(screen.getByText(/1 refused and still selected: Use JWT for authentication \(locked store\)/))
        .toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "Dismiss 1" })).toBeInTheDocument();
  });

  it("does not select refused rows that left the screen while it ran", async () => {
    let finish: (r: { done: DecisionRecord[]; failed: { decision: DecisionRecord; message: string }[] }) => void =
      () => {};
    const onDismissMany = vi.fn(
      (ds: DecisionRecord[]) =>
        new Promise<{ done: DecisionRecord[]; failed: { decision: DecisionRecord; message: string }[] }>(
          (resolve) => {
            finish = resolve;
            void ds;
          },
        ),
    );
    const view = renderLanes("candidates", { decisions: two(), onDismissMany });

    fireEvent.click(screen.getByRole("checkbox", { name: "Select every candidate on this page" }));
    fireEvent.click(screen.getByRole("button", { name: "Dismiss 2" }));
    // The reader pages on before the run settles.
    view.rerender(
      <DecisionReviewLanes
        lane="candidates"
        onLaneChange={vi.fn()}
        decisions={[record({ id: "d9", title: "Next page" })]}
        repoId="r1"
        onDismissMany={onDismissMany}
      />,
    );
    await act(async () => {
      finish({ done: [], failed: [{ decision: record({ id: "d1" }), message: "locked" }] });
    });

    expect(screen.queryByText(/selected$/)).toBeNull();
    expect(screen.queryByRole("button", { name: /^Dismiss \d/ })).toBeNull();
  });

  it("is off where the surface cannot write", () => {
    renderLanes("candidates", {
      decisions: two(),
      onDismissMany: vi.fn(),
      readOnlyReason: "Read-only snapshot",
    });

    expect(screen.queryByRole("checkbox")).toBeNull();
  });
});

describe("empty and failed lanes", () => {
  it("says what will fill the lane", () => {
    renderLanes("candidates", { decisions: [] });

    expect(screen.getByText(/No candidates are waiting/)).toBeInTheDocument();
  });

  it("says nothing while the first page is still loading", () => {
    renderLanes("candidates", { decisions: undefined, isLoading: true });

    expect(screen.queryByText(/No candidates are waiting/)).not.toBeInTheDocument();
  });

  it("offers a retry when the fetch failed", () => {
    const onRetry = vi.fn();
    renderLanes("candidates", {
      decisions: [],
      error: new Error("boom"),
      onRetry,
    });

    // The error replaces the lane rather than sitting beside an empty state
    // that would read as "there is nothing here".
    expect(screen.queryByText(/No candidates are waiting/)).not.toBeInTheDocument();
    expect(screen.getByText(/Couldn't load this lane/)).toBeInTheDocument();
  });
});
