import { describe, it, expect } from "vitest";
import { render, screen, renderHook } from "@testing-library/react";
import { VirtualizedTable } from "../../src/shared/virtualized-table/virtualized-table.js";
import { useVirtualRows } from "../../src/shared/virtualized-table/use-virtual-rows.js";

interface Row {
  id: string;
  name: string;
}

function makeRows(n: number): Row[] {
  return Array.from({ length: n }, (_, i) => ({ id: `r${i}`, name: `row-${i}` }));
}

function renderTable(rows: Row[]) {
  return render(
    <VirtualizedTable
      rows={rows}
      rowKey={(r) => r.id}
      header={
        <tr>
          <th>Name</th>
        </tr>
      }
      renderRow={(r) => (
        <tr>
          <td>{r.name}</td>
        </tr>
      )}
      empty={<div>No rows</div>}
    />,
  );
}

describe("VirtualizedTable", () => {
  it("renders the header and every row below the windowing threshold", () => {
    renderTable(makeRows(5));
    expect(screen.getByText("Name")).toBeTruthy();
    for (let i = 0; i < 5; i++) {
      expect(screen.getByText(`row-${i}`)).toBeTruthy();
    }
  });

  it("renders the empty slot when there are no rows", () => {
    renderTable([]);
    expect(screen.getByText("No rows")).toBeTruthy();
  });
});

describe("useVirtualRows", () => {
  it("returns every row with no padding below the threshold", () => {
    const { result } = renderHook(() =>
      useVirtualRows({ count: 10, estimateSize: 40, threshold: 60 }),
    );
    expect(result.current.isVirtualized).toBe(false);
    expect(result.current.virtualRows).toHaveLength(10);
    expect(result.current.virtualRows[0]).toEqual({ index: 0, start: 0, size: 40 });
    expect(result.current.virtualRows[9]?.start).toBe(360); // 9 * 40
    expect(result.current.paddingTop).toBe(0);
    expect(result.current.paddingBottom).toBe(0);
  });

  it("activates windowing above the threshold", () => {
    const { result } = renderHook(() =>
      useVirtualRows({ count: 500, estimateSize: 40, threshold: 60 }),
    );
    expect(result.current.isVirtualized).toBe(true);
    // Windowed: far fewer rows rendered than the full 500.
    expect(result.current.virtualRows.length).toBeLessThan(500);
  });

  it("gives the spacer the full list height while no window has been measured", () => {
    // jsdom reports a zero-height viewport, which is also the state of any real
    // scroll container on first paint. A zero spacer there is a deadlock: no
    // rows means no height, and no height means the virtualizer never computes
    // a range, so the container stays empty forever.
    const { result } = renderHook(() =>
      useVirtualRows({ count: 500, estimateSize: 40, threshold: 60 }),
    );
    expect(result.current.virtualRows).toHaveLength(0);
    expect(result.current.paddingBottom).toBe(500 * 40);
  });
});
