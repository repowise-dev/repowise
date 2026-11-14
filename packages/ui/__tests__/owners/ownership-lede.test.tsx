import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { OwnersSummary } from "@repowise-dev/types/owners";

import { OwnershipLede } from "../../src/owners/ownership-lede";
import type { KnowledgeCoverage } from "../../src/owners/knowledge-coverage";

const summary: OwnersSummary = {
  truck_factor: 1,
  people: 3,
  automation_identities: 0,
  owners: 2,
  tracked_files: 10,
  owned_files: 10,
  single_owner_files: 6,
  single_owner_hotspots: 2,
  concentration: { top_n: 3, share: 0.9, owners: [{ key: "ana@x.dev", name: "Ana Example", files_owned: 9, share: 0.9 }] },
  silo_modules: [],
  activity_window_days: 90,
  active_contributors: 2,
  activity_complete: true,
  quiet_owners: [],
  quiet_owners_total: 0,
};

const coverage = (share: number | null): KnowledgeCoverage => ({
  files: 4,
  sharedFiles: 3,
  nloc: 100,
  sharedNloc: 74,
  share,
  basis: "nloc",
  singleHolderAreas: [],
  singleHolderFiles: 1,
});

const href = (tab: string, leave?: string[]) => `?tab=${tab}${leave?.length ? `&leave=${leave.join(",")}` : ""}`;

describe("OwnershipLede", () => {
  it("leads with knowledge coverage, calm green when well shared", () => {
    render(<OwnershipLede coverage={coverage(0.74)} summary={summary} hrefForTab={href} />);
    expect(screen.getByText("74%", { selector: "*:not(p)" })).toBeTruthy();
    expect(screen.getByText("Well shared")).toBeTruthy();
    expect(screen.getByText(/has two or more people who know it/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /Copy prompt/ })).toBeTruthy();
  });

  it("reads plain when low and never names the truck-factor person", () => {
    const { container } = render(<OwnershipLede coverage={coverage(0.3)} summary={summary} hrefForTab={href} />);
    expect(screen.queryByText("Well shared")).toBeNull();
    expect(screen.getByText("Fewest people holding half the owned files")).toBeTruthy();
    expect(container.textContent).not.toContain("Ana Example");
  });

  it("says so when nothing with history changed", () => {
    render(<OwnershipLede coverage={coverage(null)} summary={summary} hrefForTab={href} />);
    expect(screen.getByText(/no moving\s+code to measure/)).toBeTruthy();
  });
});
