// @vitest-environment jsdom

import React from "react";
import { SWRConfig } from "swr";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { WorkspaceSetupRepo, WorkspaceSetupResponse } from "@/lib/api/workspace";

const mocks = vi.hoisted(() => ({ getWorkspaceSetup: vi.fn() }));
vi.mock("@/lib/api/workspace", () => ({ getWorkspaceSetup: mocks.getWorkspaceSetup }));
vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a>,
}));

import { WorkspaceSetup, workspaceSetupCells } from "./workspace-setup";

const member = (overrides: Partial<WorkspaceSetupRepo>): WorkspaceSetupRepo => ({
  alias: "payments-api",
  path: "payments-api",
  repo_id: "r1",
  status: "available",
  index_state: "up_to_date",
  agent_calls: 4,
  first_call_at: "2026-11-20T10:00:00Z",
  sync: false,
  coverage: "found",
  evidence: "found",
  open: 3,
  offer: "finish_setup",
  essentials_done: false,
  ...overrides,
});

function renderPage(data: WorkspaceSetupResponse) {
  mocks.getWorkspaceSetup.mockResolvedValue(data);
  return render(
    <SWRConfig value={{ provider: () => new Map() }}>
      <WorkspaceSetup />
    </SWRConfig>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("workspaceSetupCells", () => {
  it("marks only what needs a look, and n/a for a member with no index", () => {
    const cells = workspaceSetupCells(member({}));
    expect(cells.index).toEqual({ tone: "done", key: "upToDate" });
    expect(cells.agent).toEqual({ tone: "done", key: "connected" });
    expect(cells.sync).toEqual({ tone: "attention", key: "off" });
    expect(cells.coverage).toEqual({ tone: "found", key: "found" });
    const none = workspaceSetupCells(member({ status: "not_indexed", repo_id: null }));
    expect(none.index).toEqual({ tone: "attention", key: "notIndexed" });
    expect(none.sync.key).toBe("na");
    expect(workspaceSetupCells(member({ first_call_at: null, agent_calls: 0 })).agent.key).toBe("noCalls");
    expect(workspaceSetupCells(member({ sync: null })).sync.key).toBe("unreadable");
  });
});

describe("WorkspaceSetup", () => {
  it("shows progress, the workspace prompt while offered, and one row per member", async () => {
    renderPage({
      repos: [
        member({}),
        member({ alias: "web-checkout", repo_id: "r2", sync: true, coverage: "on", evidence: "none", essentials_done: true, offer: null, open: 0 }),
        member({ alias: "notifications-svc", repo_id: null, status: "not_indexed", offer: null, open: 0 }),
      ],
      essentials_done: 1,
      open: 3,
      offer: "finish_setup",
      prompt: "Finish setting up Repowise across this workspace.",
    });
    expect(await screen.findByText("1 of 3 repos have every essential done.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Finish setup across the workspace with your agent" })).toBeTruthy();
    expect(screen.getByText(/Finish setting up Repowise across this workspace/)).toBeTruthy();
    const rows = screen.getAllByRole("row");
    expect(rows).toHaveLength(4);
    expect(within(rows[1]!).getByRole("link", { name: "payments-api" }).getAttribute("href")).toBe(
      "/repos/r1/settings/setup",
    );
    expect(within(rows[3]!).queryByRole("link")).toBeNull();
    expect(within(rows[3]!).getByText("Not indexed")).toBeTruthy();
  });

  it("has no prompt once no member has steps left", async () => {
    renderPage({
      repos: [member({ offer: null, open: 0, essentials_done: true, sync: true })],
      essentials_done: 1,
      open: 0,
      offer: null,
      prompt: null,
    });
    expect(await screen.findByText("1 of 1 repo has every essential done.")).toBeTruthy();
    expect(screen.queryByText("Finish setup across the workspace with your agent")).toBeNull();
  });
});
