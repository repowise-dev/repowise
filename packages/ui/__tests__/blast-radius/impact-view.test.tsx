import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import axe from "axe-core";
import type { ReactElement } from "react";
import { ImpactView, changeIdFromLink, changeLinkParam } from "../../src/blast-radius/impact-view.js";
import type { ImpactAdapter } from "../../src/blast-radius/impact-adapter.js";
import type {
  BlastRadiusResponse,
  ChangeBlastRadiusResponse,
  ChangeCandidatesResponse,
  ChangeHealthCheckResponse,
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
    expect(await screen.findByText(/^Reading what depends on feat\/x/)).toBeInTheDocument();
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

describe("the step map", () => {
  it("opens on the weakest step-1 folder, narrows the dependents list, and clears from the keyboard", async () => {
    const analyze = vi.fn(async () => ({
      ...RESULT,
      transitive_affected: [
        { path: "packages/s/src/weak/a.py", depth: 1, via: "packages/s/src/core/x.py" },
        { path: "packages/s/src/calm/b.py", depth: 1, via: "packages/s/src/core/x.py" },
        { path: "packages/s/src/calm/c.py", depth: 1, via: "packages/s/src/core/x.py" },
        { path: "tests/test_x.py", depth: 1, via: "packages/s/src/core/x.py" },
      ],
      affected_signals: [
        { path: "packages/s/src/weak/a.py", is_hotspot: false, health_score: 3.1, module: "packages/s" },
        { path: "tests/test_x.py", is_hotspot: false, health_score: 9, module: "tests", is_test: true },
      ],
    }));
    renderView(<ImpactView adapter={makeAdapter({ analyze })} initialFiles={["packages/s/src/core/x.py"]} />);
    const weak = await screen.findByRole("button", { name: /^s\/weak, 1 step away: 1 file, 1 with poor health/ });
    expect(weak).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/Showing s\/weak, 1 step away/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /tests/ })).not.toBeInTheDocument();

    const calm = screen.getByRole("button", { name: /^s\/calm, 1 step away: 2 files/ });
    fireEvent.keyDown(calm, { key: "Enter" });
    expect(calm).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/Showing s\/calm, 1 step away/)).toBeInTheDocument();

    fireEvent.keyDown(calm, { key: " " });
    expect(calm).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByText(/Showing/)).not.toBeInTheDocument();
  });
});

describe("change links", () => {
  it("reads every picker id pasted as is, so ?change=commit:<sha> opens the commit", () => {
    const sha = "e".repeat(40);
    for (const id of [`commit:${sha}`, "branch:feat/x", "working-tree"]) {
      expect(changeIdFromLink(id)).toBe(id);
      expect(changeIdFromLink(changeLinkParam(id))).toBe(id);
    }
    // A branch named like a picker id keeps its prefix both ways.
    expect(changeLinkParam("branch:working-tree")).toBe("branch:working-tree");
    expect(changeIdFromLink(changeLinkParam("branch:working-tree"))).toBe("branch:working-tree");
    // A malformed commit id stays a branch name rather than a bad commit.
    expect(changeIdFromLink("commit:abc")).toBe("branch:commit:abc");
  });

  it("knows no pull request kind: an unlisted pr:<n> id names nothing", async () => {
    const { changeFor } = await import("../../src/blast-radius/impact-view.js");
    expect(changeFor("pr:7", [])).toBeUndefined();
  });
});

describe("a host with no checkout", () => {
  it("offers commits only, hides the working tree and the health check, and opens a commit", async () => {
    const commit = {
      id: `commit:${"b".repeat(40)}`,
      kind: "commit" as const,
      label: "bbbbbbbb",
      title: "Fix the cache",
      author: "Dev",
      committed_at: null,
      head_sha: "b".repeat(40),
      base: null,
      ahead: null,
      files_changed: 3,
      is_current: false,
    };
    const analyzeChange = vi.fn(async () => changeResult(["src/core.ts"]));
    const checkHealth = vi.fn();
    const adapter = makeAdapter({
      listChanges: vi.fn(async () => ({ working_tree_supported: false, default_id: commit.id, changes: [commit] })),
      analyzeChange,
      checkHealth,
    });
    renderView(<ImpactView adapter={adapter} />);
    await waitFor(() => expect(analyzeChange).toHaveBeenCalledWith({ changeId: commit.id, maxDepth: 3 }));
    await openPicker(/Change under review: Fix the cache/);
    expect(screen.getByRole("radio", { name: /Commits/ })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: /Working tree/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Run health check/ })).not.toBeInTheDocument();
  });
});

describe("a change that fails to open", () => {
  it("says a missing change is gone, keeps the picker, and offers a retry for other failures", async () => {
    const analyzeChange = vi
      .fn()
      .mockRejectedValueOnce(Object.assign(new Error("API error 404: unknown branch 'commit:x'"), { status: 404 }))
      .mockRejectedValueOnce(new Error("API error 500: boom"))
      .mockResolvedValue(changeResult(["src/core.ts"]));
    const adapter = makeAdapter({ listChanges: vi.fn(async () => CHANGES), analyzeChange });
    renderView(<ImpactView adapter={adapter} />);
    expect(await screen.findByText(/not in this repository any more/)).toBeInTheDocument();
    expect(screen.queryByText(/API error/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();

    await openPicker(/Change under review/);
    fireEvent.click(screen.getByRole("radio", { name: /Branches/ }));
    fireEvent.click(await screen.findByRole("button", { name: /feat\/x/ }));
    expect(await screen.findByText("boom")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("1 file changed, no test found to run.")).toBeInTheDocument();
  });
});

describe("drawing interactions", () => {
  const CORE = "packages/s/src/core/x.py";
  const analyze = () =>
    vi.fn(async () => ({
      ...RESULT,
      transitive_affected: [
        { path: "packages/s/src/a/1.py", depth: 1, via: CORE },
        { path: "packages/s/src/b/1.py", depth: 1, via: CORE },
        { path: "packages/s/src/c/1.py", depth: 2, via: "packages/s/src/a/1.py" },
        ...Array.from({ length: 7 }, (_, i) => ({ path: `packages/s/src/z${i}/1.py`, depth: 2, via: "packages/s/src/b/1.py" })),
      ],
      affected_signals: [{ path: "packages/s/src/a/1.py", is_hotspot: false, health_score: 3, module: "packages/s" }],
    }));
  const block = (name: RegExp) => screen.getByRole("button", { name });

  it("lights a hovered block's path and names it, and links list rows back to the drawing", async () => {
    renderView(<ImpactView adapter={makeAdapter({ analyze: analyze() })} initialFiles={[CORE]} />);
    const a = await screen.findByRole("button", { name: /^s\/a, 1 step away/ });
    fireEvent.mouseEnter(a);
    expect(screen.getByRole("tooltip")).toHaveTextContent("s/a");
    expect(screen.getByRole("tooltip")).toHaveTextContent("1.py");
    expect(block(/^s\/b, 1 step away/)).toHaveAttribute("opacity", "0.25");
    expect(block(/^s\/c, 2 steps away/)).toHaveAttribute("opacity", "1");
    fireEvent.mouseLeave(a);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    // Hovering a file in the list lights its block's path.
    // The dependents list is last on the page; its rows carry the hover link.
    fireEvent.mouseEnter(screen.getAllByText("1.py").at(-1)!.closest("li")!);
    expect(document.querySelectorAll('g[role="button"][opacity="0.25"]').length).toBeGreaterThan(0);
  });

  it("is one tab stop with arrow keys, opens a folded step, and Escape clears", async () => {
    renderView(<ImpactView adapter={makeAdapter({ analyze: analyze() })} initialFiles={[CORE]} />);
    await screen.findByRole("button", { name: /^s\/a, 1 step away/ });
    const tabbable = document.querySelectorAll('g[role="button"][tabindex="0"]');
    expect(tabbable).toHaveLength(1);

    const a = block(/^s\/a, 1 step away/);
    a.focus();
    fireEvent.keyDown(a, { key: "ArrowDown" });
    expect(document.activeElement).toBe(block(/^s\/b, 1 step away/));

    fireEvent.click(block(/more folders.*Show every folder/));
    // Focus follows to the new control, so the keyboard never drops to the page.
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Show fewer folders in this step" }));
    expect(block(/^s\/z6, 2 steps away/)).toBeInTheDocument();

    expect(screen.getByText(/^Showing s\/a, 1 step away/)).toBeInTheDocument();
    fireEvent.keyDown(block(/^s\/b, 1 step away/), { key: "Escape" });
    expect(screen.queryByText(/^Showing/)).not.toBeInTheDocument();
  });

  it("filters the list to a band's files with a removable token", async () => {
    renderView(<ImpactView adapter={makeAdapter({ analyze: analyze() })} initialFiles={[CORE]} />);
    await screen.findByRole("button", { name: /^s\/a, 1 step away/ });
    const band = [...document.querySelectorAll("svg path")].find((p) => p.querySelector("title")?.textContent?.startsWith("1 file reached"));
    fireEvent.click(band!);
    expect(screen.getByText(/^Showing reached through/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Clear the filter/ }));
    expect(screen.queryByText(/^Showing/)).not.toBeInTheDocument();
  });

  it("on touch, the first tap only highlights and the second picks", async () => {
    // jsdom has no PointerEvent; a MouseEvent that carries pointerType stands in.
    class TouchPointer extends MouseEvent {
      pointerType: string;
      constructor(type: string, init: MouseEventInit & { pointerType?: string } = {}) {
        super(type, init);
        this.pointerType = init.pointerType ?? "mouse";
      }
    }
    vi.stubGlobal("PointerEvent", TouchPointer);
    renderView(<ImpactView adapter={makeAdapter({ analyze: analyze() })} initialFiles={[CORE]} />);
    const b = await screen.findByRole("button", { name: /^s\/b, 1 step away/ });
    fireEvent.pointerDown(b, { pointerType: "touch" });
    fireEvent.click(b);
    expect(b).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("tooltip")).toHaveTextContent("s/b");
    fireEvent.click(b);
    expect(b).toHaveAttribute("aria-pressed", "true");
    vi.unstubAllGlobals();
  });
});

describe("what the page counts", () => {
  it("never calls a file new since the last index untested, says so once, and counts only changed files with no test", async () => {
    const analyze = vi.fn(async () => ({
      ...RESULT,
      transitive_affected: [{ path: "src/dep.py", depth: 1, via: "src/old.py" }],
      test_gaps: ["src/new.py", "src/old.py", "src/dep.py"],
      affected_signals: [
        { path: "src/new.py", is_hotspot: null, health_score: null, module: null, indexed: false },
        { path: "src/old.py", is_hotspot: false, health_score: 8, module: "src", indexed: true },
        { path: "src/dep.py", is_hotspot: false, health_score: 8, module: "src", indexed: true },
      ],
      changed_owners: [{ email: "a@x.dev", name: "Ann", files: 1 }],
    }));
    renderView(<ImpactView adapter={makeAdapter({ analyze })} initialFiles={["src/new.py", "src/old.py"]} />);
    expect(await screen.findByText(/1 changed file is new since the last index/)).toBeInTheDocument();
    expect(screen.getByText(/That is 1 of the 2 changed files; the rest have no owner on record/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Changed files with no test/ })).toHaveTextContent("1");
    // Whether a dependent is tested is not about this change: no count, section or chip says it.
    expect(screen.queryByText(/Dependent files with no test/)).not.toBeInTheDocument();
    expect(screen.getByText("1 changed file with no test found.")).toBeInTheDocument();
  });
});

describe("a server that pages the reached files", () => {
  const SUMMARY = { files: 3000, tests: 0, direct: 3000, modules: 1, max_depth: 1, imported: ["src/core.ts"] };

  function paged(rows: string[], over: Partial<ChangeBlastRadiusResponse> = {}): ChangeBlastRadiusResponse {
    return {
      ...changeResult(["src/core.ts"]),
      transitive_affected: rows.map((path) => ({ path, depth: 1, via: null })),
      affected_signals: [],
      transitive_total: 3000,
      next_offset: rows.length,
      reach_summary: SUMMARY,
      ...over,
    };
  }

  it("counts every file, reads more on request, and draws a wide change's map when asked", async () => {
    const analyzeChange = vi.fn(async (input: { offset?: number; withGraph?: boolean }) => {
      if (input.withGraph)
        return paged(["src/a.ts", "src/b.ts"], {
          transitive_affected: [
            { path: "src/a.ts", depth: 1, via: "src/core.ts" },
            { path: "src/b.ts", depth: 1, via: "src/core.ts" },
          ],
        });
      return input.offset ? paged(["src/b.ts"], { next_offset: null }) : paged(["src/a.ts"]);
    });
    const adapter = makeAdapter({ listChanges: vi.fn(async () => CHANGES), analyzeChange });
    renderView(<ImpactView adapter={adapter} />);

    expect(await screen.findByText(/nearest 1 of 3,000 files/)).toBeInTheDocument();
    // Too wide to draw on arrival: the map waits for the reader.
    expect(analyzeChange).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Read more files" }));
    await waitFor(() => expect(analyzeChange).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 1 })));
    expect(await screen.findByText(/nearest 2 of 3,000 files/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Draw the map" }));
    await waitFor(() =>
      expect(analyzeChange).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 0, limit: 5000, withGraph: true })),
    );
    expect(await screen.findByText(/Drawn from the nearest 2 of 3,000 files/)).toBeInTheDocument();
  });

  it("draws the map on arrival for a change of ordinary reach", async () => {
    const small = { ...SUMMARY, files: 1, direct: 1 };
    const analyzeChange = vi.fn(async (input: { withGraph?: boolean }) =>
      paged(["src/a.ts"], {
        transitive_total: 1,
        next_offset: null,
        reach_summary: small,
        ...(input.withGraph ? { transitive_affected: [{ path: "src/a.ts", depth: 1, via: "src/core.ts" }] } : {}),
      }),
    );
    renderView(<ImpactView adapter={makeAdapter({ listChanges: vi.fn(async () => CHANGES), analyzeChange })} />);

    await waitFor(() => expect(analyzeChange).toHaveBeenCalledWith(expect.objectContaining({ withGraph: true })));
    expect(screen.queryByRole("button", { name: "Draw the map" })).not.toBeInTheDocument();
  });
});

describe("a cut test list", () => {
  it("says how many of the recommended tests it lists", async () => {
    const rec = (i: number) => ({
      test_id: `tests/test_${i}.py`,
      test_file: `tests/test_${i}.py`,
      repository_id: "repo-1",
      repository: "app",
      basis: "inferred" as const,
      bases: ["inferred" as const],
      source_files: ["src/core.ts"],
      evidence: [],
    });
    const freshness = { status: "unknown" as const, reason: null, ingested_commit: null, indexed_commit: null };
    const analyze = vi.fn(async (): Promise<BlastRadiusResponse> => ({
      ...RESULT,
      test_impact: {
        recommendations: [rec(1), rec(2)],
        recommendations_total: 150,
        recommendations_emitted: 2,
        recommendations_truncated: true,
        recommendations_omitted: 148,
        recommendations_by_primary_basis: { measured: 0, inferred: 150 },
        files: [],
        files_total: 1,
        files_without_measured_tests: [],
        unknown_files: [],
        coverage: {
          status: "unavailable" as const,
          reason: null,
          map_present: false,
          pair_count: 0,
          test_count: 0,
          source_file_count: 0,
          changed_files_total: 1,
          changed_files_with_measured_tests: 0,
          changed_files_without_measured_tests: 1,
          ingested_at: null,
          source_format: null,
          freshness,
        },
        inference: {
          status: "available" as const,
          reason: null,
          changed_files_total: 1,
          changed_files_with_candidates: 1,
          candidates_before_dedup: 150,
        },
        analysis: { status: "partial" as const, stale: false, partial: true, degraded: false, basis_categories: ["inferred" as const] },
      },
    }));
    renderView(<ImpactView adapter={makeAdapter({ analyze })} initialFiles={["src/core.ts"]} />);
    expect(await screen.findByText("The first 2 of 150 are listed here.")).toBeInTheDocument();
  });
});

describe("accessibility", () => {
  const CHECK: ChangeHealthCheckResponse = {
    change_id: "working-tree",
    revspec: "HEAD",
    status: "complete",
    explanation: null,
    scope: {},
    skipped: {},
    gate: { passed: false, verdict: "fail", fail_on: "introduced", fail_at: "high", failing_count: 1, warning_count: 0, note_count: 0, notes: [] },
    findings: [
      { source: "health", rule: "deep_nesting", level: "error", file_path: "src/a.py", line_number: 3, symbol: null, message: "Deep nesting", fingerprint: "1", change_kind: "introduced", severity: "high" },
    ],
  };
  const analyzeChange = () =>
    vi.fn(async () => ({
      ...changeResult(["packages/s/src/core/x.py"]),
      transitive_affected: [
        { path: "packages/s/src/a/1.py", depth: 1, via: "packages/s/src/core/x.py" },
        { path: "packages/s/src/b/1.py", depth: 2, via: "packages/s/src/a/1.py" },
      ],
      affected_signals: [{ path: "packages/s/src/a/1.py", is_hotspot: true, health_score: 3, module: "packages/s" }],
    }));

  // jsdom computes no layout, so contrast is checked on the tokens instead (scripts/contrast-check.py);
  // landmarks belong to the host page, not this view.
  const violations = async () => {
    const r = await axe.run(document.body, { rules: { "color-contrast": { enabled: false }, region: { enabled: false } } });
    return r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
  };

  it("passes axe on a reviewed change with its health check", async () => {
    const adapter = makeAdapter({
      listChanges: vi.fn(async () => ({ ...CHANGES, health_check_supported: true })),
      analyzeChange: analyzeChange(),
      checkHealth: vi.fn(async () => CHECK),
    });
    renderView(<ImpactView adapter={adapter} />);
    fireEvent.click(await screen.findByRole("button", { name: /Run health check/ }));
    await screen.findByText("Deep nesting");
    expect(await violations()).toEqual([]);
    // The open picker renders in a portal; it is checked too.
    await openPicker(/Change under review/);
    expect(await violations()).toEqual([]);
  });

  it("passes axe on the files form and on a change that fails to open", async () => {
    const first = renderView(<ImpactView adapter={makeAdapter()} />);
    await screen.findByRole("button", { name: "Add hotspot src/hot.ts" });
    expect(await violations()).toEqual([]);
    first.unmount();
    const analyzeChange = vi.fn().mockRejectedValue(new Error("API error 500: boom"));
    renderView(<ImpactView adapter={makeAdapter({ listChanges: vi.fn(async () => CHANGES), analyzeChange })} />);
    await screen.findByText("boom");
    expect(await violations()).toEqual([]);
  });

  it("names each finding's severity in words and announces only the outcomes", async () => {
    const adapter = makeAdapter({
      listChanges: vi.fn(async () => ({ ...CHANGES, health_check_supported: true })),
      analyzeChange: analyzeChange(),
      checkHealth: vi.fn(async () => CHECK),
    });
    const { container } = renderView(<ImpactView adapter={adapter} />);
    fireEvent.click(await screen.findByRole("button", { name: /Run health check/ }));
    await screen.findByText("Deep nesting");
    expect(screen.getByText("High")).not.toHaveClass("sr-only");
    expect(container.querySelector("[aria-live]")).toBeNull();
    const said = screen.getAllByRole("status").map((s) => s.textContent);
    expect(said).toEqual(expect.arrayContaining(["Review ready.", "Health check: Fail, 1 failing, 0 warnings."]));
  });
});
