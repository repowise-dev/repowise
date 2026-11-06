// @vitest-environment jsdom

import React from "react";
import { SWRConfig } from "swr";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RepoResponse } from "@/lib/api/types";
import { setupFixture } from "@/components/setup/setup-fixture";

const mocks = vi.hoisted(() => ({
  getRepoSetup: vi.fn(),
  getOnboardingState: vi.fn(),
  pathname: "/repos/r1/overview",
}));

vi.mock("@/lib/api/setup", () => ({
  getRepoSetup: mocks.getRepoSetup,
  getOnboardingState: mocks.getOnboardingState,
  updateOnboardingState: vi.fn(),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => mocks.pathname,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
// Footer and dialog neighbours fetch on their own; this file is about the pill's wiring.
vi.mock("@/components/repos/add-repo-dialog", () => ({ AddRepoDialog: () => null }));
vi.mock("./feedback-button", () => ({ FeedbackButton: () => null }));
vi.mock("./version-footer", () => ({ VersionFooter: () => null }));
vi.mock("./language-switcher", () => ({ LanguageSwitcher: () => null }));

import { Sidebar } from "./sidebar";
import { MobileNav } from "./mobile-nav";

const repo = (workspace_status: string | null): RepoResponse =>
  ({ id: "r1", name: "orbit-api", workspace_status }) as unknown as RepoResponse;

const PILL = { name: "Setup: 2 of 3 essentials done" };

function wrap(ui: React.ReactElement) {
  return render(<SWRConfig value={{ provider: () => new Map() }}>{ui}</SWRConfig>);
}

beforeEach(() => {
  window.localStorage.clear();
  const setup = setupFixture();
  mocks.getRepoSetup.mockResolvedValue(setup);
  mocks.getOnboardingState.mockResolvedValue(setup.onboarding);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Setup pill in the desktop sidebar", () => {
  it("shows for the active indexed repo", async () => {
    wrap(<Sidebar repos={[repo("indexed")]} />);
    const link = await screen.findByRole("link", PILL);
    expect(link.getAttribute("href")).toBe("/repos/r1/settings/setup");
  });

  it("stays out for a workspace repo that still needs an index", async () => {
    wrap(<Sidebar repos={[repo("needs_index")]} />);
    await Promise.resolve();
    expect(screen.queryByRole("link", PILL)).toBeNull();
    expect(mocks.getRepoSetup).not.toHaveBeenCalled();
  });
});

describe("Setup pill in the mobile nav", () => {
  it("shows for the active indexed repo", async () => {
    wrap(<MobileNav repos={[repo("indexed")]} />);
    fireEvent.click(screen.getByRole("button", { name: /navigation/i }));
    const link = await screen.findByRole("link", PILL);
    expect(link.getAttribute("href")).toBe("/repos/r1/settings/setup");
  });

  it("stays out for a workspace repo that still needs an index", async () => {
    wrap(<MobileNav repos={[repo("needs_index")]} />);
    fireEvent.click(screen.getByRole("button", { name: /navigation/i }));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
    expect(screen.queryByRole("link", PILL)).toBeNull();
    expect(mocks.getRepoSetup).not.toHaveBeenCalled();
  });
});
