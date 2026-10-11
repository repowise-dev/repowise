/**
 * The dimension filter has to offer every dimension the query accepts.
 *
 * Both findings route docstrings promise that asking for a dimension returns
 * it, and the component's own state is typed `HealthDimension | "all"`, so
 * `performance` and `advisory` were legal values with no way to select them.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { HealthWorkQueueQuery } from "@repowise-dev/types";

import type { CodeHealthAdapter } from "../../src/health/code-health-adapter";
import { FindingsView } from "../../src/health/findings-view";

const overview = () => ({ files: [], biomarkers: [], summary: {} }) as never;

function adapter(
  getHealthWorkQueue: CodeHealthAdapter["getHealthWorkQueue"],
): CodeHealthAdapter {
  return {
    cacheKey: `dimension-filter-${Math.random()}`,
    getOverview: async () => overview(),
    listFindings: async () => [],
    getHealthWorkQueue,
    updateFindingStatus: async () => undefined,
    renderFileDrawer: () => null,
  } as unknown as CodeHealthAdapter;
}

describe("FindingsView dimension filter", () => {
  it("offers every dimension the findings query accepts", async () => {
    render(<FindingsView adapter={adapter(async () => ({ targets: [], total: 0 }))} />);
    const select = await screen.findByLabelText("Dimension");
    const names = within(select)
      .getAllByRole("option")
      .map((o) => (o as HTMLOptionElement).value);
    expect(names).toEqual(["all", "defect", "maintainability", "performance", "advisory"]);
  });

  it("asks the server for the advisory dimension once it is selected", async () => {
    const load = vi.fn(async (_opts?: HealthWorkQueueQuery) => ({ targets: [], total: 0 }));
    render(<FindingsView adapter={adapter(load)} />);
    const select = await screen.findByLabelText("Dimension");
    await waitFor(() => expect(load).toHaveBeenCalled());

    fireEvent.change(select, { target: { value: "advisory" } });
    await waitFor(() =>
      expect(load.mock.calls.at(-1)?.[0]).toMatchObject({ dimension: "advisory" }),
    );
  });
});
