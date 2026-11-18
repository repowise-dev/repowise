import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import {
  DECISION_SECTIONS,
  DecisionReviewLanes,
  type DecisionSection,
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
  lane: DecisionSection,
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

describe("section structure", () => {
  it("is a digest, in order: in force, recent, background, history", () => {
    expect([...DECISION_SECTIONS]).toEqual(["in_force", "recent", "candidates", "history"]);
  });

  it("renders one tab per section in that order, with the measured counts", () => {
    renderLanes("in_force", {
      counts: { in_force: 122, recent: 4, candidates: 381, history: 2 },
    });

    const tablist = screen.getByRole("tablist");
    const tabs = within(tablist).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent?.replace(/\d+/g, ""))).toEqual([
      "In force",
      "Recent",
      "Background",
      "History",
    ]);
    expect(within(tablist).getByText("381")).toBeInTheDocument();
    expect(within(tablist).getByText("4")).toBeInTheDocument();
  });

  it("omits a badge for a section nobody counted", () => {
    renderLanes("in_force", { counts: { in_force: 3 } });

    const tablist = screen.getByRole("tablist");
    // One badge rendered, not five zeroes: a number nobody measured is worse
    // than no number.
    expect(within(tablist).queryByText("0")).not.toBeInTheDocument();
    expect(within(tablist).getByText("3")).toBeInTheDocument();
  });

  it("reports the section change rather than switching itself", () => {
    const onLaneChange = vi.fn();
    renderLanes("in_force", { onLaneChange });

    fireEvent.click(screen.getByRole("tab", { name: /Background/ }));

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

  it("says background records are not in force and agents read them through get_why", () => {
    renderLanes("candidates");

    expect(screen.getByText(/Not in force/)).toBeInTheDocument();
    expect(screen.getByText(/through get_why/)).toBeInTheDocument();
  });

  it("offers the review verbs only in the background section", () => {
    const onAccept = vi.fn();
    renderLanes("in_force", { onAccept, decisions: [record({ currency: "active" })] });
    expect(screen.queryByRole("button", { name: "Put in force" })).not.toBeInTheDocument();

    renderLanes("candidates", { onAccept });
    expect(screen.getByRole("button", { name: "Put in force" })).toBeInTheDocument();
  });

  it("hands the record back rather than writing", () => {
    const onAccept = vi.fn();
    const onDismiss = vi.fn();
    renderLanes("candidates", { onAccept, onDismiss });

    fireEvent.click(screen.getByRole("button", { name: "Put in force" }));
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

    expect(screen.getByRole("button", { name: "Putting in force…" })).toBeDisabled();
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
    expect(screen.getByRole("button", { name: "Putting in force…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Put in force" })).toBeEnabled();
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
    expect(screen.getByRole("button", { name: "Put in force" })).toBeDisabled();
    expect(screen.getByText(/names no files/)).toBeInTheDocument();
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
    expect(screen.getByRole("button", { name: "Put in force" })).toBeDisabled();
    expect(screen.getByText(/doesn't say what was chosen/i)).toBeInTheDocument();
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

    expect(screen.getByRole("button", { name: "Put in force" })).toBeEnabled();
    expect(screen.queryByText(/Cannot accept/)).not.toBeInTheDocument();
  });

  it("explains itself instead of rendering a control it cannot use", () => {
    renderLanes("candidates", {
      onAccept: vi.fn(),
      readOnlyReason: "Read-only snapshot",
    });

    expect(screen.queryByRole("button", { name: "Put in force" })).not.toBeInTheDocument();
    expect(screen.getByText("Read-only snapshot")).toBeInTheDocument();
  });
});

describe("currency marks the exception, not the default", () => {
  // Scoped to the row list: every lane name is also a tab label, so an
  // unscoped query would find the tab and pass whatever the row rendered.
  const rows = () => within(screen.getByRole("table"));

  it("says nothing extra about a decision that still describes its code", () => {
    renderLanes("in_force", { decisions: [record({ currency: "active" })] });

    expect(rows().queryByText("Needs review")).not.toBeInTheDocument();
    expect(rows().queryByText("Uncheckable")).not.toBeInTheDocument();
    expect(rows().queryByText("Active")).not.toBeInTheDocument();
  });

  it("names a currency the reader cannot guess", () => {
    renderLanes("in_force", {
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
    renderLanes("in_force", {
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
    renderLanes("in_force", {
      decisions: [
        record({
          currency: "active",
          accepter: "Raghav",
          accepter_kind: "person",
          accepter_session: "sess-42",
        }),
      ],
    });

    fireEvent.click(screen.getByText("Use JWT for authentication"));
    const drawer = within(screen.getByRole("dialog"));

    expect(drawer.getByText("Signed by Raghav, session sess-42")).toBeInTheDocument();
    expect(drawer.queryByText(/an agent|a person/)).not.toBeInTheDocument();
  });

  it("says an agent accepted it once, not again as a signer", () => {
    renderLanes("in_force", {
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

    expect(drawer.getByText("Accepted by your agent (sess-42)")).toBeInTheDocument();
    expect(drawer.queryByText(/Signed by|Signer not recorded/)).not.toBeInTheDocument();
  });

  it("says nothing about a candidate, which nobody signed", () => {
    renderLanes("candidates", { decisions: [record({ currency: null })] });

    fireEvent.click(screen.getByText("Use JWT for authentication"));

    expect(within(screen.getByRole("dialog")).queryByText(/Signed by|Signer not recorded/))
      .not.toBeInTheDocument();
  });
});

describe("reviewing the candidates with an agent", () => {
  it("copies the host's prompt from the candidates lane", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderLanes("candidates", { reviewPrompt: () => "review them" });

    fireEvent.click(screen.getByRole("button", { name: "Check with your agent" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("review them"));
  });

  it("is offered only where there are candidates and a prompt", () => {
    const first = renderLanes("candidates");
    expect(screen.queryByRole("button", { name: /with your agent/ })).not.toBeInTheDocument();
    first.unmount();

    const second = renderLanes("candidates", { reviewPrompt: () => "x", decisions: [] });
    expect(screen.queryByRole("button", { name: /with your agent/ })).not.toBeInTheDocument();
    second.unmount();

    renderLanes("in_force", { reviewPrompt: () => "x" });
    expect(screen.queryByRole("button", { name: /with your agent/ })).not.toBeInTheDocument();
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
    expect(screen.queryByText(/No background records/)).not.toBeInTheDocument();
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
    expect(screen.queryByText(/put in force as they stand come first/)).toBeNull();
    first.unmount();

    renderLanes("candidates", { order: "review" });
    expect(screen.getByText(/put in force as they stand come first/)).toBeInTheDocument();
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
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Put in force" }));

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
    renderLanes("in_force", { decisions: [drifted()], onReaffirm });

    fireEvent.click(screen.getByRole("button", { name: "Reaffirm" }));

    expect(onReaffirm).toHaveBeenCalledWith(expect.objectContaining({ id: "d1" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("offers it in the drawer too", () => {
    const onReaffirm = vi.fn();
    renderLanes("in_force", { decisions: [drifted()], onReaffirm });

    fireEvent.click(screen.getByText("Use JWT for authentication"));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Reaffirm" }));

    expect(onReaffirm).toHaveBeenCalledTimes(1);
  });

  it("is not offered on a decision whose files have not moved", () => {
    renderLanes("in_force", { decisions: [record({ currency: "active" })], onReaffirm: vi.fn() });

    expect(screen.queryByRole("button", { name: "Reaffirm" })).toBeNull();
  });
});

describe("pinning a decision in force", () => {
  const inForce = (overrides: Partial<DecisionRecord> = {}) =>
    record({ status: "active", currency: "active", ...overrides });
  const rows = () => within(screen.getByRole("table"));

  it("marks a pinned row and leaves the others quiet", () => {
    renderLanes("in_force", {
      decisions: [
        inForce({ id: "p", title: "Pinned one", pinned: true }),
        inForce({ id: "q", title: "Plain one", pinned: false }),
      ],
    });

    expect(rows().getAllByText("Pinned")).toHaveLength(1);
    // The host sends pinned rows first; the section keeps that order.
    const titles = rows()
      .getAllByText(/one$/)
      .map((el) => el.textContent);
    expect(titles).toEqual(["Pinned one", "Plain one"]);
  });

  it("offers Pin on an unpinned row and Unpin on a pinned one, handing the record back", () => {
    const onPin = vi.fn();
    renderLanes("in_force", {
      decisions: [
        inForce({ id: "p", title: "Pinned one", pinned: true }),
        inForce({ id: "q", title: "Plain one" }),
      ],
      onPin,
    });

    fireEvent.click(screen.getByRole("button", { name: "Pin" }));
    expect(onPin).toHaveBeenCalledWith(expect.objectContaining({ id: "q" }), true);
    fireEvent.click(screen.getByRole("button", { name: "Unpin" }));
    expect(onPin).toHaveBeenCalledWith(expect.objectContaining({ id: "p" }), false);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("offers it in the drawer, beside the record's agent prompts", () => {
    const onPin = vi.fn();
    renderLanes("in_force", {
      decisions: [inForce()],
      onPin,
      recordPrompts: (d) => <span>prompts for {d.id}</span>,
    });

    fireEvent.click(screen.getByText("Use JWT for authentication"));
    const drawer = within(screen.getByRole("dialog"));
    expect(drawer.getByText("prompts for d1")).toBeInTheDocument();
    fireEvent.click(drawer.getByRole("button", { name: "Pin" }));
    expect(onPin).toHaveBeenCalledWith(expect.objectContaining({ id: "d1" }), true);
  });

  it("is never offered on background records", () => {
    renderLanes("candidates", { onPin: vi.fn(), onAccept: vi.fn() });

    expect(screen.queryByRole("button", { name: "Pin" })).toBeNull();
  });
});

describe("what came into force recently", () => {
  it("says who put each one in force and the commit it landed in", () => {
    renderLanes("recent", {
      decisions: [
        record({
          status: "active",
          currency: "active",
          recent: {
            at: "2026-11-10T12:00:00Z",
            signer: "claude_code",
            signer_kind: "agent",
            commit: "abc123456789",
            landed_on: "main",
          },
        }),
      ],
    });

    expect(screen.getByRole("columnheader", { name: "By" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "In force" })).toBeInTheDocument();
    expect(screen.getByText("claude_code (agent)")).toBeInTheDocument();
    expect(screen.getByText("abc123456 on main")).toBeInTheDocument();
  });

  it("says what fills it when nothing came into force", () => {
    renderLanes("recent", { decisions: [] });

    expect(screen.getByText(/Nothing came into force in the last week/)).toBeInTheDocument();
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

    fireEvent.click(screen.getByRole("checkbox", { name: "Select every record on this page" }));
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

    fireEvent.click(screen.getByRole("checkbox", { name: "Select every record on this page" }));
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

    expect(screen.getByText(/No background records/)).toBeInTheDocument();
  });

  it("says nothing while the first page is still loading", () => {
    renderLanes("candidates", { decisions: undefined, isLoading: true });

    expect(screen.queryByText(/No background records/)).not.toBeInTheDocument();
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
    expect(screen.queryByText(/No background records/)).not.toBeInTheDocument();
    expect(screen.getByText(/Couldn't load this lane/)).toBeInTheDocument();
  });
});

describe("a record with no recorded date", () => {
  // The shared type says `string`, but a host whose schema allows a null
  // timestamp sends one, and a single such row must not take down the queue.
  const undated = () => record({ created_at: null as unknown as string });

  it("renders the row with an empty-date mark and no date tooltip", () => {
    renderLanes("candidates", { decisions: [undated()] });

    expect(screen.getByText("Use JWT for authentication")).toBeInTheDocument();
    const marks = screen.getAllByText("—");
    expect(marks.length).toBeGreaterThan(0);
    for (const mark of marks) expect(mark).not.toHaveAttribute("title");
  });

  it.each([
    ["missing", null as unknown as string],
    ["unparseable", "not a date"],
  ])("leaves the recorded date out of the drawer header when it is %s", (_, createdAt) => {
    renderLanes("candidates", { decisions: [record({ created_at: createdAt })] });

    fireEvent.click(screen.getByText("Use JWT for authentication"));
    const drawer = within(screen.getByRole("dialog"));
    expect(drawer.queryByText(/recorded/)).not.toBeInTheDocument();
  });

  it("shows the recorded date in the drawer header when there is one", () => {
    renderLanes("candidates");

    fireEvent.click(screen.getByText("Use JWT for authentication"));
    const drawer = within(screen.getByRole("dialog"));
    expect(drawer.getByText(/recorded Jan 1, 2026/)).toBeInTheDocument();
  });
});
