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
  getMcpToolSurface: vi.fn(),
  repoId: { current: "repo-1" },
}));

vi.mock("@/lib/api/costs", () => ({
  getSavings: mocks.getSavings,
  getCostSummary: mocks.getCostSummary,
  listCosts: mocks.listCosts,
}));
vi.mock("@/lib/api/setup", () => ({ getRepoSetup: mocks.getRepoSetup }));
vi.mock("@/lib/api/repos", () => ({ getRepo: mocks.getRepo }));
vi.mock("@/lib/api/mcp-tools", () => ({ getMcpToolSurface: mocks.getMcpToolSurface }));
vi.mock("next/navigation", () => ({ useParams: () => ({ id: mocks.repoId.current }) }));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import CostsPage from "./page";
import { jobsNotSetUp, nudgeHref, pickSavingsNudge } from "./savings-nudge";
import { checkAgents, connectedAgentIds } from "./savings-checks";
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

const CORE = ["get_answer", "get_context", "get_symbol", "search_codebase", "get_why"];
const STANDARD = [...CORE, "get_overview", "get_change_risk", "get_risk", "get_health", "get_dead_code"];
const SURFACE = {
  repo_id: "r1",
  is_workspace: false,
  override: null,
  profiles: { core: CORE, standard: STANDARD },
  tools: [],
  clients: {
    "claude-code": { profile: "standard", tools: STANDARD },
    cursor: { profile: "core", tools: CORE },
  },
};
const AGENTS = [
  { id: "claude-code", display_name: "Claude Code", installed: true, connected: true, last_call_at: null },
  { id: "cursor", display_name: "Cursor", installed: true, connected: true, last_call_at: null },
  { id: "codex", display_name: "Codex", installed: true, connected: false },
];
const CHECK_ROWS = [
  { tool: "get_change_risk", calls: 12, outcome_calls: 12, high: 3, medium: 4, low: 5 },
  { tool: "get_why", calls: 0, outcome_calls: 0 },
  { tool: "get_risk", calls: 0, outcome_calls: 0 },
  { tool: "get_health", calls: 0, outcome_calls: 0 },
  { tool: "get_dead_code", calls: 0, outcome_calls: 0 },
];

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
  mocks.getMcpToolSurface.mockResolvedValue(SURFACE);
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

  it("keeps the spend section when only savings failed", async () => {
    mocks.getSavings.mockRejectedValue(new Error("boom"));
    renderPage();
    expect(await screen.findByText("What Repowise used")).toBeTruthy();
    expect((await screen.findAllByText("$1.25")).length).toBeGreaterThan(0);
  });

  it("suggests distill after real usage, links to its Setup row, and hides on dismiss", async () => {
    mocks.getRepoSetup.mockResolvedValue(makeSetup({ rewrite_hook: { state: "not_installed" } }));
    renderPage();
    expect(await screen.findByText("Trim long command output")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open setup" }).getAttribute("href")).toBe(
      `/repos/${mocks.repoId.current}/settings/setup#distill_suggested`,
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

describe("checks your agents ran", () => {
  it("asks for connected agents' tools only and names who lacks a tool", async () => {
    mocks.getRepoSetup.mockResolvedValue(makeSetup({ agents: AGENTS } as Partial<SetupResponse>));
    mocks.getSavings.mockResolvedValue(makeSavings({ checks: CHECK_ROWS }));
    renderPage();
    expect(await screen.findByText("Checks your agents ran")).toBeTruthy();
    await waitFor(() =>
      expect(mocks.getMcpToolSurface).toHaveBeenCalledWith(mocks.repoId.current, ["claude-code", "cursor"]),
    );
    expect(await screen.findByText("Add it for Cursor")).toBeTruthy();
    expect(screen.queryByText(/Codex has it|Codex doesn't/)).toBeNull();
    expect(screen.getByRole("link", { name: "Change tools in Agents & editors" }).getAttribute("href")).toBe(
      `/repos/${mocks.repoId.current}/settings/agents`,
    );
  });

  it("does not fetch tools when no agent is connected and offers to connect one", async () => {
    mocks.getSavings.mockResolvedValue(
      makeSavings({ checks: CHECK_ROWS.map((row) => ({ tool: row.tool, calls: 0, outcome_calls: 0 })) }),
    );
    renderPage();
    expect(await screen.findByText("Connect an agent")).toBeTruthy();
    expect(mocks.getMcpToolSurface).not.toHaveBeenCalled();
  });
});

describe("checkAgents", () => {
  it("is unknown until both Setup and the tool lists are in", () => {
    const setup = makeSetup({ agents: AGENTS } as Partial<SetupResponse>);
    expect(checkAgents(undefined, SURFACE)).toBeUndefined();
    expect(checkAgents(makeSetup({ agents_available: false }), SURFACE)).toBeUndefined();
    expect(checkAgents(setup, undefined)).toBeUndefined();
    expect(checkAgents(makeSetup(), undefined)).toEqual([]);
    expect(checkAgents(setup, SURFACE)?.map((a) => [a.name, a.profile, a.tools.length])).toEqual([
      ["Claude Code", "standard", 10],
      ["Cursor", "core", 5],
    ]);
    expect(connectedAgentIds(setup)).toEqual(["claude-code", "cursor"]);
    // A connected agent the server could not resolve: unknown, never dropped.
    expect(checkAgents(setup, { ...SURFACE, clients: { cursor: SURFACE.clients.cursor } })).toBeUndefined();
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

  it("links each suggestion to its Setup rule", () => {
    expect(nudgeHref("r1", "rewrite")).toBe("/repos/r1/settings/setup#distill_suggested");
    expect(nudgeHref("r1", "model")).toBe("/repos/r1/settings/setup#docs_model_ready");
  });

  it("waits for real usage", () => {
    const s = makeSetup({ rewrite_hook: { state: "not_installed" } });
    expect(pickSavingsNudge(s, 19, none)).toBeNull();
  });
});

describe("jobsNotSetUp", () => {
  it("marks trimming only when distill is known to be off", () => {
    expect(jobsNotSetUp(makeSetup({ rewrite_hook: { state: "not_installed" } }))).toEqual(["trim"]);
    expect(jobsNotSetUp(makeSetup({ rewrite_hook: { state: "unknown" } }))).toEqual([]);
    expect(jobsNotSetUp(undefined)).toEqual([]);
  });
});
