import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { BulkTriageBar, runCapped } from "../../src/health/bulk-triage-bar.js";

describe("runCapped", () => {
  it("never has more than the cap in flight and reports failures", async () => {
    let inFlight = 0;
    let peak = 0;
    const { ok, failed } = await runCapped([1, 2, 3, 4, 5, 6, 7, 8, 9], 3, async (n) => {
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      await new Promise((r) => setTimeout(r, 1));
      inFlight -= 1;
      if (n % 4 === 0) throw new Error("nope");
    });
    expect(peak).toBe(3);
    expect(failed.sort()).toEqual([4, 8]);
    expect(ok).toHaveLength(7);
  });
});

describe("BulkTriageBar", () => {
  const selection = new Map([
    ["a.py", "f-a"],
    ["b.py", "f-b"],
  ]);

  it("renders nothing with nothing selected", () => {
    const { container } = render(
      <BulkTriageBar
        selection={new Map()}
        selectablePaths={[]}
        onSelectAll={() => {}}
        onClear={() => {}}
        updateStatus={async () => {}}
        onDone={() => {}}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("sets the status of each selected row's finding and keeps the failures", async () => {
    const updateStatus = vi.fn(async (id: string) => {
      if (id === "f-b") throw new Error("409");
    });
    const onDone = vi.fn();
    render(
      <BulkTriageBar
        selection={selection}
        selectablePaths={[
          { path: "a.py", findingId: "f-a" },
          { path: "b.py", findingId: "f-b" },
        ]}
        onSelectAll={() => {}}
        onClear={() => {}}
        updateStatus={updateStatus}
        onDone={onDone}
      />,
    );
    expect(screen.getByRole("region", { name: "Bulk triage" }).textContent).toMatch(
      /2 rows selected\. Sets the status of the finding each row names\./,
    );
    // Every row on the page is already selected, so the bar does not offer it.
    expect(screen.queryByRole("button", { name: "Select all on this page" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "False positive" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(["b.py"]));
    expect(updateStatus.mock.calls.map((c) => c)).toEqual([
      ["f-a", "false_positive"],
      ["f-b", "false_positive"],
    ]);
    expect(screen.getByRole("status").textContent).toMatch(/1 could not be updated and stays selected/);
  });

  it("offers the rest of the page when only part of it is selected", () => {
    const onSelectAll = vi.fn();
    render(
      <BulkTriageBar
        selection={new Map([["a.py", "f-a"]])}
        selectablePaths={[
          { path: "a.py", findingId: "f-a" },
          { path: "c.py", findingId: "f-c" },
        ]}
        onSelectAll={onSelectAll}
        onClear={() => {}}
        updateStatus={async () => {}}
        onDone={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Select all on this page" }));
    expect(onSelectAll).toHaveBeenCalled();
  });
});
