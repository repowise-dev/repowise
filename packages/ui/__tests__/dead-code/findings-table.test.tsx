import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { FindingsTable } from "../../src/dead-code/findings-table.js";
import type { DeadCodeFinding } from "@repowise-dev/types/dead-code";

// jsdom has no layout engine → stub ResizeObserver so the Radix slider mounts.
class RO {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", RO);

const toastError = vi.fn();
vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: (...a: unknown[]) => toastError(...a),
    warning: vi.fn(),
  },
}));

function finding(over: Partial<DeadCodeFinding> & { id: string }): DeadCodeFinding {
  return {
    kind: "unreachable_file",
    file_path: `src/${over.id}.ts`,
    symbol_name: null,
    symbol_kind: null,
    confidence: 0.9,
    reason: "No importers",
    lines: 10,
    safe_to_delete: true,
    risk_factors: [],
    primary_owner: "alice",
    status: "open",
    note: null,
    ...over,
  };
}

const noopPatch = vi.fn(async (id: string) => finding({ id, status: "resolved" }));

const noopBulk = vi.fn(async (ids: string[]) => ids);

function renderTable(findings: DeadCodeFinding[], props: Record<string, unknown> = {}) {
  return render(
    <FindingsTable
      findings={findings}
      onPatch={noopPatch}
      onBulkResolve={noopBulk}
      {...props}
    />,
  );
}

beforeEach(() => {
  toastError.mockClear();
  noopPatch.mockClear();
});

describe("FindingsTable kind bucketing", () => {
  it("gives every kind present a tab, including ones with no hardcoded label", () => {
    renderTable([
      finding({ id: "a", kind: "unreachable_file" }),
      finding({ id: "b", kind: "unused_internal" }),
      finding({ id: "c", kind: "some_future_kind" }),
    ]);

    // The regression this guards: a fixed three-kind allowlist silently dropped
    // unused_internal findings that every other surface was counting.
    expect(screen.getByRole("tab", { name: /Unreachable Files/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Unused Internals/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Some Future Kind/ })).toBeInTheDocument();
  });

  it("shows no tab for a kind that is absent from the data", () => {
    renderTable([finding({ id: "a", kind: "unreachable_file" })]);

    expect(screen.queryByRole("tab", { name: /Zombie Packages/ })).not.toBeInTheDocument();
  });

  it("renders an empty state rather than tabs when there are no findings", () => {
    renderTable([]);

    expect(screen.getByText("No findings")).toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  });

  it("tab counts follow the confidence filter while the tab itself stays put", async () => {
    renderTable([
      finding({ id: "a", kind: "unreachable_file", confidence: 1 }),
      finding({ id: "b", kind: "unreachable_file", confidence: 0.45 }),
    ]);

    expect(screen.getByRole("tab", { name: /Unreachable Files 2/ })).toBeInTheDocument();

    const slider = screen.getByLabelText("Minimum confidence");
    for (let i = 0; i < 12; i++) fireEvent.keyDown(slider, { key: "ArrowRight" });

    // The count drops, but the tab does not disappear from under the pointer.
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: /Unreachable Files 1/ })).toBeInTheDocument(),
    );
  });
});

describe("FindingsTable selection", () => {
  it("select-all covers only the visible rows", () => {
    renderTable([
      finding({ id: "a", confidence: 0.95 }),
      finding({ id: "b", confidence: 0.95 }),
    ]);

    fireEvent.click(screen.getByLabelText("Select all findings"));

    expect(screen.getByRole("button", { name: "Resolve 2 selected" })).toBeInTheDocument();
  });

  it("drops selected rows the confidence filter has hidden", async () => {
    const onBulkResolve = vi.fn(async (ids: string[]) => ids);
    renderTable(
      [finding({ id: "a", confidence: 1 }), finding({ id: "b", confidence: 0.45 })],
      { onBulkResolve },
    );

    fireEvent.click(screen.getByLabelText("Select all findings"));
    expect(screen.getByRole("button", { name: "Resolve 2 selected" })).toBeInTheDocument();

    // Raise the floor past the second finding: it leaves the table, so it must
    // leave the selection too — otherwise "Resolve N selected" resolves rows
    // the user can no longer see.
    const slider = screen.getByLabelText("Minimum confidence");
    for (let i = 0; i < 12; i++) fireEvent.keyDown(slider, { key: "ArrowRight" });

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Resolve 1 selected" })).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole("button", { name: "Resolve 1 selected" }));
    fireEvent.click(await screen.findByRole("button", { name: "Resolve all" }));

    await waitFor(() => expect(onBulkResolve).toHaveBeenCalledWith(["a"]));
  });

  it("clears selection when switching tabs", () => {
    renderTable([
      finding({ id: "a", kind: "unreachable_file" }),
      finding({ id: "b", kind: "unused_export" }),
    ]);

    fireEvent.click(screen.getByLabelText("Select all findings"));
    expect(screen.getByRole("button", { name: "Resolve 1 selected" })).toBeInTheDocument();

    // Radix activates a tab on mousedown, not click.
    fireEvent.mouseDown(screen.getByRole("tab", { name: /Unused Exports/ }));

    expect(screen.queryByRole("button", { name: /Resolve \d+ selected/ })).not.toBeInTheDocument();
  });
});

describe("FindingsTable row affordances", () => {
  it("links the file path and opens the row through the host router", () => {
    const onNavigate = vi.fn();
    renderTable([finding({ id: "a" })], {
      fileHref: (p: string) => `/repos/repo-1/files/${p}`,
      onNavigate,
    });

    const link = screen.getByRole("link", { name: "src/a.ts" });
    expect(link).toHaveAttribute("href", "/repos/repo-1/files/src/a.ts");

    fireEvent.click(screen.getByRole("row", { name: /src\/a\.ts/ }));
    expect(onNavigate).toHaveBeenCalledWith("/repos/repo-1/files/src/a.ts");

    // A plain click on the name routes through the host too, rather than
    // falling through to a full page load inside a client-routed row.
    onNavigate.mockClear();
    fireEvent.click(link, { button: 0 });
    expect(onNavigate).toHaveBeenCalledWith("/repos/repo-1/files/src/a.ts");
  });

  it("does not navigate when the checkbox is clicked", () => {
    const onNavigate = vi.fn();
    renderTable([finding({ id: "a" })], { fileHref: (p: string) => `/f/${p}`, onNavigate });

    fireEvent.click(screen.getByLabelText("Select finding src/a.ts"));

    expect(onNavigate).not.toHaveBeenCalled();
  });

  it("shows the detector's reason, which previously only the AI prompt saw", () => {
    renderTable([finding({ id: "a", reason: "No importers in the dependency graph" })]);

    expect(screen.getByText("No importers in the dependency graph")).toBeInTheDocument();
  });

  it("offers an AI prompt per row and for the current selection", () => {
    const onGeneratePrompt = vi.fn();
    renderTable([finding({ id: "a" }), finding({ id: "b" })], { onGeneratePrompt });

    fireEvent.click(screen.getByRole("button", { name: "AI cleanup prompt for src/a.ts" }));
    expect(onGeneratePrompt).toHaveBeenCalledWith(["a"]);

    fireEvent.click(screen.getByLabelText("Select all findings"));
    fireEvent.click(screen.getByRole("button", { name: "AI prompt for 2 selected" }));
    expect(onGeneratePrompt).toHaveBeenLastCalledWith(["a", "b"]);
  });

  it("hides the graph action when the host exposes no graph route", () => {
    renderTable([finding({ id: "a" })]);

    expect(screen.queryByRole("link", { name: "Graph" })).not.toBeInTheDocument();
  });
});

describe("FindingsTable row actions", () => {
  it("surfaces a toast instead of an unhandled rejection when a patch fails", async () => {
    const onPatch = vi.fn(async () => {
      throw new Error("Network request failed");
    });
    renderTable([finding({ id: "a" })], { onPatch });

    fireEvent.click(screen.getByRole("button", { name: "Resolve src/a.ts" }));
    fireEvent.click(await screen.findByRole("button", { name: "Resolve" }));

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(String(toastError.mock.calls[0]?.[0])).toMatch(/Couldn't update finding/);
  });
});
