/**
 * The file page's `?line=N` deep link: which tab's source carries the mark.
 * The panels themselves are tested in `@repowise-dev/ui`; this checks the
 * host decides from the tab the page actually opens on.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import type { FileDetailResponse } from "@repowise-dev/types/files";

const mocks = vi.hoisted(() => ({
  getFileDetail: vi.fn(),
  getFileContent: vi.fn(),
  panelArgs: [] as Array<Record<string, unknown>>,
}));

vi.mock("@/lib/api/files", () => ({
  getFileDetail: mocks.getFileDetail,
  getFileContent: mocks.getFileContent,
}));
vi.mock("@repowise-dev/ui/files", async (importActual) => {
  const actual = await importActual<typeof import("@repowise-dev/ui/files")>();
  return {
    ...actual,
    buildFilePanels: (args: Parameters<typeof actual.buildFilePanels>[0]) => {
      mocks.panelArgs.push(args as unknown as Record<string, unknown>);
      return actual.buildFilePanels(args);
    },
  };
});

import FileEntityPage from "./page";

function makeDetail(overrides: Partial<FileDetailResponse> = {}): FileDetailResponse {
  return {
    file_path: "src/app.py",
    wiki_page: null,
    health: { metric: null, breakdown: null, findings: [], trend: null, signals: null },
    git: null,
    coverage: null,
    graph: null,
    symbols: [],
    function_blame: [],
    function_blame_count: 0,
    governing_decisions: [],
    dead_code: [],
    ...overrides,
  };
}

const SOURCE = "a = 1\nb = 2\nc = 3\n";

async function renderPage(tab: string | undefined, line?: string) {
  mocks.panelArgs.length = 0;
  await FileEntityPage({
    params: Promise.resolve({ id: "repo-1", path: ["src", "app.py"] }),
    searchParams: Promise.resolve({ ...(tab ? { tab } : {}), ...(line ? { line } : {}) }),
  });
  return mocks.panelArgs[0]!;
}

describe("file page ?line=N", () => {
  beforeEach(() => {
    mocks.getFileContent.mockResolvedValue(SOURCE);
  });

  it("shows the linked source when ?tab=decisions falls back to Overview", async () => {
    // No governing decisions, so the Decisions tab is not in the row.
    mocks.getFileDetail.mockResolvedValue(makeDetail());
    const args = await renderPage("decisions", "2");
    expect(args.sourceCodeHtml).toContain('data-line="2"');
    expect(args.highlightLine).toBe(2);
  });

  it("keeps the Overview source when the link opens another tab", async () => {
    // The host switches back to Overview client-side with `line` still in the URL.
    mocks.getFileDetail.mockResolvedValue(makeDetail());
    const args = await renderPage("health", "2");
    expect(args.sourceCodeHtml).toContain('data-line="2"');
  });

  it("still marks the line in a file past the highlight cap, as escaped plain text", async () => {
    mocks.getFileContent.mockResolvedValue(`a = 1\nb = "<x>"\n${"#".repeat(120_000)}\n`);
    mocks.getFileDetail.mockResolvedValue(makeDetail());
    const args = await renderPage(undefined, "2");
    expect(args.sourceCodeHtml).toContain('<span class="line" data-line="2">b = "&lt;x&gt;"</span>');
  });

  it("fetches no source without a line link", async () => {
    mocks.getFileContent.mockClear();
    mocks.getFileDetail.mockResolvedValue(makeDetail());
    const args = await renderPage("health");
    expect(args.sourceCodeHtml).toBeUndefined();
    expect(mocks.getFileContent).not.toHaveBeenCalled();
  });

  it("renders untinted source on Coverage for a file no test ran", async () => {
    mocks.getFileDetail.mockResolvedValue(
      makeDetail({
        coverage: {
          line_coverage_pct: 0,
          branch_coverage_pct: null,
          total_coverable_lines: 3,
          covered_line_count: 0,
          covered_lines: [],
          source_format: "lcov",
          ingested_at: null,
          ingested_commit_sha: null,
        },
      }),
    );
    const args = await renderPage("coverage", "3");
    expect(args.coverageCodeHtml).toContain('data-line="3"');
    expect(args.coverageCodeHtml).not.toContain("data-covered");
  });
});
