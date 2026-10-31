import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import type { ReactElement } from "react";
import { ImpactView, changeIdFromLink, changeLinkParam } from "../../src/blast-radius/impact-view.js";
import type { ImpactAdapter } from "../../src/blast-radius/impact-adapter.js";
import type {
  BlastRadiusResponse,
  ChangeBlastRadiusResponse,
  ChangeCandidatesResponse,
} from "@repowise-dev/types/blast-radius";

const RESULT: BlastRadiusResponse = {
  structural_impact_score: 0.42,
  structural_impact_band: "localized",
  structural_impact_scale: {
    field: "structural_impact_score",
    kind: "heuristic_structural_score",
    unit: "normalized_points",
    range: { minimum: 0, maximum: 10 },
    measures: "indexed structural exposure",
    deterministic: true,
    calibration: { status: "uncalibrated", source: null },
    authoritative_for_change_review: false,
    runtime_breakage_probability: false,
  },
  overall_risk_score: 0.42,
  overall_risk_score_compatibility: {
    deprecated: true,
    replacement: "structural_impact_score",
    equivalent_value: true,
    historical_meaning: "uncalibrated 0-10 structural blast-radius heuristic",
  },
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

const CHANGES: ChangeCandidatesResponse = {
  working_tree_supported: true,
  default_id: "working-tree",
  changes: [
    {
      id: "working-tree",
      kind: "working_tree",
      label: "Uncommitted changes",
      title: "On main",
      author: null,
      committed_at: null,
      head_sha: null,
      base: null,
      ahead: null,
      files_changed: 2,
      is_current: true,
    },
    {
      id: "branch:feat/x",
      kind: "branch",
      label: "feat/x",
      title: "feat: x",
      author: "Dev",
      committed_at: null,
      head_sha: "a".repeat(40),
      base: "origin/main",
      ahead: 1,
      files_changed: 1,
      is_current: false,
    },
  ],
};

function changeResult(files: string[]): ChangeBlastRadiusResponse {
  return {
    ...RESULT,
    change_id: "x",
    changed_files: files,
    changed_files_total: files.length,
    transitive_affected: [{ path: "src/app.ts", depth: 1, via: files[0] ?? null }],
    affected_signals: [{ path: "src/app.ts", is_hotspot: true, health_score: 3.2, module: "src" }],
  };
}

async function openPicker(name: RegExp) {
  fireEvent.click(await screen.findByRole("button", { name }));
}

describe("ImpactView without a change list", () => {
  it("leads with picking files and analyzes the chosen hotspot", async () => {
    const adapter = makeAdapter();
    renderView(<ImpactView adapter={adapter} />);

    fireEvent.click(await screen.findByRole("button", { name: "Add hotspot src/hot.ts" }));
    fireEvent.click(screen.getByRole("button", { name: "Analyze these files" }));

    await waitFor(() =>
      expect(adapter.analyze).toHaveBeenCalledWith({ changedFiles: ["src/hot.ts"], maxDepth: 3 }),
    );
    // The verdict leads, with the agent prompt as the action.
    expect(await screen.findByRole("button", { name: /Copy review prompt/ })).toBeInTheDocument();
    expect(screen.getByText("Small change.")).toBeInTheDocument();
  });

  it("cannot analyze an empty selection", async () => {
    const adapter = makeAdapter();
    renderView(<ImpactView adapter={adapter} />);
    expect(screen.getByRole("button", { name: "Analyze these files" })).toBeDisabled();
  });

  it("analyzes seeded files on arrival", async () => {
    const adapter = makeAdapter();
    renderView(<ImpactView adapter={adapter} initialFiles={["src/a.ts"]} />);
    await waitFor(() =>
      expect(adapter.analyze).toHaveBeenCalledWith({ changedFiles: ["src/a.ts"], maxDepth: 3 }),
    );
    expect(await screen.findByText("1 picked file")).toBeInTheDocument();
  });
});

describe("ImpactView with a change list", () => {
  it("opens the default change and switches from the picker", async () => {
    const analyzeChange = vi.fn(async ({ changeId }: { changeId: string }) =>
      changeResult(changeId === "working-tree" ? ["src/a.ts", "src/b.ts"] : ["src/core.ts"]),
    );
    const adapter = makeAdapter({
      listChanges: vi.fn(async () => CHANGES),
      analyzeChange,
      fileHref: (p) => `/files/${p}`,
    });
    renderView(<ImpactView adapter={adapter} />);

    await waitFor(() => expect(analyzeChange).toHaveBeenCalledWith({ changeId: "working-tree", maxDepth: 3 }));
    expect(await screen.findByText("2 files changed, no test found to run.")).toBeInTheDocument();
    // The importer is a weak-health hotspot: it is the first file to look at.
    expect(screen.getAllByRole("link", { name: /src\/app\.ts/ })[0]).toHaveAttribute("href", "/files/src/app.ts");

    await openPicker(/Change under review: Uncommitted changes/);
    fireEvent.click(screen.getByRole("radio", { name: /Branches/ }));
    fireEvent.click(await screen.findByRole("button", { name: /feat\/x/ }));
    await waitFor(() => expect(analyzeChange).toHaveBeenCalledWith({ changeId: "branch:feat/x", maxDepth: 3 }));
    expect(await screen.findByText("1 file changed, no test found to run.")).toBeInTheDocument();
    expect(adapter.analyze).not.toHaveBeenCalled();
  });

  it("keeps the numbers on screen until the new pick's own arrive, and says it is reading", async () => {
    let release: () => void = () => {};
    const analyzeChange = vi.fn(({ changeId }: { changeId: string }) =>
      changeId === "working-tree"
        ? Promise.resolve(changeResult(["src/a.ts", "src/b.ts"]))
        : new Promise<ChangeBlastRadiusResponse>((resolve) => {
            release = () => resolve(changeResult(["src/core.ts"]));
          }),
    );
    renderView(
      <ImpactView adapter={makeAdapter({ listChanges: vi.fn(async () => CHANGES), analyzeChange })} />,
    );
    expect(await screen.findByText("2 files changed, no test found to run.")).toBeInTheDocument();

    await openPicker(/Change under review/);
    fireEvent.click(screen.getByRole("radio", { name: /Branches/ }));
    fireEvent.click(await screen.findByRole("button", { name: /feat\/x/ }));
    expect(await screen.findByText("Reading what depends on feat/x...")).toBeInTheDocument();
    expect(screen.getByText("2 files changed, no test found to run.")).toBeInTheDocument();
    release();
    expect(await screen.findByText("1 file changed, no test found to run.")).toBeInTheDocument();
  });
});

describe("shareable change links", () => {
  it("round-trips wip, a branch and a full sha", () => {
    const sha = "c".repeat(40);
    for (const id of ["working-tree", "branch:feat/x", `commit:${sha}`]) {
      expect(changeIdFromLink(changeLinkParam(id))).toBe(id);
    }
    expect(changeLinkParam("working-tree")).toBe("wip");
    expect(changeLinkParam("branch:feat/x")).toBe("feat/x");
    // A branch named like the working tree or a sha keeps its prefix.
    expect(changeLinkParam("branch:wip")).toBe("branch:wip");
    expect(changeIdFromLink(changeLinkParam("branch:wip"))).toBe("branch:wip");
    expect(changeIdFromLink(changeLinkParam(`branch:${sha}`))).toBe(`branch:${sha}`);
  });

  it("opens the linked change instead of the default, and reports picks", async () => {
    const analyzeChange = vi.fn(async () => changeResult(["src/core.ts"]));
    const onChangePicked = vi.fn();
    renderView(
      <ImpactView
        adapter={makeAdapter({ listChanges: vi.fn(async () => CHANGES), analyzeChange })}
        initialChange="feat/x"
        onChangePicked={onChangePicked}
      />,
    );
    await waitFor(() => expect(analyzeChange).toHaveBeenCalledWith({ changeId: "branch:feat/x", maxDepth: 3 }));
    expect(analyzeChange).not.toHaveBeenCalledWith({ changeId: "working-tree", maxDepth: 3 });

    await openPicker(/Change under review: feat\/x/);
    fireEvent.click(screen.getByRole("radio", { name: /Working tree/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Uncommitted changes/ }));
    expect(onChangePicked).toHaveBeenCalledWith("wip");
  });
});
