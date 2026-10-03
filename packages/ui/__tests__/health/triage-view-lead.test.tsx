/**
 * The score leads Code Health in full. When the host hands TriageView Fix
 * first, it renders under the score, never above it.
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
  it("leads with the full score and puts Fix first under it", async () => {
    const { container } = renderView(<section data-testid="lead">Fix first</section>);
    const score = await screen.findByText("Maintainability");
    const lead = screen.getByTestId("lead");
    expect(score.compareDocumentPosition(lead) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // The score is not folded behind a disclosure.
    expect(container.querySelector("details")).toBeNull();
  });

  it("keeps the full lede when no lead is given", async () => {
    const { container } = renderView();
    await screen.findByText("Maintainability");
    expect(container.querySelector("details")).toBeNull();
  });
});
