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

// jsdom has no layout engine → stub ResizeObserver so the Radix slider mounts.
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
 */
const rowButton = (name: string) =>
  within(screen.getByRole("table")).getByRole("button", { name });
const queryRowButton = (name: string) => {
  const table = screen.queryByRole("table");
  return table ? within(table).queryByRole("button", { name }) : null;
};
const findRowButton = async (name: string) => {
  await screen.findByRole("table");
  return rowButton(name);
};

const SUMMARY: DeadCodeSummary = {
  total_findings: 3,
  confidence_summary: { high: 2, medium: 1, low: 0 },
  deletable_lines: 120,
  total_lines: 9000,
  by_kind: { unreachable_file: 2, unused_export: 1, zombie_package: 0 },
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

    // Summary headline (total findings) + the safe pile punchline.
    expect(await screen.findByText("Propose cleanup")).toBeInTheDocument();
    expect(screen.getByText("lines in cleanup candidates")).toBeInTheDocument();
    // The safe slice (not the unsafe bootstrap file) drives the pile preview.
    expect(screen.getByText(/legacy\.ts/)).toBeInTheDocument();
  });

  it("Re-analyze calls the adapter and shows a success toast", async () => {
    const adapter = makeAdapter();
    renderView(<DeadCodeView adapter={adapter} />);

    fireEvent.click(screen.getByRole("button", { name: "Re-analyze" }));

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
    expect(screen.queryByRole("button", { name: /All findings/ })).not.toBeInTheDocument();
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
    fireEvent.change(await screen.findByLabelText("Status"), {
      target: { value: "acknowledged" },
    });

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
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "open" } });

    expect(screen.queryByText("No dead code found")).not.toBeInTheDocument();
    // It is cleanup-ready, so a pile now exists and the section mounts closed.
    fireEvent.click(await screen.findByRole("button", { name: /All findings/ }));
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

    fireEvent.click(await screen.findByRole("button", { name: /All findings/ }));
    expect(await findRowButton("Resolve src/old/legacy.ts")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Re-analyze" }));

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
    await screen.findByText("lines in cleanup candidates");

    const listCallsBefore = (adapter.listFindings as ReturnType<typeof vi.fn>).mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Re-analyze" }));

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

    fireEvent.click(screen.getByRole("button", { name: "Re-analyze" }));

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

    fireEvent.click(screen.getByRole("button", { name: "Re-analyze" }));

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(String(toastError.mock.calls[0]?.[0])).toMatch(/Another job is already running/);
  });

  it("undo puts the row back on screen, not just in the database", async () => {
    const adapter = makeAdapter();
    renderView(<DeadCodeView adapter={adapter} />);

    // Open the drill-down table (collapsed by default).
    fireEvent.click(await screen.findByRole("button", { name: /All findings/ }));
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
});
