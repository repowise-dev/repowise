// @vitest-environment jsdom

import React from "react";
import { SWRConfig } from "swr";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SetupResponse } from "@/lib/api/setup";
import { setupFixture } from "./setup-fixture";

const mocks = vi.hoisted(() => ({
  getRepoSetup: vi.fn(),
  setSetupHook: vi.fn(),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
}));

vi.mock("@/lib/api/setup", () => ({
  getRepoSetup: mocks.getRepoSetup,
  setSetupHook: mocks.setSetupHook,
  updateOnboardingState: vi.fn(),
  connectSetupAgent: vi.fn(),
  disconnectSetupAgent: vi.fn(),
  addSetupCoverage: vi.fn(),
  getOnboardingState: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { error: mocks.toastError, success: mocks.toastSuccess } }));
vi.mock("@/components/model-upgrade/model-upgrade", () => ({
  useModelUpgrade: () => ({ openSheet: vi.fn(), sheet: null }),
}));

import { RepoAgentRows, RepoSetupRows } from "./setup-settings";

function wrap(setup: SetupResponse, node: React.ReactNode) {
  mocks.getRepoSetup.mockResolvedValue(setup);
  return render(<SWRConfig value={{ provider: () => new Map() }}>{node}</SWRConfig>);
}

const syncRows = (setup: SetupResponse) =>
  wrap(setup, <RepoSetupRows repoId="r1" repoName="orbit-api" ids={["sync", "rewrite"]} />);

beforeEach(() => window.localStorage.clear());
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Sync settings rows", () => {
  it("rolls a switch back and says why when the command reports ok: false", async () => {
    mocks.setSetupHook.mockResolvedValue({
      ok: false,
      command: "repowise hook rewrite install",
      message: "That didn't work here. Run it in a terminal: repowise hook rewrite install",
    });
    syncRows(setupFixture());
    const toggle = await screen.findByRole("switch", { name: "Shorter command output" });
    expect(toggle.getAttribute("aria-checked")).toBe("false");

    fireEvent.click(toggle);
    await waitFor(() => expect(mocks.setSetupHook).toHaveBeenCalledWith("r1", "rewrite", true));
    await waitFor(() =>
      expect(mocks.toastError).toHaveBeenCalledWith(
        "That didn't work here. Run it in a terminal: repowise hook rewrite install",
        { description: "repowise hook rewrite install" },
      ),
    );
    expect(
      screen.getByRole("switch", { name: "Shorter command output" }).getAttribute("aria-checked"),
    ).toBe("false");
  });

  it("asks before turning sync off, naming the security check and the health gate", async () => {
    mocks.setSetupHook.mockResolvedValue({ ok: true, command: "repowise hook uninstall" });
    syncRows(setupFixture());
    fireEvent.click(await screen.findByRole("switch", { name: "Keep the index in sync" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("pre-commit security check and the health gate");
    expect(mocks.setSetupHook).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Turn off" }));
    await waitFor(() => expect(mocks.setSetupHook).toHaveBeenCalledWith("r1", "post-commit", false));
  });

  it("disables writes when the server does not allow them", async () => {
    syncRows(setupFixture({ writes_allowed: false }));
    const toggle = await screen.findByRole("switch", { name: "Shorter command output" });
    expect(toggle.hasAttribute("disabled")).toBe(true);
  });
});

describe("Agents settings rows", () => {
  it("lists each agent with its own connect button, and hosted last", async () => {
    wrap(setupFixture({ writes_allowed: false }), <RepoAgentRows repoId="r1" repoName="orbit-api" />);
    const connect = await screen.findByRole("button", { name: "Connect Cursor" });
    expect(connect.hasAttribute("disabled")).toBe(true);
    expect(screen.getByText("Claude.ai and ChatGPT")).toBeTruthy();
  });
});
