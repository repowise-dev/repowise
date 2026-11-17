// @vitest-environment jsdom

import React from "react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { SWRConfig } from "swr";
import { TestsView, coverageCommandForLanguages } from "./tests-view";

const mocks = vi.hoisted(() => ({
  getOverviewSummary: vi.fn(),
  coverageViewProps: [] as Array<{ coverageCommand?: string }>,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/lib/api/overview", () => ({
  getOverviewSummary: mocks.getOverviewSummary,
}));

vi.mock("@repowise-dev/ui/health", async () => {
  const actual = await vi.importActual<typeof import("@repowise-dev/ui/health")>("@repowise-dev/ui/health");
  return {
    ...actual,
    CoverageView: (props: { coverageCommand?: string }) => {
      mocks.coverageViewProps.push(props);
      return (
        <div data-testid="coverage-view" data-command={props.coverageCommand}>
          {props.coverageCommand ?? "neutral-default"}
        </div>
      );
    },
  };
});

function renderTestsView(repoId = "repo-123") {
  return render(
    <SWRConfig value={{ provider: () => new Map(), errorRetryCount: 0, dedupingInterval: 0 }}>
      <TestsView repoId={repoId} />
    </SWRConfig>,
  );
}

describe("coverageCommandForLanguages", () => {
  it("returns Go test and coverage add commands for Go repos", () => {
    const cmd = coverageCommandForLanguages([{ language: "Go" }]);
    expect(cmd).toBe("go test -coverprofile=coverage.out ./...\nrepowise coverage add coverage.out");
  });

  it("returns npm test and coverage add commands for TypeScript repos", () => {
    const cmd = coverageCommandForLanguages([{ language: "TypeScript" }, { language: "JavaScript" }]);
    expect(cmd).toBe("npm test -- --coverage\nrepowise coverage add coverage/lcov.info");
  });

  it("returns npm test and coverage add commands for JavaScript repos", () => {
    const cmd = coverageCommandForLanguages([{ language: "javascript" }]);
    expect(cmd).toBe("npm test -- --coverage\nrepowise coverage add coverage/lcov.info");
  });

  it("returns pytest and coverage add commands for Python repos", () => {
    const cmd = coverageCommandForLanguages([{ language: "python" }]);
    expect(cmd).toBe("pytest --cov --cov-report=lcov\nrepowise coverage add coverage.lcov");
  });

  it("returns cargo llvm-cov commands for Rust repos", () => {
    const cmd = coverageCommandForLanguages([{ language: "Rust" }]);
    expect(cmd).toBe("cargo llvm-cov --lcov --output-path coverage/lcov.info\nrepowise coverage add coverage/lcov.info");
  });

  it("returns mvn test jacoco commands for Java repos", () => {
    const cmd = coverageCommandForLanguages([{ language: "Java" }]);
    expect(cmd).toBe("mvn test jacoco:report\nrepowise coverage add target/site/jacoco/jacoco.xml");
  });

  it("returns mvn test jacoco commands for Kotlin repos", () => {
    const cmd = coverageCommandForLanguages([{ language: "Kotlin" }]);
    expect(cmd).toBe("mvn test jacoco:report\nrepowise coverage add target/site/jacoco/jacoco.xml");
  });

  it("returns undefined for empty, unknown, or missing languages to trigger the language-neutral default", () => {
    expect(coverageCommandForLanguages(undefined)).toBeUndefined();
    expect(coverageCommandForLanguages([])).toBeUndefined();
    expect(coverageCommandForLanguages([{ language: "brainfuck" }])).toBeUndefined();
  });
});

describe("TestsView component", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.coverageViewProps.length = 0;
  });

  afterEach(() => {
    cleanup();
  });

  it("renders CoverageView without blocking when overview summary is pending", () => {
    // Return a pending promise that never resolves during initial render
    mocks.getOverviewSummary.mockReturnValue(new Promise(() => {}));

    renderTestsView("repo-test-pending");

    // CoverageView is rendered immediately, not blocked or hidden
    expect(screen.getByTestId("coverage-view")).toBeTruthy();
    expect(mocks.coverageViewProps[0]?.coverageCommand).toBeUndefined();
  });

  it("falls back to undefined (neutral command) without blocking when overview summary fails", async () => {
    // Simulate /overview-summary error (e.g. 500, network error, sub-section error)
    mocks.getOverviewSummary.mockRejectedValue(new Error("Overview summary failed"));

    renderTestsView("repo-test-error");

    // CoverageView is still rendered, not blocked by ApiError
    await waitFor(() => {
      expect(screen.getByTestId("coverage-view")).toBeTruthy();
    });
    // coverageCommand remains undefined, falling back to neutral command
    expect(screen.getByTestId("coverage-view").getAttribute("data-command")).toBeNull();
    expect(mocks.coverageViewProps[mocks.coverageViewProps.length - 1]?.coverageCommand).toBeUndefined();
  });

  it("passes language-specific command to CoverageView when overview summary resolves", async () => {
    mocks.getOverviewSummary.mockResolvedValue({ languages: [{ language: "Go" }] });

    renderTestsView("repo-test-go");

    await waitFor(() => {
      expect(screen.getByTestId("coverage-view").getAttribute("data-command")).toBe(
        "go test -coverprofile=coverage.out ./...\nrepowise coverage add coverage.out",
      );
    });
  });

  it("leaves coverageCommand undefined when repository has no detected languages", async () => {
    mocks.getOverviewSummary.mockResolvedValue({ languages: [] });

    renderTestsView("repo-test-empty");

    await waitFor(() => {
      expect(screen.getByTestId("coverage-view")).toBeTruthy();
    });
    expect(screen.getByTestId("coverage-view").getAttribute("data-command")).toBeNull();
    expect(mocks.coverageViewProps[mocks.coverageViewProps.length - 1]?.coverageCommand).toBeUndefined();
  });
});
