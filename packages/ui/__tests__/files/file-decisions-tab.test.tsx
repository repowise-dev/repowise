import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { FileDecisionsTab } from "../../src/files/file-decisions-tab.js";
import { FilePage } from "../../src/files/file-page.js";
import type { FileDetailResponse, GoverningDecisionRef } from "@repowise-dev/types/files";

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

describe("FilePage - Governing Decisions Header & Tab", () => {
  it("renders header summary and Decisions tab when decisions exist", () => {
    const decisions = [
      makeDecision("d1", "Keep branch names generic", "active"),
      makeDecision("d2", "Use TypeScript strictly", "active"),
    ];
    const data = makeMockFileData(decisions);

    render(<FilePage data={data} repoId="r1" />);

    expect(screen.getByText("Governed by")).toBeTruthy();
    expect(screen.getByText("2 decisions")).toBeTruthy();
    expect(screen.getByRole("tab", { name: /decisions 2/i })).toBeTruthy();
  });

  it("hides header summary line and Decisions tab when governing_decisions is empty", () => {
    const data = makeMockFileData([]);

    render(<FilePage data={data} repoId="r1" />);

    expect(screen.queryByText("Governed by")).toBeNull();
    expect(screen.queryByRole("tab", { name: /decisions/i })).toBeNull();
  });

  it("switches to Decisions tab when clicking header summary line", () => {
    const decisions = [makeDecision("d1", "Single decision title", "active")];
    const data = makeMockFileData(decisions);
    const onTabChange = vi.fn();

    render(<FilePage data={data} repoId="r1" onTabChange={onTabChange} />);

    const headerButton = screen.getByRole("button", { name: /governed by 1 decision/i });
    fireEvent.click(headerButton);

    expect(onTabChange).toHaveBeenCalledWith("decisions");
    expect(screen.getByRole("tab", { name: /decisions 1/i }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByText("Single decision title")).toBeTruthy();
  });

  it("falls back to overview if initialTab is decisions but governing_decisions is empty", () => {
    const data = makeMockFileData([]);

    render(<FilePage data={data} repoId="r1" initialTab="decisions" />);

    expect(screen.getByRole("tab", { name: /overview/i }).getAttribute("aria-selected")).toBe("true");
  });
});
