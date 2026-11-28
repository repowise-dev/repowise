// @vitest-environment jsdom

import React from "react";
import { SWRConfig } from "swr";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { sessionMomentTaken } from "@/lib/release-moment";

const mocks = vi.hoisted(() => ({
  search: "",
  welcomeSeen: {} as Record<string, string>,
  updateOnboardingState: vi.fn(),
  getWorkspaceSetup: vi.fn(),
}));

vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams(mocks.search) }));
vi.mock("next/link", () => ({
  default: ({ children, href, onClick }: { children: React.ReactNode; href: string; onClick?: () => void }) => (
    <a
      href={href}
      onClick={(e) => {
        e.preventDefault();
        onClick?.();
      }}
    >
      {children}
    </a>
  ),
}));
vi.mock("@/lib/api/setup", () => ({
  getOnboardingState: async () => ({ welcome_seen: mocks.welcomeSeen, hints_enabled: true, signed_in: false }),
  updateOnboardingState: mocks.updateOnboardingState,
  // welcome-dialog imports these for the repo welcome.
  getRepoSetup: vi.fn(),
  connectSetupAgent: vi.fn(),
  setSetupHook: vi.fn(),
}));
vi.mock("@/lib/api/workspace", () => ({ getWorkspaceSetup: mocks.getWorkspaceSetup }));
vi.mock("@/lib/api/meta", () => ({ getMetaVersion: async () => ({ server_version: "0.62.0" }) }));

import {
  WORKSPACE_WELCOME_KEY,
  WorkspaceWelcomeDialog,
  WorkspaceWelcomeMoment,
  firstWorkspaceCall,
  type WorkspaceWelcomeFacts,
} from "./workspace-welcome";
import { FIRST_CALL_POLL_MS } from "./welcome-dialog";

const FACTS: WorkspaceWelcomeFacts = {
  name: "acme",
  repoCount: 3,
  fileCount: 2911,
  contractLinks: 18,
  topCoChange: { a: "web-checkout", b: "payments-api", sessions: 41 },
  lowest: { name: "ledger-worker", score: 5.2 },
  notIndexed: ["notifications-svc"],
  members: ["payments-api", "web-checkout", "ledger-worker"],
};

const member = (alias: string, first_call_at: string | null) => ({
  alias,
  path: alias,
  repo_id: alias,
  status: "available" as const,
  first_call_at,
});

function wrap(node: React.ReactNode) {
  return render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{node}</SWRConfig>);
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  mocks.search = "";
  mocks.welcomeSeen = {};
  mocks.updateOnboardingState.mockResolvedValue({});
  mocks.getWorkspaceSetup.mockResolvedValue({ repos: [member("payments-api", null)], essentials_done: 0, open: 0 });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("WorkspaceWelcomeMoment", () => {
  it("opens once for a new browser and stamps the workspace, not a repo", async () => {
    wrap(<WorkspaceWelcomeMoment facts={FACTS} />);
    expect(await screen.findByRole("heading", { name: "We mapped 3 repos in acme" })).toBeTruthy();
    expect(mocks.updateOnboardingState).toHaveBeenCalledWith({ welcome_seen_repo: WORKSPACE_WELCOME_KEY });
    expect(sessionMomentTaken()).toBe(true);
  });

  it("stays shut once seen on this machine", async () => {
    mocks.welcomeSeen = { [WORKSPACE_WELCOME_KEY]: "2026-11-19T00:00:00Z" };
    wrap(<WorkspaceWelcomeMoment facts={FACTS} />);
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(mocks.updateOnboardingState).not.toHaveBeenCalled();
  });
});

describe("WorkspaceWelcomeDialog", () => {
  it("shows cross-repo cards, then the members waiting, then the cross-repo question", async () => {
    wrap(<WorkspaceWelcomeDialog open onOpenChange={vi.fn()} facts={FACTS} />);
    expect(screen.getByText("Contracts")).toBeTruthy();
    expect(screen.getByText(/changed together/).textContent).toBe("web-checkout and payments-api changed together.");
    expect(screen.getByText("work sessions")).toBeTruthy();
    expect(screen.getByText(/the lowest of 3 indexed/)).toBeTruthy();
    expect(screen.getByText(/Contracts that touch it are not seen yet/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Next: your agent" }));
    expect(screen.getByText("One workspace server answers about every repo in it.")).toBeTruthy();
    expect(await screen.findByText("payments-api")).toBeTruthy();
    expect(screen.getByText("Waiting for its first call")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Next: ask it" }));
    expect(screen.getByText(/It asks across these repos/)).toBeTruthy();
    expect(
      screen.getByText(/show me how payments-api, web-checkout and ledger-worker depend on each other/),
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: "Finish in Setup" }).getAttribute("href")).toBe("/workspace/setup");
  });

  it("checks for the first call on any member while step 2 waits", async () => {
    vi.useFakeTimers();
    try {
      wrap(<WorkspaceWelcomeDialog open onOpenChange={vi.fn()} facts={FACTS} />);
      fireEvent.click(screen.getByRole("button", { name: "Next: your agent" }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10);
      });
      mocks.getWorkspaceSetup.mockResolvedValue({
        repos: [member("payments-api", "2026-11-20T10:00:00Z")],
        essentials_done: 0,
        open: 0,
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(FIRST_CALL_POLL_MS + 50);
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(50);
      });
      expect(mocks.getWorkspaceSetup.mock.calls.length).toBeGreaterThan(1);
      expect(screen.getByText(/^First call /)).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("finds the first call across members", () => {
    expect(firstWorkspaceCall(null)).toBeNull();
    expect(
      firstWorkspaceCall({
        repos: [member("a", "2026-11-20T11:00:00Z"), member("b", "2026-11-20T09:00:00Z"), member("c", null)],
        essentials_done: 0,
        open: 0,
      }),
    ).toBe("2026-11-20T09:00:00Z");
  });
});
