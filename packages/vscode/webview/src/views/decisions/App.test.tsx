import { describe, it, expect, vi, afterEach, beforeAll } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { App, routeForHref } from "./App";
import type { WebviewHost } from "../../runtime/rpc";
import type { RepoInit } from "../../../../src/shared/webviewMessages";
import type { DecisionRecordResponse } from "@repowise-dev/api-client/types";
import type { DecisionLaneCounts } from "@repowise-dev/types/decisions";

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
  window.matchMedia ??= ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
});

afterEach(cleanup);

function decision(over: Partial<DecisionRecordResponse>): DecisionRecordResponse {
  return {
    id: "d",
    repository_id: "r1",
    title: "Untitled",
    status: "active",
    context: "",
    decision: "",
    rationale: "",
    alternatives: [],
    consequences: [],
    affected_files: [],
    affected_modules: [],
    tags: [],
    source: "cli",
    evidence_commits: [],
    evidence_file: null,
    evidence_line: null,
    confidence: 1,
    staleness_score: 0,
    superseded_by: null,
    last_code_change: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

const PORTS = decision({ id: "a", title: "Adopt hexagonal ports", decision: "Ports and adapters." });
const LEGACY = decision({ id: "b", title: "Retire the legacy client" });

const LANES: DecisionLaneCounts = {
  total: 12,
  candidates: 9,
  active: 2,
  needs_review: 0,
  uncheckable: 1,
  history: 0,
  in_force: 3,
  recent: 1,
} as DecisionLaneCounts;

const REPO: RepoInit = { id: "r1", name: "repo", headCommit: null, defaultBranch: "main" };

function makeHost() {
  const api = {
    decisionLaneCounts: vi.fn().mockResolvedValue(LANES),
    decisionCounts: vi.fn().mockResolvedValue({ total: 12, proposed: 9, active: 3 }),
    checkoutFacts: vi.fn().mockResolvedValue({ episodes: [], available: true, total: 0 }),
    decisionAgentPrompt: vi.fn((kind: string) =>
      Promise.resolve({ kind, text: `prompt for ${kind}`, count: 9 }),
    ),
    decisionHealth: vi.fn().mockResolvedValue({ ungoverned_hotspots: [] }),
    listDecisions: vi.fn((query?: { lane?: string }) =>
      Promise.resolve(query?.lane === "recent" ? [PORTS] : [PORTS, LEGACY]),
    ),
    decision: vi.fn((id: string) => Promise.resolve(id === "a" ? PORTS : LEGACY)),
    decisionLineage: vi.fn().mockResolvedValue([]),
    decisionEvidence: vi.fn().mockResolvedValue([]),
    moduleSuggestions: vi.fn().mockResolvedValue([]),
    patchDecision: vi.fn().mockResolvedValue(PORTS),
    setDecisionPinned: vi.fn().mockResolvedValue(PORTS),
    reaffirmDecision: vi.fn().mockResolvedValue(PORTS),
    createDecision: vi.fn().mockResolvedValue(PORTS),
  };
  const host = {
    api: api as unknown as WebviewHost["api"],
    onInit: () => () => {},
    onRefresh: () => () => {},
    onUpdateDone: () => () => {},
    onThemeChanged: () => () => {},
    ready: () => {},
    openFile: vi.fn(),
    copyText: () => {},
    openExternal: () => {},
    openView: vi.fn(),
    focusHome: () => {},
    openNativeSettings: () => {},
    updateIndex: () => {},
    setTheme: () => {},
  } satisfies WebviewHost;
  return { host, api };
}

describe("decisions App", () => {
  it("leads with the in-force figure and the web page's sections", async () => {
    const { host, api } = makeHost();
    render(<App host={host} repo={REPO} params={{}} refreshToken={0} />);

    expect(await screen.findByText("In force")).toBeTruthy();
    expect(screen.getByText("of 12 recorded")).toBeTruthy();
    expect(
      await screen.findByText(/3 decisions govern this repository/),
    ).toBeTruthy();
    expect(await screen.findByText("Check 9 background records on the busiest files")).toBeTruthy();
    expect(await screen.findByText("All decisions")).toBeTruthy();
    expect(await screen.findByText("About this checkout")).toBeTruthy();
    // The lane is a server query, paged, not a filter over a loaded window.
    await waitFor(() =>
      expect(api.listDecisions).toHaveBeenCalledWith(
        expect.objectContaining({ lane: "in_force", limit: 50, offset: 0 }),
      ),
    );
  });

  it("opens a record in the panel and returns to the board", async () => {
    const { host, api } = makeHost();
    render(<App host={host} repo={REPO} params={{}} refreshToken={0} />);

    // The digest's This week row links to the record by its web route.
    const link = (await screen.findAllByRole("link", { name: "Adopt hexagonal ports" }))[0]!;
    expect(link.getAttribute("href")).toBe("/repos/r1/decisions/a");
    fireEvent.click(link);

    await waitFor(() => expect(api.decision).toHaveBeenCalledWith("a"));
    expect(await screen.findByText("Governance scope")).toBeTruthy();

    const back = document.querySelector('a[href="/repos/r1/decisions"]');
    expect(back).toBeTruthy();
    fireEvent.click(back!);
    await waitFor(() => expect(screen.queryByText("Governance scope")).toBeNull());
    expect(screen.getByText("All decisions")).toBeTruthy();
  });
});

describe("routeForHref", () => {
  it("maps the web routes the shared components link to", () => {
    expect(routeForHref("/repos/r1/decisions", "r1")).toEqual({ kind: "board" });
    expect(routeForHref("/repos/r1/decisions/abc", "r1")).toEqual({ kind: "record", id: "abc" });
    expect(routeForHref("/repos/r1/code-health", "r1")).toEqual({ kind: "health" });
    expect(routeForHref("/repos/r1/commits?commit=1a2b", "r1")).toEqual({ kind: "inert" });
    expect(routeForHref("https://github.com/x/y/pull/1", "r1")).toBeNull();
    expect(routeForHref("repowise-open:src%2Fa.ts", "r1")).toBeNull();
  });
});
