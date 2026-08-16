import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { FileDecisionsTab } from "../../src/files/file-decisions-tab.js";
import { FilePage } from "../../src/files/file-page.js";
import { FilePageHeader } from "../../src/files/file-page-header.js";
import { buildFilePanels } from "../../src/files/file-page-panels.js";
import { fileTabsFor, type FilePageTab } from "../../src/files/file-page-tabs.js";
import type { FileDetailResponse, GoverningDecisionRef } from "@repowise-dev/types/files";

// jsdom has no scrollIntoView; `ViewTabs` keeps the active tab in view on mount.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

function makeDecision(id: string, title: string, status: string): GoverningDecisionRef {
  return { id, title, status };
}

function makeMockFileData(decisions: GoverningDecisionRef[]): FileDetailResponse {
  return {
    file_path: "src/main.ts",
    wiki_page: null,
    health: {
      metric: null,
      breakdown: null,
      findings: [],
      trend: null,
      signals: null,
    },
    git: null,
    coverage: null,
    graph: null,
    symbols: [],
    function_blame: [],
    governing_decisions: decisions,
    dead_code: [],
  };
}

describe("FileDecisionsTab", () => {
  it("renders empty state when there are no decisions", () => {
    render(<FileDecisionsTab decisions={[]} linkPrefix="/repos/r1" />);
    expect(screen.getByText("No governing decisions")).toBeTruthy();
    expect(
      screen.getByText("This file is not directly linked to any architectural governing decisions."),
    ).toBeTruthy();
  });

  it("renders decision title, status badge, and link for provided decisions", () => {
    const decisions = [
      makeDecision("d1", "Use human-sounding docs style", "active"),
      makeDecision("d2", "Skip perf validation for test scripts", "proposed"),
    ];

    render(<FileDecisionsTab decisions={decisions} linkPrefix="/repos/r1" />);

    expect(screen.getByText("Use human-sounding docs style")).toBeTruthy();
    expect(screen.getByText("Skip perf validation for test scripts")).toBeTruthy();
    expect(screen.getByText("active")).toBeTruthy();
    expect(screen.getByText("proposed")).toBeTruthy();

    const links = screen.getAllByRole("link");
    expect(links.some((l) => l.getAttribute("href") === "/repos/r1/decisions/d1")).toBe(true);
    expect(links.some((l) => l.getAttribute("href") === "/repos/r1/decisions/d2")).toBe(true);
  });
});

/** The page as the route composes it: panels rendered ahead of the shell, the
 *  shell client-only. Written out here rather than hidden behind a helper so a
 *  regression that puts a tab body back inside the client bundle shows up as a
 *  test that no longer compiles. */
function renderPage(
  data: FileDetailResponse,
  extra: { initialTab?: FilePageTab; onTabChange?: (t: FilePageTab) => void } = {},
) {
  const tabs = fileTabsFor(data);
  const panels = buildFilePanels({
    data,
    linkPrefix: "/repos/r1",
    fileHref: (p) => `/repos/r1/files/${p}`,
    symbolHref: (s) => `/repos/r1/symbols/${s}`,
  });
  return render(
    <FilePage
      header={<FilePageHeader data={data} linkPrefix="/repos/r1" />}
      tabs={tabs}
      panels={panels}
      {...extra}
    />,
  );
}

describe("FilePage — the tab row", () => {
  it("badges the Decisions tab with its count when decisions exist", () => {
    const data = makeMockFileData([
      makeDecision("d1", "Keep branch names generic", "active"),
      makeDecision("d2", "Use TypeScript strictly", "active"),
    ]);

    renderPage(data);

    expect(screen.getByRole("tab", { name: /decisions 2/i })).toBeTruthy();
  });

  it("drops the Decisions tab entirely when governing_decisions is empty", () => {
    renderPage(makeMockFileData([]));

    expect(screen.queryByRole("tab", { name: /decisions/i })).toBeNull();
  });

  it("shows the decisions panel and reports the change when the tab is clicked", () => {
    const data = makeMockFileData([makeDecision("d1", "Single decision title", "active")]);
    const onTabChange = vi.fn();

    renderPage(data, { onTabChange });
    fireEvent.click(screen.getByRole("tab", { name: /decisions 1/i }));

    expect(onTabChange).toHaveBeenCalledWith("decisions");
    expect(
      screen.getByRole("tab", { name: /decisions 1/i }).getAttribute("aria-selected"),
    ).toBe("true");
    expect(screen.getByText("Single decision title")).toBeTruthy();
  });

  it("falls back to overview if initialTab is decisions but governing_decisions is empty", () => {
    renderPage(makeMockFileData([]), { initialTab: "decisions" });

    expect(screen.getByRole("tab", { name: /overview/i }).getAttribute("aria-selected")).toBe(
      "true",
    );
  });
});
