import { beforeAll, describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { FilesIndex } from "../../src/files/files-index.js";
import type { FileLanguageCount, FileRow } from "@repowise-dev/types/files";

// jsdom has no layout engine → stub ResizeObserver so the treemap can size
// itself. Same shape as the code-health map's stub.
beforeAll(() => {
  class RO {
    cb: ResizeObserverCallback;
    constructor(cb: ResizeObserverCallback) {
      this.cb = cb;
    }
    observe() {
      this.cb(
        [{ contentRect: { width: 800, height: 400 } } as ResizeObserverEntry],
        this as unknown as ResizeObserver,
      );
    }
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", RO);
});

function row(overrides: Partial<FileRow> = {}): FileRow {
  return {
    file_path: "src/foo.ts",
    language: "typescript",
    loc: 100,
    symbol_count: 4,
    pagerank_pct: 50,
    in_degree: 2,
    out_degree: 3,
    defect_score: 9,
    maintainability_score: 9,
    performance_score: 9,
    churn_pct: 20,
    commit_count: 10,
    last_commit_at: null,
    coverage_pct: 80,
    is_test: false,
    is_entry_point: false,
    community_id: 0,
    ...overrides,
  };
}

const LANGS: FileLanguageCount[] = [
  { language: "typescript", count: 2 },
  { language: "python", count: 1 },
];

function renderIndex(files: FileRow[], languages: FileLanguageCount[] = LANGS) {
  return render(<FilesIndex files={files} languages={languages} fileHref={(p) => `/f/${p}`} />);
}

/** The table's half of the page, for names the map also uses. */
function tableSection(): HTMLElement {
  return screen.getByRole("heading", { name: "Every file" }).closest("section")!;
}

/** The map's key row: swatches on the left, what area means on the right. */
function keyRow(): HTMLElement {
  return screen.getByText(/^Area is/).parentElement!;
}

/**
 * The sentence under a heading, as one string.
 *
 * `getByText` matches a node's *direct* text children, so any sentence with a
 * figure spanned inside it is invisible to it — which is every sentence on this
 * page, since the figures are marked up rather than plain.
 */
function sentenceUnder(heading: string): HTMLElement {
  return screen.getByRole("heading", { name: heading }).nextElementSibling as HTMLElement;
}

describe("FilesIndex", () => {
  it("leads with the map, not a figure strip", () => {
    renderIndex([row()]);

    // The two section headings, in the order the page renders them. The map
    // has to be first: a hero figure above a canvas answers a question the
    // reader did not arrive with.
    const headings = screen.getAllByRole("heading", { level: 2 });
    expect(headings.map((h) => h.textContent)).toEqual(["Repository map", "Every file"]);
  });

  it("reports the healthy share against the number that carries a score", () => {
    renderIndex([
      row({ file_path: "a.ts", defect_score: 9 }),
      row({ file_path: "b.ts", defect_score: 7.5 }),
      row({ file_path: "c.ts", defect_score: null }),
    ]);

    // 2 of 3 files are scored, and only the 9 is Healthy — 7.5 sits in the
    // Warning band. A local `>= 7` threshold would call this 100%, disagreeing
    // with the amber tile the map paints for the same file.
    expect(screen.getByText(/50%/)).toBeInTheDocument();
    expect(screen.getByText(/carrying a health score are healthy/)).toBeInTheDocument();
  });

  it("names the active sort rather than claiming a fixed order", () => {
    renderIndex([row({ file_path: "a.ts" }), row({ file_path: "b.ts" })]);

    expect(screen.getByText(/sorted by dependents/)).toBeInTheDocument();

    // The map's colour control also offers "Health", so reach for the one
    // inside the table's section.
    fireEvent.click(within(tableSection()).getByRole("button", { name: "Health" }));
    expect(screen.getByText(/sorted by health/)).toBeInTheDocument();
  });

  it("keeps distinct percentiles distinct in the dependents column", () => {
    // Both round to 100. The column printed `Math.round(pagerank_pct)`, so the
    // rows that sort first were exactly the rows whose figures collapsed into
    // one another.
    renderIndex([
      row({ file_path: "a.ts", pagerank_pct: 100 }),
      row({ file_path: "b.ts", pagerank_pct: 99.6 }),
    ]);

    expect(screen.getByText("100.0")).toBeInTheDocument();
    expect(screen.getByText("99.6")).toBeInTheDocument();
  });

  it("filters the table without disturbing the map's own scope", () => {
    renderIndex([
      row({ file_path: "src/app.ts" }),
      row({ file_path: "tests/app_test.ts", is_test: true }),
    ]);

    expect(sentenceUnder("Every file")).toHaveTextContent("2 files, sorted by dependents");

    fireEvent.click(screen.getByRole("button", { name: "Tests" }));
    expect(sentenceUnder("Every file")).toHaveTextContent("1 file, sorted by dependents");
    // The map still draws both: the toolbar scopes the table, not the canvas.
    expect(keyRow()).toHaveTextContent("2 items at the repository root");
  });
});

describe("FilesTreemap key row", () => {
  it("names every health band, and the thresholds that define them", () => {
    renderIndex([row({ defect_score: 9 })]);

    // All three even though this level only carries one: a fixed scale showing
    // two steps reads as a scale that has two.
    for (const band of ["Healthy", "Warning", "Alert"]) {
      expect(screen.getByText(band)).toBeInTheDocument();
    }
    expect(screen.getByText("8+")).toBeInTheDocument();
    expect(screen.getByText("4–8")).toBeInTheDocument();
    expect(screen.getByText("< 4")).toBeInTheDocument();
  });

  it("marks unscored tiles only when there are some", () => {
    const { unmount } = renderIndex([row({ defect_score: 9 })]);
    expect(screen.queryByText("Not scored")).not.toBeInTheDocument();
    unmount();

    renderIndex([row({ defect_score: null })]);
    expect(screen.getByText("Not scored")).toBeInTheDocument();
  });

  it("keys the languages actually on screen when colouring by language", () => {
    renderIndex([
      row({ file_path: "a.ts", language: "typescript" }),
      row({ file_path: "b.py", language: "python" }),
    ]);

    fireEvent.click(screen.getByRole("button", { name: "Language" }));

    const key = screen.getByText(/^Area is/).parentElement!;
    expect(within(key).getByText("typescript")).toBeInTheDocument();
    expect(within(key).getByText("python")).toBeInTheDocument();
    // Health bands belong to the other mode and must not linger.
    expect(within(key).queryByText("Healthy")).not.toBeInTheDocument();
  });

  it("says what area means, and it follows the control", () => {
    renderIndex([row()]);

    expect(
      screen.getByText(/Area is how much the rest of the codebase depends on it/),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Lines" }));
    expect(screen.getByText(/Area is lines of code/)).toBeInTheDocument();
  });

  it("counts what is drawn, from the array the tiles are drawn from", () => {
    // Three files under one root folder is one tile, not three.
    renderIndex([
      row({ file_path: "src/a.ts" }),
      row({ file_path: "src/b.ts" }),
      row({ file_path: "src/c.ts" }),
    ]);

    expect(keyRow()).toHaveTextContent("1 item at the repository root");
  });
});
