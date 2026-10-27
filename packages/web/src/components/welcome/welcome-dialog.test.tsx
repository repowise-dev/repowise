// @vitest-environment jsdom

import React from "react";
import { SWRConfig } from "swr";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SetupResponse } from "@/lib/api/setup";
import type { WelcomeLens } from "@/lib/welcome-moment";
import { WelcomeDialog } from "./welcome-dialog";
import { showsHostedRow } from "./welcome-step-yours";

const mocks = vi.hoisted(() => ({
  connectSetupAgent: vi.fn(),
  setSetupHook: vi.fn(),
  getProviders: vi.fn(),
  openSheet: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, onClick }: { children: React.ReactNode; href: string; onClick?: () => void }) => (
    <a href={href} onClick={(e) => { e.preventDefault(); onClick?.(); }}>
      {children}
    </a>
  ),
}));
vi.mock("@/lib/api/setup", () => ({
  connectSetupAgent: mocks.connectSetupAgent,
  setSetupHook: mocks.setSetupHook,
}));
vi.mock("@/lib/api/providers", () => ({ getProviders: mocks.getProviders }));
vi.mock("@/components/model-upgrade/model-upgrade", () => ({
  useModelUpgrade: () => ({ openSheet: mocks.openSheet, sheet: null }),
}));
vi.mock("sonner", () => ({ toast: { error: mocks.toastError, success: vi.fn() } }));

function setup(overrides: Partial<SetupResponse> = {}): SetupResponse {
  return {
    repo_id: "r1",
    generated_at: "",
    index: { state: "up_to_date" },
    agents_available: true,
    agents: [
      { id: "claude-code", display_name: "Claude Code", installed: true, connected: true },
      {
        id: "cursor",
        display_name: "Cursor",
        installed: true,
        connected: false,
        config_files: { repo: [".cursor/mcp.json"] },
      },
    ],
    calls_available: true,
    calls: {},
    coverage_available: true,
    model_available: true,
    sync: { installed: true },
    rewrite_hook: { state: "not_installed" },
    coverage: {},
    model: { docs_mode: "deterministic" },
    checks_available: false,
    onboarding: { hints_enabled: true, signed_in: false },
    writes_allowed: true,
    ...overrides,
  };
}

const LENSES: WelcomeLens[] = [
  { key: "health", score: 7.4, band: "good", worstPath: "a.py", worstScore: 3.1 },
  { key: "docs", pages: 412 },
];

function renderDialog(props: Partial<React.ComponentProps<typeof WelcomeDialog>> = {}) {
  const onOpenChange = vi.fn();
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <WelcomeDialog
        open
        onOpenChange={onOpenChange}
        repoId="r1"
        repoName="orbit-api"
        fileCount={1284}
        years={9}
        lenses={LENSES}
        setup={setup()}
        hotspotPath="scheduler/planner.py"
        {...props}
      />
    </SWRConfig>,
  );
  return { onOpenChange };
}

beforeEach(() => {
  mocks.getProviders.mockResolvedValue({ active: { provider: null, model: null }, providers: [] });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("WelcomeDialog", () => {
  it("walks the three steps forward and back, naming each in the title", () => {
    renderDialog();
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "We mapped orbit-api" })).toBeTruthy();
    expect(screen.getByText("Step 1 of 3")).toBeTruthy();
    expect(screen.getByText(/1,284 files and 9 years of history/)).toBeTruthy();
    expect(document.activeElement?.textContent).toBe("We mapped orbit-api");

    fireEvent.click(screen.getByRole("button", { name: "Next: your agent" }));
    expect(screen.getByRole("heading", { name: "Your agent can read this map" })).toBeTruthy();
    expect(document.activeElement?.textContent).toBe("Your agent can read this map");
    const steps = screen.getByRole("list", { name: "Tour steps" });
    expect(within(steps).getByText("Your agent").closest("li")?.getAttribute("aria-current")).toBe("step");

    fireEvent.click(screen.getByRole("button", { name: "Next: make it yours" }));
    expect(screen.getByText("Step 3 of 3")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Finish in Setup" }).getAttribute("href")).toBe(
      "/repos/r1/settings/setup",
    );

    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByText("Step 2 of 3")).toBeTruthy();
  });

  it("skips the tour from the first step", () => {
    const { onOpenChange } = renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "Skip the tour" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("closes when a lens card opens its page", () => {
    const { onOpenChange } = renderDialog();
    const link = screen.getByRole("link", { name: "Open health" });
    expect(link.getAttribute("href")).toBe("/repos/r1/code-health");
    fireEvent.click(link);
    expect(onOpenChange).toHaveBeenCalledWith(false);
    // Only the measured lenses render.
    expect(screen.queryByText("Dead code")).toBeNull();
  });

  it("connects an agent, and rolls back when the command reports failure", async () => {
    mocks.connectSetupAgent.mockResolvedValue({ ok: false, command: "repowise agents add", message: "no config dir" });
    renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "Next: your agent" }));
    expect(screen.getByText("Waiting for its first call")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    expect(mocks.connectSetupAgent).toHaveBeenCalledWith("r1", "cursor");
    await waitFor(() => expect(mocks.toastError).toHaveBeenCalled());
    expect(mocks.toastError.mock.calls[0]![0]).toBe("Couldn't connect Cursor");
    expect(screen.getByRole("button", { name: "Connect" })).toBeTruthy();
    expect(screen.getByText(/Installed on this machine/)).toBeTruthy();
  });

  it("keeps a connected agent after a successful connect", async () => {
    mocks.connectSetupAgent.mockResolvedValue({ ok: true, command: "repowise agents add" });
    renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "Next: your agent" }));
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Connect" })).toBeNull());
    expect(screen.getAllByText("Waiting for its first call")).toHaveLength(2);
    expect(mocks.toastError).not.toHaveBeenCalled();
  });

  it("asks before turning sync off, and says what else goes", async () => {
    mocks.setSetupHook.mockResolvedValue({ ok: true, command: "repowise hook uninstall" });
    renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "Next: your agent" }));
    fireEvent.click(screen.getByRole("button", { name: "Next: make it yours" }));

    const toggle = screen.getByRole("switch", { name: "Keep the index in sync" });
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(toggle);
    expect(mocks.setSetupHook).not.toHaveBeenCalled();
    expect(screen.getByText(/pre-commit security check and the health gate/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Turn off" }));
    await waitFor(() => expect(mocks.setSetupHook).toHaveBeenCalledWith("r1", "post-commit", false));
    await waitFor(() => expect(toggle.getAttribute("aria-checked")).toBe("false"));
  });

  it("opens the model sheet from the plum box", () => {
    renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "Next: your agent" }));
    fireEvent.click(screen.getByRole("button", { name: "Next: make it yours" }));
    fireEvent.click(screen.getByRole("button", { name: "Write the docs" }));
    expect(mocks.openSheet).toHaveBeenCalled();
  });

  it("moves on with Enter from the heading, but not from a control", () => {
    renderDialog();
    fireEvent.keyDown(document.activeElement!, { key: "Enter" });
    expect(screen.getByText("Step 2 of 3")).toBeTruthy();
    fireEvent.keyDown(screen.getByRole("button", { name: "Back" }), { key: "Enter" });
    expect(screen.getByText("Step 2 of 3")).toBeTruthy();
    fireEvent.keyDown(document.activeElement!, { key: "Enter" });
    expect(screen.getByText("Step 3 of 3")).toBeTruthy();
    // The last step leaves Enter to the Finish link.
    fireEvent.keyDown(document.activeElement!, { key: "Enter" });
    expect(screen.getByText("Step 3 of 3")).toBeTruthy();
  });

  it("lists connected agents first, a few at most, and shortens config paths", () => {
    const many = ["a", "b", "c", "d", "e"].map((id) => ({
      id,
      display_name: `Agent ${id}`,
      installed: true,
      connected: id === "e",
      config_files: { repo: [`C:\\work\\repo\\.${id}\\mcp.json`] },
    }));
    renderDialog({ setup: setup({ agents: many }) });
    fireEvent.click(screen.getByRole("button", { name: "Next: your agent" }));
    const names = screen.getAllByRole("heading", { level: 4 }).map((h) => h.textContent);
    expect(names).toEqual(["Agent e", "Agent a", "Agent b", "Agent c"]);
    expect(screen.getByText("1 more agent found. Setup lists them all.")).toBeTruthy();
    expect(screen.getByText(".a/mcp.json").getAttribute("title")).toBe("C:\\work\\repo\\.a\\mcp.json");
  });
});

describe("hosted row", () => {
  it("shows with hints on for someone not signed in", () => {
    expect(showsHostedRow(setup())).toBe(true);
  });

  it("hides when hints are off, when signed in, or without setup data", () => {
    expect(showsHostedRow(setup({ onboarding: { hints_enabled: false, signed_in: false } }))).toBe(false);
    expect(showsHostedRow(setup({ onboarding: { hints_enabled: true, signed_in: true } }))).toBe(false);
    expect(showsHostedRow(null)).toBe(false);
  });

  it("renders its link to hosted only when shown", () => {
    renderDialog({ setup: setup({ onboarding: { hints_enabled: false, signed_in: false } }) });
    fireEvent.click(screen.getByRole("button", { name: "Next: your agent" }));
    fireEvent.click(screen.getByRole("button", { name: "Next: make it yours" }));
    expect(screen.queryByRole("link", { name: "See hosted" })).toBeNull();
    cleanup();
    renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "Next: your agent" }));
    fireEvent.click(screen.getByRole("button", { name: "Next: make it yours" }));
    expect(screen.getByRole("link", { name: "See hosted" }).getAttribute("href")).toContain(
      "src=local_web_welcome",
    );
  });
});
