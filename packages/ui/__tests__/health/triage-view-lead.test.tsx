/**
 * The score leads Code Health in full. When the host hands TriageView Fix
 * first, it renders under the score, never above it.
 */

import { describe, expect, it, vi } from "vitest";
import { useEffect } from "react";
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
    performance_causes: { now: 4, production: 415, all: 926, per_10k_loc: 9.03 },
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
    expect(screen.getByText("6.8").closest("details")).toBeNull();
    expect(container.textContent).not.toContain("More");
  });

  it("keeps the full lede when no lead is given", async () => {
    renderView();
    await screen.findByText("Maintainability");
    expect(screen.getByText("6.8").closest("details")).toBeNull();
  });
});

describe("TriageView selection", () => {
  it("mounts the lead once, so it keeps its state when the overview lands", async () => {
    const mounts = vi.fn();
    function Lead() {
      useEffect(() => {
        mounts();
      }, []);
      return <section data-testid="lead">Fix first</section>;
    }
    renderView(<Lead />);
    // Rendered while the overview is still loading, then still the same node.
    expect(screen.getByTestId("lead")).toBeInTheDocument();
    await screen.findByText("Maintainability");
    expect(mounts).toHaveBeenCalledTimes(1);
  });

  it("opens the drawer for a linked file before the overview arrives", () => {
    const renderFileDrawer = vi.fn(() => null);
    render(
      <SWRConfig value={{ provider: () => new Map() }}>
        <TriageView
          adapter={{ ...adapter(), renderFileDrawer } as unknown as CodeHealthAdapter}
          selectedPath="src/a.py"
          onSelectPath={() => {}}
        />
      </SWRConfig>,
    );
    expect(renderFileDrawer).toHaveBeenCalledWith(
      expect.objectContaining({ filePath: "src/a.py", lens: "health" }),
    );
  });
});
