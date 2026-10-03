/**
 * When the host hands TriageView a lead (Fix first), it renders above the
 * score, and the score drops to one secondary line. Without one, the score
 * keeps leading, so a host that has not adopted Fix first is unchanged.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { SWRConfig } from "swr";
import type { HealthOverviewResponse } from "@repowise-dev/types/health";

import { TriageView } from "../../src/health/triage-view";
import type { CodeHealthAdapter } from "../../src/health/code-health-adapter";

const overview = {
  summary: {
    average_health: 6.8,
    maintainability_average: 8.0,
    hotspot_health: 4.5,
    performance_average: 9.7,
    performance_findings: 942,
    file_count: 3787,
    open_findings: 14755,
  },
} as unknown as HealthOverviewResponse;

function adapter(): CodeHealthAdapter {
  return {
    cacheKey: `repo-${Math.random()}`,
    getOverview: vi.fn(async () => overview),
    navigate: vi.fn(),
    renderFileDrawer: () => null,
  } as unknown as CodeHealthAdapter;
}

function renderView(leadSlot?: React.ReactNode) {
  return render(
    <SWRConfig value={{ provider: () => new Map() }}>
      <TriageView adapter={adapter()} {...(leadSlot ? { leadSlot } : {})} />
    </SWRConfig>,
  );
}

describe("TriageView lead", () => {
  it("puts the lead first and the score behind one line", async () => {
    const { container } = renderView(<section data-testid="lead">Fix first</section>);
    await screen.findByText(/out of 10 across 3,787 files/);
    const lead = screen.getByTestId("lead");
    const details = container.querySelector("details")!;
    expect(lead.compareDocumentPosition(details) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(details.open).toBe(false);
  });

  it("keeps the full lede when no lead is given", async () => {
    const { container } = renderView();
    await screen.findByText("Maintainability");
    expect(container.querySelector("details")).toBeNull();
  });
});
