// @vitest-environment jsdom

/**
 * The costs page's host-owned behaviour.
 *
 * The shared savings primitives are tested in `@repowise-dev/ui`. What is only
 * true here is what the host owns: the reset notice's storage key and its
 * fail-open reads, and which of the four report states the page is in --
 * unavailable, loading, error, or a report. Those are the three places this
 * page can lie to a reader on its own.
 */

import React from "react";
import { SWRConfig } from "swr";
import { cleanup, render, screen, waitFor, fireEvent } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CostSummary, Savings } from "@/lib/api/costs";

const mocks = vi.hoisted(() => ({
  getSavings: vi.fn(),
  getCostSummary: vi.fn(),
  repoId: { current: "repo-1" },
}));

vi.mock("@/lib/api/costs", () => ({
  getSavings: mocks.getSavings,
  getCostSummary: mocks.getCostSummary,
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: mocks.repoId.current }),
}));

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children?: React.ReactNode;
  }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import CostsPage from "./page";
import { ACCOUNTING_METHOD_VERSION } from "@repowise-dev/ui/savings";

/** A report with something in it. Every figure is a wire field; the page does
 *  no arithmetic, so the values only have to be distinguishable. */
function makeSavings(overrides: Partial<Savings> = {}): Savings {
  return {
    available: true,
    window_days: null,
    as_of: "2026-09-19T00:00:00Z",
    first_event_at: "2026-09-01T00:00:00Z",
    last_event_at: "2026-09-19T00:00:00Z",

    unique_events: 12,
    successful_or_usable_partial_events: 11,
    saving_interactions: 9,
    mcp_queries_answered: 4,
    dead_ends: 1,

    saved_input_tokens: 400_000,
    measured_saved_input_tokens: 400_000,
    inferred_saved_input_tokens: 0,
    priced_saved_input_tokens: 0,
    unpriced_saved_input_tokens: 400_000,
    priced_input_savings_usd: 0,
    saved_output_tokens: null,
    priced_saved_output_tokens: 0,
    unpriced_saved_output_tokens: 0,
    priced_output_savings_usd: 0,
    baseline_events: 120,
    reducing_events: 110,
    baseline_input_tokens: 2_000_000,
    baseline_saved_input_tokens: 1_000_000,
    input_reduction_ratio: 0.5,
    input_reduction_ratio_p90: 0.88,

    per_operation: [{ group: "rg", events: 9, saved_input_tokens: 400_000 }],
    per_surface: [{ group: "distill", events: 9, saved_input_tokens: 400_000 }],
    per_agent: [
      {
        agent: "claude_code",
        agent_display_name: "Claude Code",
        events: 9,
        saved_input_tokens: 400_000,
      },
    ],
    per_model: [{ group: null, events: 9, saved_input_tokens: 400_000 }],
    per_day: [{ group: "2026-09-19", events: 9, saved_input_tokens: 400_000 }],

    opportunity_count: 0,
    opportunity_tokens_excluded: 0,
    per_opportunity_kind: [],
    missed_events: 0,
    missed_tokens_est: 0,
    missed_window_days: 0,
    reread_events: 0,
    reread_tokens_est: 0,
    ...overrides,
  };
}

function makeSpend(overrides: Partial<CostSummary> = {}): CostSummary {
  return {
    total_cost_usd: 1.25,
    total_calls: 7,
    total_input_tokens: 5000,
    total_output_tokens: 900,
    since: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

/** A fresh SWR cache per render, so a key resolved in one test is not served
 *  from cache in the next. */
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
  // A distinct repository per test keeps the notice's storage key, which is
  // keyed on the repository, from carrying between them.
  repoCounter += 1;
  mocks.repoId.current = `repo-${repoCounter}`;
  mocks.getSavings.mockResolvedValue(makeSavings());
  mocks.getCostSummary.mockResolvedValue(makeSpend());
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("costs page reset notice", () => {
  it("writes dismissal under the repository and accounting-method version", async () => {
    renderPage();

    const dismiss = await screen.findByRole("button", {
      name: "Dismiss the savings accounting notice",
    });
    fireEvent.click(dismiss);

    await waitFor(() =>
      expect(
        screen.queryByText(/Savings accounting has been upgraded/),
      ).toBeNull(),
    );
    // The exact key, not merely "some key": a repository-only key would hide a
    // later methodology change, and a version-only key would hide the notice
    // for every other repository.
    expect(
      window.localStorage.getItem(
        `repowise:savings-reset-dismissed:${mocks.repoId.current}:v${ACCOUNTING_METHOD_VERSION}`,
      ),
    ).toBe("1");
  });

  it("stays dismissed for a repository that already stored the key", async () => {
    window.localStorage.setItem(
      `repowise:savings-reset-dismissed:${mocks.repoId.current}:v${ACCOUNTING_METHOD_VERSION}`,
      "1",
    );

    renderPage();

    await screen.findByRole("heading", { name: "Repowise model spend" });
    expect(
      screen.queryByText(/Savings accounting has been upgraded/),
    ).toBeNull();
  });

  it("shows again for the same repository under a later accounting version", async () => {
    window.localStorage.setItem(
      `repowise:savings-reset-dismissed:${mocks.repoId.current}:v${ACCOUNTING_METHOD_VERSION + 1}`,
      "1",
    );

    renderPage();

    expect(
      await screen.findByText(/Savings accounting has been upgraded/),
    ).toBeTruthy();
  });

  it("shows the notice when storage cannot be read", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage blocked");
    });

    renderPage();

    // Fail open: an explanation shown twice beats a restarted total never
    // explained.
    expect(
      await screen.findByText(/Savings accounting has been upgraded/),
    ).toBeTruthy();
  });

  it("hides the notice even when the dismissal cannot be stored", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("storage blocked");
    });

    renderPage();
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Dismiss the savings accounting notice",
      }),
    );

    // The reader asked for it to go away now. It returns on the next mount,
    // which is the cost of storage being unavailable, not a reason to ignore
    // the click.
    await waitFor(() =>
      expect(
        screen.queryByText(/Savings accounting has been upgraded/),
      ).toBeNull(),
    );
  });
});

describe("costs page savings states", () => {
  it("says nothing has been measured when the report is unavailable", async () => {
    mocks.getSavings.mockResolvedValue(makeSavings({ available: false }));

    renderPage();

    expect(
      await screen.findByRole("heading", { name: "No agent savings recorded yet" }),
    ).toBeTruthy();
    // Unavailable is a semantic state, not a load state, and not an error.
    expect(screen.queryByText("Loading agent savings")).toBeNull();
    expect(screen.queryByText("Couldn't load agent savings")).toBeNull();
    // Nor is it a measured zero: no figure is published for a report that has
    // nothing behind it.
    expect(screen.queryByRole("heading", { name: "Savings by source" })).toBeNull();
  });

  it("reserves the report while it is in flight", async () => {
    mocks.getSavings.mockReturnValue(new Promise<Savings>(() => {}));

    renderPage();

    // The skeleton announces itself through a `role="status"` region. Queried
    // by its text, not by an accessible name: `status` takes its name from the
    // author, so a name-scoped query here matches nothing and passes whatever
    // the page does.
    expect(await screen.findByText("Loading agent savings")).toBeTruthy();
    // A pending fetch is not an empty repository.
    expect(
      screen.queryByRole("heading", { name: "No agent savings recorded yet" }),
    ).toBeNull();
  });

  it("renders the report once it resolves", async () => {
    renderPage();

    expect(
      await screen.findByRole("heading", { name: "Savings by source" }),
    ).toBeTruthy();
    expect(screen.queryByText("Loading agent savings")).toBeNull();
  });
});

describe("costs page error copy", () => {
  it("reassures about spend only when spend actually loaded", async () => {
    mocks.getSavings.mockRejectedValue(new Error("savings down"));

    renderPage();

    expect(
      await screen.findByText(
        "The savings endpoint did not respond. Model spend below is unaffected.",
      ),
    ).toBeTruthy();
  });

  it("makes no claim about spend when spend failed too", async () => {
    mocks.getSavings.mockRejectedValue(new Error("savings down"));
    mocks.getCostSummary.mockRejectedValue(new Error("spend down"));

    renderPage();

    expect(
      await screen.findByText("The savings endpoint did not respond."),
    ).toBeTruthy();
    // This sentence sat directly above a spend error before the fix.
    expect(screen.queryByText(/Model spend below is unaffected/)).toBeNull();
    expect(screen.getByText("Couldn't load model spend")).toBeTruthy();
  });

  it("keeps the savings report when only spend fails", async () => {
    mocks.getCostSummary.mockRejectedValue(new Error("spend down"));

    renderPage();

    expect(await screen.findByText("Couldn't load model spend")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Savings by source" })).toBeTruthy();
    expect(screen.queryByText("Couldn't load agent savings")).toBeNull();
  });
});
