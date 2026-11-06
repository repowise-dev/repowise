// @vitest-environment jsdom

import React from "react";
import { SWRConfig } from "swr";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SetupResponse } from "@/lib/api/setup";
import { doneFixture, setupFixture } from "./setup-fixture";

const mocks = vi.hoisted(() => ({
  getRepoSetup: vi.fn(),
  getOnboardingState: vi.fn(),
  updateOnboardingState: vi.fn(),
}));

vi.mock("@/lib/api/setup", () => ({
  getRepoSetup: mocks.getRepoSetup,
  getOnboardingState: mocks.getOnboardingState,
  updateOnboardingState: mocks.updateOnboardingState,
}));

import { SetupPill, setupPillRepoId } from "./setup-pill";

function renderPill(setup: SetupResponse, compact = false) {
  mocks.getRepoSetup.mockResolvedValue(setup);
  mocks.getOnboardingState.mockResolvedValue(setup.onboarding);
  return render(
    <SWRConfig value={{ provider: () => new Map() }}>
      <SetupPill repoId="r1" compact={compact} />
    </SWRConfig>,
  );
}

beforeEach(() => window.localStorage.clear());
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SetupPill", () => {
  it("shows how many essentials are done and links to Setup", async () => {
    renderPill(setupFixture());
    const link = await screen.findByRole("link", { name: "Setup: 2 of 3 essentials done" });
    expect(link.getAttribute("href")).toBe("/repos/r1/settings/setup");
    expect(link.textContent).toContain("2 of 3");
  });

  it("hides on this machine with ×", async () => {
    mocks.updateOnboardingState.mockResolvedValue({ ...setupFixture().onboarding, setup_pill_hidden: true });
    renderPill(setupFixture());
    fireEvent.click(await screen.findByRole("button", { name: "Hide the setup reminder on this machine" }));
    expect(screen.queryByRole("link")).toBeNull();
    expect(mocks.updateOnboardingState).toHaveBeenCalledWith({ setup_pill_hidden: true });
  });

  it("does not run the setup checks once hidden", async () => {
    const s = setupFixture();
    renderPill({ ...s, onboarding: { ...s.onboarding, setup_pill_hidden: true } });
    await waitFor(() => expect(mocks.getOnboardingState).toHaveBeenCalled());
    expect(mocks.getRepoSetup).not.toHaveBeenCalled();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("disappears when all three essentials are done, and stops checking after", async () => {
    const first = renderPill(doneFixture());
    await waitFor(() => expect(mocks.getRepoSetup).toHaveBeenCalled());
    expect(screen.queryByRole("link")).toBeNull();
    await waitFor(() => expect(window.localStorage.getItem("repowise:setup-done:r1")).toBe("1"));
    first.unmount();

    vi.clearAllMocks();
    renderPill(setupFixture());
    await Promise.resolve();
    expect(mocks.getRepoSetup).not.toHaveBeenCalled();
  });

  it("follows the active repo only once it has an index", () => {
    const repo = (id: string, status: string | null = null) =>
      ({ id, name: id, workspace_status: status }) as never;
    expect(setupPillRepoId([repo("r1")], "r1")).toBe("r1");
    expect(setupPillRepoId([repo("r1", "needs_index")], "r1")).toBeUndefined();
    expect(setupPillRepoId([repo("ws:x")], "ws:x")).toBeUndefined();
    expect(setupPillRepoId([repo("r1")], undefined)).toBeUndefined();
  });
});
