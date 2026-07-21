import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { FileOverviewTab } from "../../src/files/file-overview-tab.js";
import type {
  FileDetailGit,
  FileDetailResponse,
  FileSymbolSlim,
} from "@repowise-dev/types/files";

function symbol(name: string, complexity: number): FileSymbolSlim {
  return {
    symbol_id: `src/foo.ts::${name}`,
    name,
    kind: "function",
    signature: `${name}()`,
    start_line: 1,
    end_line: 10,
    visibility: "public",
    complexity_estimate: complexity,
    is_async: false,
  };
}

function makeGit(overrides: Partial<FileDetailGit> = {}): FileDetailGit {
  return {
    file_path: "src/foo.ts",
    commit_count_total: 40,
    commit_count_90d: 8,
    commit_count_30d: 2,
    churn_percentile: 92,
    primary_owner: "Ada",
    is_hotspot: true,
    is_stable: false,
    bus_factor: 2,
    contributor_count: 4,
    lines_added_90d: 100,
    lines_deleted_90d: 40,
    avg_commit_size: 30,
    commit_categories: {},
    significant_commits: [],
    top_authors: [],
    co_change_partners: [],
    agent: { agent_commit_count: 0, agent_authored_pct: null, tier_counts: {} },
    first_commit_at: null,
    ...overrides,
  };
}

function makeData(
  symbols: FileSymbolSlim[],
  git: FileDetailGit | null = null,
): FileDetailResponse {
  return {
    file_path: "src/foo.ts",
    wiki_page: null,
    health: { metric: null, breakdown: null, findings: [], trend: null, signals: null },
    git,
    coverage: null,
    graph: null,
    symbols,
    function_blame: [],
    governing_decisions: [],
    dead_code: [],
  } as unknown as FileDetailResponse;
}

const symbolHref = (id: string) => `/symbols/${id}`;
const fileHref = (p: string) => `/files/${p}`;

function renderTab(data: FileDetailResponse) {
  return render(<FileOverviewTab data={data} symbolHref={symbolHref} fileHref={fileHref} />);
}

describe("FileOverviewTab symbol fix counts", () => {
  it("marks only the symbols that were actually fixed", () => {
    renderTab(
      makeData(
        [symbol("run", 20), symbol("parse", 15)],
        makeGit({ fix_symbol_counts: { "src/foo.ts::run": 3 } }),
      ),
    );
    const run = screen.getByText("run").closest("a");
    const parse = screen.getByText("parse").closest("a");
    expect(run?.textContent).toContain("3");
    expect(parse?.textContent).not.toMatch(/\d/);
  });

  it("leaves the chip row untouched when the index carries no rollup", () => {
    renderTab(makeData([symbol("run", 20)], makeGit()));
    expect(screen.getByText("run").closest("a")?.textContent).toBe("runfunction");
  });

  it("adds nothing when the file has no git metadata at all", () => {
    renderTab(makeData([symbol("run", 20)], null));
    expect(screen.getByText("run").closest("a")?.textContent).toBe("runfunction");
  });

  it("reserves the last slot for the most-fixed symbol", () => {
    // Twenty symbols, eight chips. `edge` is the least complex, so complexity
    // alone would hide the one thing worth knowing about.
    const symbols = [
      ...Array.from({ length: 19 }, (_, i) => symbol(`big${i}`, 100 - i)),
      symbol("edge", 1),
    ];
    renderTab(makeData(symbols, makeGit({ fix_symbol_counts: { "src/foo.ts::edge": 5 } })));
    expect(screen.getByText("edge")).toBeTruthy();
    // It takes the last slot only, so the most complex symbols all survive.
    expect(screen.getByText("big0")).toBeTruthy();
    expect(screen.getByText("big6")).toBeTruthy();
    expect(screen.queryByText("big7")).toBeNull();
  });

  it("does not evict the file's most complex symbol for trivial fixed helpers", () => {
    // Ten one-fix helpers plus one very complex, never-fixed symbol. Sorting
    // the whole list on "was fixed" would drop `dispatch` off the panel.
    const symbols = [
      symbol("dispatch", 90),
      ...Array.from({ length: 10 }, (_, i) => symbol(`helper${i}`, i + 1)),
      ...Array.from({ length: 12 }, (_, i) => symbol(`filler${i}`, 50 + i)),
    ];
    const fix_symbol_counts = Object.fromEntries(
      Array.from({ length: 10 }, (_, i) => [`src/foo.ts::helper${i}`, 1]),
    );
    renderTab(makeData(symbols, makeGit({ fix_symbol_counts })));
    expect(screen.getByText("dispatch")).toBeTruthy();
  });

  it("keeps the most-fixed symbol ahead of a merely-fixed one", () => {
    const symbols = [
      symbol("parse", 3),
      ...Array.from({ length: 19 }, (_, i) => symbol(`big${i}`, 100 - i)),
    ];
    renderTab(
      makeData(
        symbols,
        makeGit({ fix_symbol_counts: { "src/foo.ts::parse": 12, "src/foo.ts::big0": 1 } }),
      ),
    );
    expect(screen.getByText("parse")).toBeTruthy();
  });

  it("stands down when a fix touched most of the file", () => {
    // One commit that fixed a bug and reformatted the file marks every symbol.
    // That map has no per-symbol signal, so marking all eight chips is noise.
    const symbols = Array.from({ length: 10 }, (_, i) => symbol(`s${i}`, 100 - i));
    const fix_symbol_counts = Object.fromEntries(
      symbols.map((s) => [s.symbol_id, 1]),
    );
    renderTab(makeData(symbols, makeGit({ fix_symbol_counts })));
    expect(screen.queryByTitle(/matched by line range/)).toBeNull();
  });

  it("still marks when the fixes are concentrated", () => {
    const symbols = Array.from({ length: 10 }, (_, i) => symbol(`s${i}`, 100 - i));
    renderTab(makeData(symbols, makeGit({ fix_symbol_counts: { "src/foo.ts::s0": 4 } })));
    expect(screen.getByText("s0").closest("a")?.textContent).toContain("4");
  });
});
