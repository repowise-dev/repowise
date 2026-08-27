// @vitest-environment jsdom

import * as React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ExternalSystemsSummary } from "@repowise-dev/types/external-systems";

const mocks = vi.hoisted(() => ({
  setQueryStates: vi.fn(),
  setPackage: vi.fn(),
  setScope: vi.fn(),
  infiniteResult: {} as Record<string, unknown>,
  focusedResult: {} as Record<string, unknown>,
  summaryKey: "",
}));

vi.mock("next/link", () => ({
  default: ({ children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a {...props}>{children}</a>
  ),
}));

vi.mock("nuqs", () => {
  const parser = (defaultValue: unknown = null) => ({
    defaultValue,
    withDefault(value: unknown) {
      return parser(value);
    },
  });
  return {
    parseAsInteger: parser(),
    parseAsString: parser(),
    parseAsStringLiteral: () => parser(),
    useQueryState: (name: string, value: { defaultValue?: unknown }) => [
      value?.defaultValue ?? null,
      name === "package" ? mocks.setPackage : mocks.setScope,
    ],
    useQueryStates: (config: Record<string, { defaultValue?: unknown }>) => [
      Object.fromEntries(Object.entries(config).map(([key, value]) => [key, value.defaultValue])),
      mocks.setQueryStates,
    ],
  };
});

vi.mock("swr/infinite", () => ({
  default: (getKey: (index: number, previous: ExternalSystemsSummary | null) => string) => {
    mocks.summaryKey = getKey(0, null);
    return mocks.infiniteResult;
  },
}));

vi.mock("swr", () => ({ default: () => mocks.focusedResult }));
vi.mock("@repowise-dev/api-client", () => ({ apiGet: vi.fn() }));
vi.mock("@/lib/api/client", () => ({}));

vi.mock("@repowise-dev/ui/dependencies", async () => {
  const actual = await vi.importActual<typeof import("@repowise-dev/ui/dependencies")>(
    "@repowise-dev/ui/dependencies",
  );
  return {
    ...actual,
    ExternalDependenciesTable: (props: {
      onStateChange: (state: typeof actual.DEFAULT_EXTERNAL_DEPENDENCY_STATE) => void;
      onSelectedChange: (entry: { package_key: string }) => void;
    }) => (
      <div>
        <button
          onClick={() => props.onStateChange({ ...actual.DEFAULT_EXTERNAL_DEPENDENCY_STATE, query: "react", page: 2 })}
        >
          Change package query
        </button>
        <button onClick={() => props.onSelectedChange({ package_key: "npm:react" })}>
          Select package
        </button>
      </div>
    ),
  };
});

import { DependenciesView } from "./dependencies-view";

afterEach(cleanup);

const summary: ExternalSystemsSummary = {
  items: [],
  returned: 0,
  total_packages: 1,
  limit: 400,
  offset: 0,
  truncated: false,
  scope: "primary",
  excluded_declarations: 0,
  total_declarations: 1,
  runtime_packages: 1,
  dev_only_packages: 0,
  observed_packages: 0,
  linked_packages: 0,
  unlinked_packages: 1,
  linked_without_imports: 0,
  ecosystems: ["npm"],
  manifest_count: 1,
};

describe("DependenciesView wiring", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.infiniteResult = {
      data: [summary],
      error: undefined,
      isLoading: false,
      isValidating: false,
      mutate: vi.fn(),
      size: 1,
      setSize: vi.fn(),
    };
    mocks.focusedResult = { data: undefined, error: undefined, isLoading: false };
  });

  it("starts from the bounded summary key and does not activate a graph request", () => {
    render(<DependenciesView repoId="repo-id" />);
    expect(mocks.summaryKey).toBe("external-systems-summary:repo-id:primary:0");
    expect(mocks.focusedResult).toEqual({ data: undefined, error: undefined, isLoading: false });
  });

  it("renders loading and error states", () => {
    mocks.infiniteResult = { ...mocks.infiniteResult, data: undefined, isLoading: true };
    const { rerender } = render(<DependenciesView repoId="repo-id" />);
    expect(screen.getByLabelText("Loading external dependencies")).toBeTruthy();

    mocks.infiniteResult = { ...mocks.infiniteResult, isLoading: false, error: new Error("offline") };
    rerender(<DependenciesView repoId="repo-id" />);
    expect(screen.getByText("Couldn't load external dependencies")).toBeTruthy();
  });

  it("writes controlled filter and package selection state back to the URL layer", () => {
    render(<DependenciesView repoId="repo-id" />);
    fireEvent.click(screen.getByRole("button", { name: "Change package query" }));
    expect(mocks.setQueryStates).toHaveBeenCalledWith(expect.objectContaining({ q: "react", page: 2 }));

    fireEvent.click(screen.getByRole("button", { name: "Select package" }));
    expect(mocks.setPackage).toHaveBeenCalledWith("npm:react");
  });
});
