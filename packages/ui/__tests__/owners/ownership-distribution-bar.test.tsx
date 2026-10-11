import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type { OwnerListEntry } from "@repowise-dev/types/owners";

import { OwnershipDistributionBar } from "../../src/owners/ownership-distribution-bar";

const owner = (name: string, files: number): OwnerListEntry => ({
  key: name,
  name,
  email: null,
  files_owned: files,
  hotspots_owned: 0,
  silo_modules: 0,
  dead_code_files_owned: 0,
  dead_code_lines_owned: 0,
  commit_count_90d: null,
  last_commit_at: null,
  bus_factor_risk_files: 0,
});

describe("OwnershipDistributionBar", () => {
  it("names the top five, links them, and folds every other contributor", () => {
    const owners = [10, 60, 5, 40, 30, 20, 2].map((n, i) => owner(`dev${i}`, n));
    const { container } = render(
      <OwnershipDistributionBar
        owners={owners}
        totalContributors={40}
        hrefFor={(o) => `/owners/${o.key}`}
      />,
    );
    const items = within(screen.getByRole("list", { name: "Owned files by contributor" }))
      .getAllByRole("listitem")
      .map((li) => li.textContent);
    // 167 owned files across the seven loaded contributors.
    expect(items).toEqual(["dev136%", "dev324%", "dev418%", "dev512%", "dev06%", "35 others4%"]);
    expect(screen.getByRole("link", { name: /dev1/ })).toHaveAttribute("href", "/owners/dev1");
    // The same neutral share steps as every other proportion, never the accent.
    expect(container.innerHTML).toContain("--color-share-1");
    expect(container.innerHTML).not.toContain("accent");
  });
});
