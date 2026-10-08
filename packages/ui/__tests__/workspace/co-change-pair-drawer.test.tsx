import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { WorkspaceCoChangeEntry } from "@repowise-dev/types/workspace";
import {
  CoChangePairDrawer,
  type CoChangeFileHistory,
  type Loadable,
} from "../../src/workspace/co-change-pair-drawer.js";
import { nearbyCommits } from "../../src/workspace/co-change-facts.js";

const PAIR: WorkspaceCoChangeEntry = {
  source_repo: "backend",
  source_file: "app/schemas/repos.py",
  target_repo: "frontend",
  target_file: "src/lib/api/types.ts",
  strength: 0.69,
  frequency: 3,
  last_date: "2026-09-01",
  evidence: null,
};

function history(sha: string, date: string): Loadable<CoChangeFileHistory> {
  return {
    state: "ready",
    data: {
      commits90d: 6,
      churnPercentile: 73,
      priorFixes: 0,
      isHotspot: true,
      commits: [{ sha, date, author: "Ana", message: `commit ${sha}` }],
    },
  };
}

describe("CoChangePairDrawer", () => {
  it("names a hidden coupling, links both files and reconstructs nearby commits", () => {
    const onPrompt = vi.fn();
    render(
      <CoChangePairDrawer
        pair={PAIR}
        onClose={() => {}}
        structure={{ state: "ready", data: { pairLinks: [], repoLinksTotal: 12, repoLinksByType: { http: 12 } } }}
        sourceHistory={history("aaaaaaaa11", "2026-09-01T10:00:00Z")}
        targetHistory={history("bbbbbbbb22", "2026-09-01T11:00:00Z")}
        commits={nearbyCommits(
          [{ sha: "aaaaaaaa11", date: "2026-09-01T10:00:00Z", author: "Ana", message: "commit aaaaaaaa11" }],
          [{ sha: "bbbbbbbb22", date: "2026-09-01T11:00:00Z", author: "Ana", message: "commit bbbbbbbb22" }],
        )}
        fileHref={(repo, path) => (repo === "backend" ? `/repos/b/files/${path}` : null)}
        onGeneratePrompt={onPrompt}
      />,
    );
    expect(screen.getByText("No declared link")).toBeInTheDocument();
    expect(screen.getByText(/a hidden coupling/)).toBeInTheDocument();
    expect(screen.getByText(/12 contract links \(12 http\)/)).toBeInTheDocument();
    expect(screen.getAllByText("Open file page")).toHaveLength(1);
    expect(screen.getByText(/frontend is not indexed/)).toBeInTheDocument();
    expect(screen.getByText("commit aaaaaaaa11")).toBeInTheDocument();
    // Paths break only after a slash: the directory segments are separate nodes.
    expect(screen.getByText("app/")).toBeInTheDocument();
    expect(screen.getByText("schemas/")).toBeInTheDocument();
    expect(screen.getAllByText("Hotspot")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: /AI investigation prompt/ }));
    expect(onPrompt).toHaveBeenCalled();
  });

  it("says what is still loading or was not checked", () => {
    render(
      <CoChangePairDrawer
        pair={PAIR}
        onClose={() => {}}
        structure={{ state: "unavailable", reason: "Contract links failed to load." }}
        sourceHistory={{ state: "loading" }}
        targetHistory={{ state: "loading" }}
        commits={null}
        onGeneratePrompt={() => {}}
      />,
    );
    expect(screen.getByText("Declared links not checked")).toBeInTheDocument();
    expect(screen.getByText("Contract links failed to load.")).toBeInTheDocument();
    expect(screen.getByText(/Loading each file/)).toBeInTheDocument();
  });
});
