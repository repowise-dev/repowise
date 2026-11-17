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

  it("shows CoverageSkeleton while overview summary is loading and does not render CoverageView prematurely", async () => {
    let resolveSummary: (value: unknown) => void;
    const summaryPromise = new Promise((resolve) => {
      resolveSummary = resolve;
    });
    mocks.getOverviewSummary.mockReturnValue(summaryPromise);

    renderTestsView("repo-test-1");

    // Skeleton is rendered while pending
    expect(screen.getByTestId("coverage-skeleton")).toBeTruthy();
    // CoverageView is NOT rendered yet (preventing premature flicker / fallback command swap)
    expect(screen.queryByTestId("coverage-view")).toBeNull();
    expect(mocks.coverageViewProps).toHaveLength(0);

    // Resolve with Go repository
    resolveSummary!({ languages: [{ language: "Go" }] });

    // Once resolved, CoverageView is mounted directly with Go command
    await waitFor(() => {
      expect(screen.getByTestId("coverage-view")).toBeTruthy();
    });
    expect(screen.queryByTestId("coverage-skeleton")).toBeNull();
    expect(screen.getByTestId("coverage-view").getAttribute("data-command")).toBe(
      "go test -coverprofile=coverage.out ./...\nrepowise coverage add coverage.out",
    );
    // Verified that CoverageView never received undefined or another command before Go
    expect(mocks.coverageViewProps[0]?.coverageCommand).toBe(
      "go test -coverprofile=coverage.out ./...\nrepowise coverage add coverage.out",
    );
  });

  it("surfaces ApiError when overview summary fails instead of silently falling back", async () => {
    mocks.getOverviewSummary.mockRejectedValue(new Error("Network timeout loading summary"));

    renderTestsView("repo-test-2");

    await waitFor(() => {
      expect(screen.getByText("Couldn't load repository summary")).toBeTruthy();
    });
    expect(screen.getByText("Network timeout loading summary")).toBeTruthy();
    expect(screen.queryByTestId("coverage-skeleton")).toBeNull();
    expect(screen.queryByTestId("coverage-view")).toBeNull();
  });

  it("renders CoverageView directly with language-specific command for Python", async () => {
    mocks.getOverviewSummary.mockResolvedValue({ languages: [{ language: "Python" }] });

    renderTestsView("repo-test-3");

    await waitFor(() => {
      expect(screen.getByTestId("coverage-view")).toBeTruthy();
    });
    expect(screen.getByTestId("coverage-view").getAttribute("data-command")).toBe(
      "pytest --cov --cov-report=lcov\nrepowise coverage add coverage.lcov",
    );
  });

  it("renders CoverageView with undefined (neutral fallback) when repository has no detected languages", async () => {
    mocks.getOverviewSummary.mockResolvedValue({ languages: [] });

    renderTestsView("repo-test-4");

    await waitFor(() => {
      expect(screen.getByTestId("coverage-view")).toBeTruthy();
    });
    expect(screen.getByTestId("coverage-view").getAttribute("data-command")).toBeNull();
    expect(mocks.coverageViewProps[0]?.coverageCommand).toBeUndefined();
  });
});
