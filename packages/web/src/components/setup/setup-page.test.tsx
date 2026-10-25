// @vitest-environment jsdom

import React from "react";
import { SWRConfig } from "swr";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SetupResponse } from "@/lib/api/setup";
import { doneFixture, setupFixture } from "./setup-fixture";

const mocks = vi.hoisted(() => ({
  getRepoSetup: vi.fn(),
  setSetupHook: vi.fn(),
  updateOnboardingState: vi.fn(),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
}));

vi.mock("@/lib/api/setup", () => ({
  getRepoSetup: mocks.getRepoSetup,
  setSetupHook: mocks.setSetupHook,
  updateOnboardingState: mocks.updateOnboardingState,
  connectSetupAgent: vi.fn(),
  disconnectSetupAgent: vi.fn(),
  addSetupCoverage: vi.fn(),
  getOnboardingState: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { error: mocks.toastError, success: mocks.toastSuccess } }));
// The sheet pulls in the providers and bulk generate flows; Setup only opens it.
vi.mock("@/components/model-upgrade/model-upgrade", () => ({
  useModelUpgrade: () => ({ openSheet: vi.fn(), sheet: null }),
}));

import { SetupPage } from "./setup-page";
import { hostedCardVisible } from "./hosted-setup-card";

function renderPage(setup: SetupResponse) {
  mocks.getRepoSetup.mockResolvedValue(setup);
  return render(
    <SWRConfig value={{ provider: () => new Map() }}>
      <SetupPage repoId="r1" repoName="orbit-api" />
    </SWRConfig>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SetupPage", () => {
  it("shows the essentials count and the tour link", async () => {
    renderPage(setupFixture());
    expect(await screen.findByText(/2 of 3 essentials done/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Take the tour" }).getAttribute("href")).toBe(
      "/repos/r1/overview?tour=1",
    );
  });

  it("links each row to the settings page that holds its control, once", async () => {
    renderPage(setupFixture());
    expect(await screen.findByText(/2 of 3 essentials done/)).toBeTruthy();
    // The switches and buttons live on the settings pages, not here too.
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.queryByRole("button", { name: "Connect Cursor" })).toBeNull();
    const hrefs = screen
      .getAllByRole("link", { name: /^Change in / })
      .map((a) => a.getAttribute("href"));
    expect(hrefs).toContain("/repos/r1/settings/sync");
    expect(hrefs).toContain("/repos/r1/settings/agents");
  });

  it("puts the prompt first on a Needs attention row, with a preview", async () => {
    const s = setupFixture();
    renderPage({ ...s, index: { ...s.index, parse_error_count: 1, parse_error_files: ["gen.py"] } });
    expect(await screen.findByText("Needs attention")).toBeTruthy();
    expect(screen.getByText("Prompt preview")).toBeTruthy();
    expect(screen.getByText(/gen\.py\. Run repowise doctor --format json to confirm/)).toBeTruthy();
  });
});

describe("unreadable sections", () => {
  it("says doctor couldn't run instead of showing an empty Needs attention", async () => {
    renderPage(setupFixture({ checks_available: false, checks: [] }));
    expect(await screen.findByText("Needs attention")).toBeTruthy();
    expect(
      screen.getByText("Couldn't read this here. Run repowise doctor in a terminal to check."),
    ).toBeTruthy();
  });

  it("shows the unreadable line for coverage in place of its empty state", async () => {
    renderPage(setupFixture({ coverage_available: false }));
    expect(
      await screen.findByText("Couldn't read this here. Run repowise coverage status in a terminal to check."),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Use this report" })).toBeNull();
  });
});

describe("hosted card", () => {
  it("appears once the essentials are done, and × hides it on this machine", async () => {
    mocks.updateOnboardingState.mockResolvedValue({ ...doneFixture().onboarding, hosted_card_hidden: true });
    renderPage(doneFixture());
    expect(await screen.findByText("You're set up")).toBeTruthy();
    expect(screen.getByText("All of this, run for you and your team")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Start free" }).getAttribute("href")).toContain(
      "src=local_web_setup_card",
    );

    fireEvent.click(screen.getByRole("button", { name: "Hide on this machine" }));
    expect(screen.queryByText("All of this, run for you and your team")).toBeNull();
    expect(mocks.updateOnboardingState).toHaveBeenCalledWith({ hosted_card_hidden: true });
  });

  it("is not shown before the essentials are done", async () => {
    renderPage(setupFixture());
    await screen.findByText(/2 of 3 essentials done/);
    expect(screen.queryByText("All of this, run for you and your team")).toBeNull();
  });

  it("stays hidden with tips off, when signed in, or when hidden before", () => {
    const on = doneFixture().onboarding;
    expect(hostedCardVisible(on, false)).toBe(true);
    expect(hostedCardVisible({ ...on, hints_enabled: false }, false)).toBe(false);
    expect(hostedCardVisible({ ...on, signed_in: true }, false)).toBe(false);
    expect(hostedCardVisible({ ...on, hosted_card_hidden: true }, false)).toBe(false);
    expect(hostedCardVisible(on, true)).toBe(false);
  });
});
