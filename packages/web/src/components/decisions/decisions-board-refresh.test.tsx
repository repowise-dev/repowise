// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import enMessages from "../../../messages/en.json";
import type { DecisionLaneCounts, DecisionRecord } from "@repowise-dev/types/decisions";

const state = vi.hoisted(() => ({ written: false }));

const lanes = (inForce: number): DecisionLaneCounts =>
  ({
    candidates: 2,
    active: inForce,
    needs_review: 0,
    uncheckable: 0,
    history: 0,
    governing: inForce,
    in_force: inForce,
    recent: 0,
    total: inForce + 2,
  }) as DecisionLaneCounts;

const record: DecisionRecord = {
  id: "d1",
  repository_id: "r1",
  title: "Use SWR for client reads",
  status: "active",
  currency: "active",
  pinned: false,
  context: "",
  decision: "",
  rationale: "",
  alternatives: [],
  consequences: [],
  affected_files: ["src/app.py"],
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
};

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/lib/api/decisions", () => ({
  createDecision: vi.fn(),
  getDecisionAgentPrompt: vi.fn(async () => ({ kind: "add", text: "", ids: [], count: 0 })),
  // Before the write the server counts 1 in force and one ungoverned hotspot;
  // after it, the write put a second record in force over that hotspot.
  getDecisionLaneCounts: vi.fn(async () => lanes(state.written ? 2 : 1)),
  getDecisionHealth: vi.fn(async () => ({
    ungoverned_hotspots: state.written ? [] : ["hot.py"],
  })),
  listDecisions: vi.fn(async () => [record]),
  patchDecision: vi.fn(),
  reaffirmDecision: vi.fn(),
  setDecisionPinned: vi.fn(async () => {
    state.written = true;
    return {};
  }),
}));

import { DecisionsBoard } from "./decision-review-wrapper";
import { DecisionsLede } from "./decisions-lede";

function renderPage() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <NextIntlClientProvider locale="en" messages={enMessages}>
        <DecisionsLede
          repoId="r1"
          initialLanes={lanes(1)}
          statusFallback={{ proposed: 0, active: 0, loaded: 0 }}
        />
        <DecisionsBoard repoId="r1" initialLaneCounts={lanes(1)} />
      </NextIntlClientProvider>
    </SWRConfig>,
  );
}

describe("DecisionsBoard review refresh", () => {
  afterEach(() => {
    cleanup();
    state.written = false;
  });

  it("moves the lede, the In force badge and the hotspot row together after a write", async () => {
    renderPage();

    expect(await screen.findByText("hot.py")).toBeTruthy();
    expect(screen.getByText("1 decision governs this repository.", { exact: false })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /In force/ }).textContent).toContain("1");

    fireEvent.click((await screen.findAllByRole("button", { name: /^More actions for/ }))[0]!);
    fireEvent.click(await screen.findByRole("menuitem", { name: /^Pin/ }));

    // One refresh revalidates every key under the decisions prefix: the lede
    // and the badge read the same lane-counts key, the digest reads health.
    await waitFor(() =>
      expect(
        screen.getByText("2 decisions govern this repository.", { exact: false }),
      ).toBeTruthy(),
    );
    await waitFor(() => expect(screen.queryByText("hot.py")).toBeNull());
    expect(screen.getByRole("tab", { name: /In force/ }).textContent).toContain("2");
  });
});
