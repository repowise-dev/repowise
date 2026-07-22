import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
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

    expect(await screen.findByText("Couldn't load summary.")).toBeInTheDocument();
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

    // A failed fetch must never render as a clean repository.
    expect(await screen.findByText("Couldn't load findings.")).toBeInTheDocument();
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
    expect(adapter.getSummary).toHaveBeenCalledTimes(2);
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
    fireEvent.click(await screen.findByRole("button", { name: "Resolve src/old/legacy.ts" }));
    fireEvent.click(await screen.findByRole("button", { name: "Resolve" }));

    // Optimistically gone from the table.
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Resolve src/old/legacy.ts" }),
      ).not.toBeInTheDocument(),
    );

    // The toast's Undo action re-patches; the row has to come back with it.
    const undo = toastSuccess.mock.calls.at(-1)?.[1]?.action;
    expect(undo?.label).toBe("Undo");
    await undo.onClick();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Resolve src/old/legacy.ts" })).toBeInTheDocument(),
    );
  });
});
