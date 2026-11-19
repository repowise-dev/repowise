// @vitest-environment jsdom

/**
 * The costs page's host-owned behaviour: the time range reaching both
 * endpoints, the reset notice's storage key, the one setup nudge and its
 * gating and dismissal, and the error state. The body itself is tested in
 * `@repowise-dev/ui`.
 */

import React from "react";
import { SWRConfig } from "swr";
import { cleanup, render, screen, waitFor, fireEvent } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CostSummary, Savings } from "@/lib/api/costs";
import type { SetupResponse } from "@/lib/api/setup";

const mocks = vi.hoisted(() => ({
  getSavings: vi.fn(),
  getCostSummary: vi.fn(),
  listCosts: vi.fn(),
  getRepoSetup: vi.fn(),
  getRepo: vi.fn(),
  repoId: { current: "repo-1" },
}));

vi.mock("@/lib/api/costs", () => ({
  getSavings: mocks.getSavings,
  getCostSummary: mocks.getCostSummary,
  listCosts: mocks.listCosts,
}));
vi.mock("@/lib/api/setup", () => ({ getRepoSetup: mocks.getRepoSetup }));
vi.mock("@/lib/api/repos", () => ({ getRepo: mocks.getRepo }));
vi.mock("next/navigation", () => ({ useParams: () => ({ id: mocks.repoId.current }) }));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import CostsPage from "./page";
import { pickSavingsNudge } from "./savings-nudge";
import { ACCOUNTING_METHOD_VERSION } from "@repowise-dev/ui/savings";

function makeSavings(overrides: Partial<Savings> = {}): Savings {
  return {
    available: true,
    window_days: null,
    first_event_at: "2026-09-01T00:00:00Z",
    unique_events: 120,
    successful_or_usable_partial_events: 118,
    saved_input_tokens: 1_000_000,
    priced_input_savings_usd: 1.25,
    baseline_input_tokens: 2_000_000,
    baseline_saved_input_tokens: 1_000_000,
    reducing_events: 110,
    input_reduction_ratio: 0.5,
    per_operation: [{ group: "get_answer", events: 120, saved_input_tokens: 1_000_000 }],
    per_day: [],
    ...overrides,
  };
}

function makeSpend(overrides: Partial<CostSummary> = {}): CostSummary {
  return {
    total_cost_usd: 1.25,
    total_calls: 7,
    total_input_tokens: 5000,
    total_output_tokens: 900,
    since: null,
    ...overrides,
  };
}

function makeSetup(overrides: Partial<SetupResponse> = {}): SetupResponse {
  return {
    repo_id: "r1",
    generated_at: "",
    index: { state: "up_to_date" },
    agents_available: true,
    agents: [],
    calls_available: true,
    calls: {},
    coverage_available: true,
    model_available: true,
    sync: { installed: true },
    rewrite_hook: { state: "installed" },
    coverage: {},
    model: { docs_mode: "llm" },
    checks_available: false,
    onboarding: { hints_enabled: true, signed_in: false },
    writes_allowed: true,
    ...overrides,
  } as SetupResponse;
}

function renderPage() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), errorRetryCount: 0, dedupingInterval: 0 }}>
      <CostsPage />
    </SWRConfig>,
  );
}

let repoCounter = 0;

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  repoCounter += 1;
  mocks.repoId.current = `repo-${repoCounter}`;
  mocks.getSavings.mockResolvedValue(makeSavings());
  mocks.getCostSummary.mockResolvedValue(makeSpend());
  mocks.listCosts.mockResolvedValue([]);
  mocks.getRepoSetup.mockResolvedValue(makeSetup());
  mocks.getRepo.mockResolvedValue({ id: "r", name: "demo" });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("costs page", () => {
  it("fetches all time first, then the chosen range on both endpoints", async () => {
    renderPage();
    expect(await screen.findByText("50%")).toBeTruthy();
    expect(mocks.getSavings).toHaveBeenCalledWith(mocks.repoId.current, {});
    expect(mocks.getCostSummary).toHaveBeenCalledWith(mocks.repoId.current, undefined);

    fireEvent.click(screen.getByRole("radio", { name: "7 days" }));
    await waitFor(() => expect(mocks.getSavings).toHaveBeenCalledWith(mocks.repoId.current, { days: 7 }));
    const since = mocks.getCostSummary.mock.calls.at(-1)?.[1] as string;
    const ageDays = (Date.now() - Date.parse(since)) / 86_400_000;
    expect(ageDays).toBeGreaterThan(6.9);
    expect(ageDays).toBeLessThan(7.1);
    expect(mocks.listCosts).toHaveBeenLastCalledWith(mocks.repoId.current, { by: "operation", since });
  });

  it("writes the reset-notice dismissal under repository and accounting version", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Dismiss the savings accounting notice" }));
    await waitFor(() => expect(screen.queryByText(/Savings accounting has been upgraded/)).toBeNull());
    expect(
      window.localStorage.getItem(
        `repowise:savings-reset-dismissed:${mocks.repoId.current}:v${ACCOUNTING_METHOD_VERSION}`,
      ),
    ).toBe("1");
  });

  it("says spend is unaffected when only savings failed", async () => {
    mocks.getSavings.mockRejectedValue(new Error("boom"));
    renderPage();
    expect(await screen.findByText(/Model spend below is unaffected/)).toBeTruthy();
  });

  it("suggests distill after real usage, links to its Setup row, and hides on dismiss", async () => {
    mocks.getRepoSetup.mockResolvedValue(makeSetup({ rewrite_hook: { state: "not_installed" } }));
    renderPage();
    expect(await screen.findByText("Trim long command output")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open setup" }).getAttribute("href")).toBe(
      `/repos/${mocks.repoId.current}/settings/setup#rewrite`,
    );
    expect(screen.getByText("Not set up")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Hide this suggestion" }));
    await waitFor(() => expect(screen.queryByText("Trim long command output")).toBeNull());
    expect(
      window.localStorage.getItem(`repowise:savings-nudge-dismissed:${mocks.repoId.current}:rewrite`),
    ).toBe("1");
  });

  it("suggests nothing before about 20 agent calls", async () => {
    mocks.getRepoSetup.mockResolvedValue(makeSetup({ rewrite_hook: { state: "not_installed" } }));
    mocks.getSavings.mockResolvedValue(makeSavings({ unique_events: 5 }));
    renderPage();
    await screen.findByText("50%");
    await waitFor(() => expect(mocks.getRepoSetup).toHaveBeenCalled());
    expect(screen.queryByText("Trim long command output")).toBeNull();
  });
});

describe("pickSavingsNudge", () => {
  const none = new Set<"rewrite" | "model">();

  it("puts distill before AI docs", () => {
    const s = makeSetup({ rewrite_hook: { state: "not_installed" }, model: { docs_mode: "deterministic" } });
    expect(pickSavingsNudge(s, 20, none)).toBe("rewrite");
    expect(pickSavingsNudge(s, 20, new Set(["rewrite"]))).toBe("model");
    expect(pickSavingsNudge(s, 20, new Set(["rewrite", "model"]))).toBeNull();
  });

  it("never suggests from an unknown or unreadable state", () => {
    expect(pickSavingsNudge(undefined, 100, none)).toBeNull();
    expect(pickSavingsNudge(makeSetup({ rewrite_hook: { state: "unknown" } }), 100, none)).toBeNull();
    expect(
      pickSavingsNudge(makeSetup({ model_available: false, model: { docs_mode: "deterministic" } }), 100, none),
    ).toBeNull();
    expect(pickSavingsNudge(makeSetup({ model: { docs_mode: "none" } }), 100, none)).toBeNull();
  });

  it("waits for real usage", () => {
    const s = makeSetup({ rewrite_hook: { state: "not_installed" } });
    expect(pickSavingsNudge(s, 19, none)).toBeNull();
  });
});
