import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import type { ReactElement } from "react";
import { DeadCodeView } from "../../src/dead-code/dead-code-view.js";
import type { DeadCodeAdapter } from "../../src/dead-code/dead-code-adapter.js";
import type {
  DeadCodeFinding,
  DeadCodeSummary,
} from "@repowise-dev/types/dead-code";

// jsdom has no layout engine → stub ResizeObserver for the Radix primitives.
class RO {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", RO);

// Capture sonner toasts so we can assert the undo affordance without a Toaster.
const toastSuccess = vi.fn();
const toastError = vi.fn();
const toastWarning = vi.fn();
vi.mock("sonner", () => ({
  toast: {
    success: (...a: unknown[]) => toastSuccess(...a),
    error: (...a: unknown[]) => toastError(...a),
    warning: (...a: unknown[]) => toastWarning(...a),
  },
}));

/**
 * The findings table stacks into a mobile card list beside the real table and
 * lets CSS pick one; jsdom applies no CSS, so a row control matches in both.
 * These scope row queries to the table.
 *
 * By caption, so a second <table> elsewhere on the page cannot match.
 */
const FINDINGS_TABLE = { name: "Dead code findings" };
const rowButton = (name: string) =>
  within(screen.getByRole("table", FINDINGS_TABLE)).getByRole("button", { name });
const queryRowButton = (name: string) => {
  const table = screen.queryByRole("table", FINDINGS_TABLE);
  return table ? within(table).queryByRole("button", { name }) : null;
};
const findRowButton = async (name: string) => {
  await screen.findByRole("table", FINDINGS_TABLE);
  return rowButton(name);
};

const SUMMARY: DeadCodeSummary = {
  total_findings: 3,
  confidence_summary: { high: 2, medium: 1, low: 0 },
  deletable_lines: 120,
  total_lines: 9000,
  by_kind: { unreachable_file: 2, unused_export: 1, zombie_package: 0 },
  analyzed_at: "2026-10-08T12:00:00Z",
};

const CLEAN: DeadCodeSummary = {
  total_findings: 0,
  confidence_summary: { high: 0, medium: 0, low: 0 },
  deletable_lines: 0,
  total_lines: 0,
  by_kind: {},
  analyzed_at: "2026-10-08T12:00:00Z",
};

const FINDINGS: DeadCodeFinding[] = [
  {
    id: "f1",
    kind: "unreachable_file",
    file_path: "src/old/legacy.ts",
    symbol_name: null,
    symbol_kind: null,
    confidence: 0.95,
    reason: "No importers",
    lines: 80,
    safe_to_delete: true,
    risk_factors: [],
    primary_owner: "alice",
    status: "open",
    note: null,
  },
  {
    id: "f2",
    kind: "unused_export",
    file_path: "src/util/helpers.ts",
    symbol_name: "unusedHelper",
    symbol_kind: "function",
    confidence: 0.88,
    reason: "Export never imported",
    lines: 40,
    safe_to_delete: true,
    risk_factors: [],
    primary_owner: "bob",
    status: "open",
    note: null,
  },
  {
    id: "f3",
    kind: "unreachable_file",
    file_path: "src/boot/init.ts",
    symbol_name: null,
    symbol_kind: null,
    confidence: 0.6,
    reason: "Possibly bootstrap-loaded",
    lines: 30,
    safe_to_delete: false,
    risk_factors: ["bootstrap"],
    primary_owner: "alice",
    status: "open",
    note: null,
  },
];

function makeAdapter(over: Partial<DeadCodeAdapter> = {}): DeadCodeAdapter {
  return {
    cacheKey: "repo-1",
    repoId: "repo-1",
    getSummary: vi.fn(async () => SUMMARY),
    listFindings: vi.fn(async () => FINDINGS),
    analyze: vi.fn(async () => undefined),
    patchFinding: vi.fn(async (id, patch) => {
      const base = FINDINGS.find((f) => f.id === id)!;
      return { ...base, status: patch.status };
    }),
    fileHref: (p) => `/repos/repo-1/files/${p}`,
    navigate: vi.fn(),
    ...over,
  };
}

// Fresh SWR cache per render so view-level keys don't bleed across tests.
function renderView(node: ReactElement) {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      {node}
    </SWRConfig>,
  );
}

/**
 * The most recent toast that actually carries an Undo action. Selecting by
 * shape rather than by position keeps this immune to an unrelated toast (a
 * refresh notice, say) landing last.
 */
function lastUndoAction(): { label: string; onClick: () => Promise<void> } {
  const call = toastSuccess.mock.calls
    .filter((c) => (c[1] as { action?: unknown } | undefined)?.action)
    .at(-1);
  const action = (call?.[1] as { action: { label: string; onClick: () => Promise<void> } }).action;
  expect(action.label).toBe("Undo");
  return action;
}

beforeEach(() => {
  toastSuccess.mockClear();
  toastError.mockClear();
  toastWarning.mockClear();
});

describe("DeadCodeView", () => {
  it("renders the summary and the safe-to-delete pile from adapter data", async () => {
    renderView(<DeadCodeView adapter={makeAdapter()} />);

    // The lede's figure, then the safe pile's own sentence.
    expect(await screen.findByText("Propose cleanup")).toBeInTheDocument();
    expect(screen.getByText(/with no runtime-load risk/)).toBeInTheDocument();
    // The safe slice (not the unsafe bootstrap file) drives the pile preview.
    expect(screen.getAllByText(/legacy\.ts/).length).toBeGreaterThan(0);
  });

  it("Re-analyze calls the adapter and shows a success toast", async () => {
    const adapter = makeAdapter();
    renderView(<DeadCodeView adapter={adapter} />);

    fireEvent.click(await screen.findByRole("button", { name: "Re-analyze" }));

    await waitFor(() => expect(adapter.analyze).toHaveBeenCalledTimes(1));
    expect(toastSuccess).toHaveBeenCalled();
  });

  it("surfaces a retry affordance when the summary fails to load", async () => {
    const adapter = makeAdapter({
      getSummary: vi.fn(async () => {
        throw new Error("boom");
      }),
    });
    renderView(<DeadCodeView adapter={adapter} />);

    expect(await screen.findByText("Couldn't load summary")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("opens the AI cleanup prompt seeded with the safe pile", async () => {
    renderView(<DeadCodeView adapter={makeAdapter()} />);

    fireEvent.click(await screen.findByText("Propose cleanup"));

    expect(await screen.findByText("AI cleanup prompt")).toBeInTheDocument();
  });

  it("surfaces a retry affordance when the findings fetch fails", async () => {
    const adapter = makeAdapter({
      listFindings: vi.fn(async () => {
        throw new Error("boom");
      }),
    });
    renderView(<DeadCodeView adapter={adapter} />);

    // A failed fetch must never render as a clean repository, so the retry
    // card has to be the whole story: no table, no "no findings" underneath it.
    expect(await screen.findByText("Couldn't load findings")).toBeInTheDocument();
    expect(screen.queryByText("No dead code found")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/No open dead-code findings/),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("table", FINDINGS_TABLE)).not.toBeInTheDocument();
  });

  it("reviews and reopens an acknowledged finding, which the toast alone could not", async () => {
    const acked: DeadCodeFinding = { ...FINDINGS[0]!, status: "acknowledged" };
    const listFindings = vi.fn(async (opts?: { status?: string }) =>
      opts?.status === "acknowledged" ? [acked] : [],
    );
    const adapter = makeAdapter({ listFindings });
    renderView(<DeadCodeView adapter={adapter} />);

    // With no open findings the page still has to offer the way back in, not
    // just the "no dead code found" state.
    fireEvent.click(await screen.findByRole("radio", { name: "Acknowledged" }));

    await waitFor(() =>
      expect(listFindings).toHaveBeenCalledWith(
        expect.objectContaining({ status: "acknowledged" }),
      ),
    );

    fireEvent.click(await findRowButton("Reopen src/old/legacy.ts"));
    fireEvent.click(await screen.findByRole("button", { name: "Reopen" }));

    await waitFor(() =>
      expect(adapter.patchFinding).toHaveBeenCalledWith("f1", { status: "open" }),
    );
    // Reopened, so it leaves the acknowledged slice it was being reviewed in.
    await waitFor(() =>
      expect(queryRowButton("Reopen src/old/legacy.ts")).not.toBeInTheDocument(),
    );

    // ...and it has to arrive in the open slice. The open payload predates the
    // reopen and does not contain it, so without merging the override in, the
    // finding is open on the server and invisible in the UI until a reload.
    fireEvent.click(screen.getByRole("radio", { name: "Open" }));

    expect(screen.queryByText("No dead code found")).not.toBeInTheDocument();
    expect(await findRowButton("Resolve src/old/legacy.ts")).toBeInTheDocument();
  });

  it("keeps the table when a refresh fails over rows already on screen", async () => {
    let calls = 0;
    const listFindings = vi.fn(async () => {
      calls += 1;
      if (calls > 1) throw new Error("Service unavailable");
      return FINDINGS;
    });
    const adapter = makeAdapter({
      listFindings,
      analyze: vi.fn(async () => ({ job_id: "job-1" })),
      waitForAnalysis: vi.fn(async () => {}),
    });
    renderView(<DeadCodeView adapter={adapter} />);

    expect(await findRowButton("Resolve src/old/legacy.ts")).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "Re-analyze" }));

    // A transient failure on the refresh must not take the working table away
    // and lose the user's place; it reports itself above the rows.
    expect(await screen.findByText("Couldn't refresh findings")).toBeInTheDocument();
    expect(rowButton("Resolve src/old/legacy.ts")).toBeInTheDocument();
  });

  it("keeps the table mounted when the last row is resolved, so undo can restore it", async () => {
    const only: DeadCodeFinding = { ...FINDINGS[0]!, safe_to_delete: false };
    const adapter = makeAdapter({ listFindings: vi.fn(async () => [only]) });
    renderView(<DeadCodeView adapter={adapter} />);

    // No safe pile above it, so the section leads the page already open.
    fireEvent.click(await findRowButton("Resolve src/old/legacy.ts"));
    fireEvent.click(await screen.findByRole("button", { name: "Resolve" }));

    // Emptying the list locally must not swap in the "No dead code found"
    // state: that unmounts the section, and undo would restore the row into a
    // section that remounted collapsed.
    await waitFor(() =>
      expect(
        queryRowButton("Resolve src/old/legacy.ts"),
      ).not.toBeInTheDocument(),
    );
    expect(screen.queryByText("No dead code found")).not.toBeInTheDocument();
    // The row leaves and the resolved state arrives in separate renders.
    expect(await screen.findByText("Every finding is resolved or set aside")).toBeInTheDocument();

    const undo = lastUndoAction();
    await undo.onClick();

    await waitFor(() =>
      expect(rowButton("Resolve src/old/legacy.ts")).toBeInTheDocument(),
    );
  });

  it("waits for the analysis job and refetches when it finishes", async () => {
    let resolveJob: (() => void) | undefined;
    const adapter = makeAdapter({
      analyze: vi.fn(async () => ({ job_id: "job-1" })),
      waitForAnalysis: vi.fn(
        () =>
          new Promise<void>((res) => {
            resolveJob = res;
          }),
      ),
    });
    renderView(<DeadCodeView adapter={adapter} />);
    await screen.findByText(/with no runtime-load risk/);

    const listCallsBefore = (adapter.listFindings as ReturnType<typeof vi.fn>).mock.calls.length;
    fireEvent.click(await screen.findByRole("button", { name: "Re-analyze" }));

    await waitFor(() => expect(adapter.waitForAnalysis).toHaveBeenCalledWith("job-1"));
    resolveJob?.();

    // Without this the toast promised fresh results over an unchanged fetch.
    await waitFor(() =>
      expect(
        (adapter.listFindings as ReturnType<typeof vi.fn>).mock.calls.length,
      ).toBeGreaterThan(listCallsBefore),
    );
    // Settle the refresh before the test ends: leaving it in flight lets a
    // late toast land inside a later test and pick up its assertions.
    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith("Dead-code findings refreshed."),
    );
    expect((adapter.getSummary as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(1);
  });

  it("blames the watch, not the launch, when the job fails after it started", async () => {
    const adapter = makeAdapter({
      analyze: vi.fn(async () => ({ job_id: "job-1" })),
      waitForAnalysis: vi.fn(async () => {
        throw new Error("Gateway timeout");
      }),
    });
    renderView(<DeadCodeView adapter={adapter} />);

    fireEvent.click(await screen.findByRole("button", { name: "Re-analyze" }));

    // The job is running; "Couldn't start analysis" would be a false statement.
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(String(toastError.mock.calls[0]?.[0])).toMatch(/stopped tracking it/);
    expect(String(toastError.mock.calls[0]?.[0])).not.toMatch(/start analysis/);
  });

  it("reports a 409 as another job running rather than a generic failure", async () => {
    const conflict = Object.assign(new Error("A job is already in progress"), { status: 409 });
    const adapter = makeAdapter({
      analyze: vi.fn(async () => {
        throw conflict;
      }),
    });
    renderView(<DeadCodeView adapter={adapter} />);

    fireEvent.click(await screen.findByRole("button", { name: "Re-analyze" }));

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(String(toastError.mock.calls[0]?.[0])).toMatch(/Another job is already running/);
  });

  it("undo puts the row back on screen, not just in the database", async () => {
    const adapter = makeAdapter();
    renderView(<DeadCodeView adapter={adapter} />);

    fireEvent.click(await findRowButton("Resolve src/old/legacy.ts"));
    fireEvent.click(await screen.findByRole("button", { name: "Resolve" }));

    // Optimistically gone from the table.
    await waitFor(() =>
      expect(
        queryRowButton("Resolve src/old/legacy.ts"),
      ).not.toBeInTheDocument(),
    );

    // The toast's Undo action re-patches; the row has to come back with it.
    const undo = lastUndoAction();
    await undo.onClick();

    await waitFor(() =>
      expect(rowButton("Resolve src/old/legacy.ts")).toBeInTheDocument(),
    );
  });

  it("calls a recorded zero clean, with no table or sections under it", async () => {
    renderView(
      <DeadCodeView
        adapter={makeAdapter({
          getSummary: vi.fn(async () => CLEAN),
          listFindings: vi.fn(async () => []),
        })}
      />,
    );

    const clean = await screen.findByText("No dead code found");
    expect(clean.closest("[data-tone]")).toHaveAttribute("data-tone", "positive");
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(screen.queryByText("All findings")).not.toBeInTheDocument();
    expect(screen.queryByText("Safe to delete")).not.toBeInTheDocument();
  });

  it("reviews past findings from the clean state", async () => {
    const listFindings = vi.fn(async () => [] as DeadCodeFinding[]);
    renderView(
      <DeadCodeView
        adapter={makeAdapter({ getSummary: vi.fn(async () => CLEAN), listFindings })}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Review past findings" }));

    expect(await screen.findByText("All findings")).toBeInTheDocument();
    await waitFor(() =>
      expect(listFindings).toHaveBeenCalledWith(expect.objectContaining({ status: "resolved" })),
    );
  });

  it("never calls an unrecorded zero clean", async () => {
    const adapter = makeAdapter({
      getSummary: vi.fn(async () => ({ ...CLEAN, analyzed_at: null })),
      listFindings: vi.fn(async () => []),
    });
    renderView(<DeadCodeView adapter={adapter} />);

    expect(await screen.findByText("Not analysed yet")).toBeInTheDocument();
    expect(screen.queryByText("No dead code found")).not.toBeInTheDocument();
    expect(document.querySelector('[data-tone="positive"]')).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));
    await waitFor(() => expect(adapter.analyze).toHaveBeenCalledTimes(1));
  });

  it("renders Re-analyze disabled with the host's reason beside it", async () => {
    const adapter = makeAdapter({ analyzeDisabledReason: "This snapshot is read-only." });
    renderView(<DeadCodeView adapter={adapter} />);

    const button = await screen.findByRole("button", { name: "Re-analyze" });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription("This snapshot is read-only.");
    expect(screen.getByText("This snapshot is read-only.")).toBeVisible();
  });

  it("switches the table's slice from the segmented status control", async () => {
    const listFindings = vi.fn(async () => FINDINGS);
    renderView(<DeadCodeView adapter={makeAdapter({ listFindings })} />);

    expect(await screen.findByRole("radio", { name: "Open" })).toHaveAttribute("aria-checked", "true");
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: "False positive" }));
    await waitFor(() =>
      expect(listFindings).toHaveBeenCalledWith(
        expect.objectContaining({ status: "false_positive" }),
      ),
    );
  });
});
