import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import type { ReactElement } from "react";
import { ImpactView } from "../../src/blast-radius/impact-view.js";
import type { ImpactAdapter } from "../../src/blast-radius/impact-adapter.js";
import type { BlastRadiusResponse } from "@repowise-dev/types/blast-radius";

const RESULT: BlastRadiusResponse = {
  overall_risk_score: 0.42,
  direct_risks: [],
  transitive_affected: [],
  cochange_warnings: [],
  recommended_reviewers: [],
  test_gaps: [],
};

function makeAdapter(over: Partial<ImpactAdapter> = {}): ImpactAdapter {
  return {
    cacheKey: "repo-1",
    listHotspots: vi.fn(async () => [{ file_path: "src/hot.ts" }]),
    searchFiles: vi.fn(async () => []),
    analyze: vi.fn(async () => RESULT),
    ...over,
  };
}

function renderView(node: ReactElement) {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      {node}
    </SWRConfig>,
  );
}

describe("ImpactView", () => {
  it("renders hotspot chips from the adapter", async () => {
    renderView(<ImpactView adapter={makeAdapter()} />);
    expect(
      await screen.findByRole("button", { name: "Add src/hot.ts to changed files" }),
    ).toBeInTheDocument();
  });

  it("requires at least one path before analyzing", async () => {
    const adapter = makeAdapter();
    renderView(<ImpactView adapter={adapter} />);

    fireEvent.click(screen.getByRole("button", { name: "Analyze" }));

    expect(await screen.findByText("Add at least one file path.")).toBeInTheDocument();
    expect(adapter.analyze).not.toHaveBeenCalled();
  });

  it("analyzes the selected hotspot and renders the reviewer slot", async () => {
    const adapter = makeAdapter({
      renderReviewers: (files) => <div>reviewers for {files.length} paths</div>,
    });
    renderView(<ImpactView adapter={adapter} />);

    // Seed a changed file from the hotspot chip, then analyze.
    fireEvent.click(
      await screen.findByRole("button", { name: "Add src/hot.ts to changed files" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Analyze" }));

    await waitFor(() =>
      expect(adapter.analyze).toHaveBeenCalledWith({
        changedFiles: ["src/hot.ts"],
        maxDepth: 3,
      }),
    );
    expect(await screen.findByText("reviewers for 1 paths")).toBeInTheDocument();
  });

  it("degrades gracefully when no reviewer slot is supplied", async () => {
    const adapter = makeAdapter();
    renderView(<ImpactView adapter={adapter} />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Add src/hot.ts to changed files" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Analyze" }));

    // Results still render (risk gauge) without a reviewer panel.
    await waitFor(() => expect(adapter.analyze).toHaveBeenCalled());
    expect(screen.queryByText(/reviewers for/)).not.toBeInTheDocument();
  });
});
