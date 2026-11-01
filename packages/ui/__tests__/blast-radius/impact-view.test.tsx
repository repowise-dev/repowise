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

describe("pull request links", () => {
  it("reads every picker id pasted as is, so ?change=commit:<sha> opens the commit", () => {
    const sha = "e".repeat(40);
    for (const id of [`commit:${sha}`, "branch:feat/x", "pr:9", "working-tree"]) {
      expect(changeIdFromLink(id)).toBe(id);
      expect(changeIdFromLink(changeLinkParam(id))).toBe(id);
    }
    // A branch named like a picker id keeps its prefix both ways.
    expect(changeLinkParam("branch:working-tree")).toBe("branch:working-tree");
    expect(changeIdFromLink(changeLinkParam("branch:working-tree"))).toBe("branch:working-tree");
    // A malformed commit id stays a branch name rather than a bad commit.
    expect(changeIdFromLink("commit:abc")).toBe("branch:commit:abc");
  });

  it("passes pr:<n> through both ways, never as a branch", () => {
    expect(changeIdFromLink("pr:123")).toBe("pr:123");
    expect(changeLinkParam("pr:123")).toBe("pr:123");
    // A branch literally named like a PR number keeps the branch reading.
    expect(changeIdFromLink("pr-123")).toBe("branch:pr-123");
  });

  it("names an unlisted PR from its id and renders a listed one as #n title with the author", async () => {
    const { changeFor } = await import("../../src/blast-radius/impact-view.js");
    const { changeName, changeMeta } = await import("../../src/blast-radius/change-picker.js");
    const unlisted = changeFor("pr:7", [])!;
    expect([unlisted.kind, unlisted.label]).toEqual(["pull_request", "#7"]);
    const listed = {
      ...unlisted,
      id: "pr:123",
      label: "#123",
      number: 123,
      title: "Fix the cache",
      author: "Dev",
      base: "main",
      head_sha: "b".repeat(40),
      files_changed: 4,
    };
    expect(changeName(listed)).toBe("#123 Fix the cache");
    expect(changeMeta(listed)).toContain("Dev");
    expect(changeMeta(listed)).toContain("4 files");
  });
});

describe("a host with no checkout", () => {
  it("offers pull requests, hides the working tree and the health check, and opens a PR", async () => {
    const pr = {
      id: "pr:123",
      kind: "pull_request" as const,
      label: "#123",
      number: 123,
      title: "Fix the cache",
      author: "Dev",
      committed_at: null,
      head_sha: "b".repeat(40),
      base: "main",
      ahead: null,
      files_changed: 3,
      is_current: false,
    };
    const analyzeChange = vi.fn(async () => changeResult(["src/core.ts"]));
    const checkHealth = vi.fn();
    const adapter = makeAdapter({
      listChanges: vi.fn(async () => ({ working_tree_supported: false, default_id: "pr:123", changes: [pr] })),
      analyzeChange,
      checkHealth,
    });
    renderView(<ImpactView adapter={adapter} />);
    await waitFor(() => expect(analyzeChange).toHaveBeenCalledWith({ changeId: "pr:123", maxDepth: 3 }));
    await openPicker(/Change under review: #123 Fix the cache/);
    expect(screen.getByRole("radio", { name: /Pull requests/ })).toBeInTheDocument();
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

describe("the PR bot line", () => {
  it("shows the bot's health before and after for a PR that carries it, and nothing otherwise", async () => {
    const { PrReviewLine } = await import("../../src/blast-radius/blast-radius-verdict.js");
    const { container, unmount } = render(
      <PrReviewLine review={{ health_before: 7.8, health_after: 4.9, introduced: 2, url: "https://example.test/pr/9" }} />,
    );
    expect(container.textContent).toContain("PR bot:");
    expect(container.textContent).toContain("health 7.8");
    expect(container.textContent).toContain("4.9");
    expect(container.textContent).toContain("2 new findings");
    expect(screen.getByRole("link", { name: "View" })).toHaveAttribute("href", "https://example.test/pr/9");
    unmount();
    // A non-web link is dropped, and a review with nothing to say renders nothing.
    const bad = render(<PrReviewLine review={{ introduced: 1, url: "javascript:alert(1)" }} />);
    expect(bad.container.textContent).toContain("1 new finding");
    expect(bad.container.querySelector("a")).toBeNull();
    bad.unmount();
    expect(render(<PrReviewLine review={{}} />).container).toBeEmptyDOMElement();

    const pr = {
      id: "pr:9",
      kind: "pull_request" as const,
      label: "#9",
      number: 9,
      title: "Tidy",
      author: null,
      committed_at: null,
      head_sha: null,
      base: "main",
      ahead: null,
      files_changed: 1,
      is_current: false,
    };
    const listChanges = vi.fn(async () => ({
      working_tree_supported: false,
      default_id: "pr:9",
      changes: [{ ...pr, pr_review: { health_before: 8, health_after: 8, introduced: 0 } }, { ...pr, id: "pr:10", label: "#10", number: 10 }],
    }));
    const analyzeChange = vi.fn(async () => changeResult(["src/core.ts"]));
    renderView(<ImpactView adapter={makeAdapter({ listChanges, analyzeChange })} />);
    expect(await screen.findByText(/no new findings/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "View" })).not.toBeInTheDocument();
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
