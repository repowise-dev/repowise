import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import type { OwnerListEntry } from "@repowise-dev/types/owners";

import { OwnerTable } from "../../src/owners/owner-table";

const owner = (name: string, over: Partial<OwnerListEntry>): OwnerListEntry => ({
  key: name,
  name,
  email: null,
  kind: "human",
  files_owned: 3,
  hotspots_owned: 0,
  silo_modules: 0,
  dead_code_files_owned: 0,
  dead_code_lines_owned: 0,
  commit_count_90d: null,
  last_commit_at: null,
  bus_factor_risk_files: 0,
  ...over,
});

describe("OwnerTable commits 90d", () => {
  it("shows a floor when the indexed commits stop inside the window", () => {
    render(
      <OwnerTable
        sort="files_owned"
        owners={[
          owner("Ann", { commit_count_90d_at_least: 312 }),
          owner("Bea", { commit_count_90d: 7 }),
          owner("Cy", {}),
        ]}
      />,
    );
    const floor = screen.getByText("≥312");
    expect(floor.getAttribute("title")).toMatch(/stop inside the 90 days/);
    expect(screen.getByText("7")).toBeTruthy();
    // Unknown with no floor stays unknown, never zero.
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });
});
