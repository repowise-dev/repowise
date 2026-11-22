// @vitest-environment jsdom

import React from "react";
import { SWRConfig } from "swr";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RepoResponse } from "@/lib/api/types";
import { setupFixture } from "@/components/setup/setup-fixture";

const mocks = vi.hoisted(() => ({
  getRepoSetup: vi.fn(),
  pathname: "/repos/r1/overview",
}));

vi.mock("@/lib/api/setup", () => ({ getRepoSetup: mocks.getRepoSetup }));
vi.mock("next/navigation", () => ({
  usePathname: () => mocks.pathname,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
// Footer and dialog neighbours fetch on their own; this file is about the Setup entry's wiring.
vi.mock("@/components/repos/add-repo-dialog", () => ({ AddRepoDialog: () => null }));
vi.mock("./feedback-button", () => ({ FeedbackButton: () => null }));
vi.mock("./version-footer", () => ({ VersionFooter: () => null }));
vi.mock("./language-switcher", () => ({ LanguageSwitcher: () => null }));

import { TooltipProvider } from "@repowise-dev/ui/ui/tooltip";
import { Sidebar } from "./sidebar";
import { MobileNav } from "./mobile-nav";

const repo = (workspace_status: string | null): RepoResponse =>
  ({ id: "r1", name: "orbit-api", workspace_status }) as unknown as RepoResponse;

const OPEN = { name: /^Setup\s*, 2 of 3 essentials done$/ };

function wrap(ui: React.ReactElement) {
  return render(
    <SWRConfig value={{ provider: () => new Map() }}>
      <TooltipProvider>{ui}</TooltipProvider>
    </SWRConfig>,
  );
}

beforeEach(() => {
  mocks.getRepoSetup.mockResolvedValue(setupFixture());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Setup entry in the desktop sidebar", () => {
  it("links to the active indexed repo's Setup with its progress", async () => {
    wrap(<Sidebar repos={[repo("indexed")]} />);
    const link = await screen.findByRole("link", OPEN);
    expect(link.getAttribute("href")).toBe("/repos/r1/settings/setup");
  });

  it("keeps its progress in the name when the sidebar is collapsed", async () => {
    wrap(<Sidebar repos={[repo("indexed")]} />);
    fireEvent.click(screen.getByRole("button", { name: /collapse/i }));
    const link = await screen.findByRole("link", { name: "Setup, 2 of 3 essentials done" });
    expect(link.getAttribute("href")).toBe("/repos/r1/settings/setup");
  });

  it("links to the workspace Setup while the repo still needs an index", async () => {
    wrap(<Sidebar repos={[repo("needs_index")]} />);
    const link = screen.getByRole("link", { name: "Setup" });
    expect(link.getAttribute("href")).toBe("/settings/setup");
    await waitFor(() => expect(mocks.getRepoSetup).not.toHaveBeenCalled());
  });
});

describe("Setup entry in the mobile nav", () => {
  it("links to the active indexed repo's Setup with its progress", async () => {
    wrap(<MobileNav repos={[repo("indexed")]} />);
    fireEvent.click(screen.getByRole("button", { name: /navigation/i }));
    const link = await screen.findByRole("link", OPEN);
    expect(link.getAttribute("href")).toBe("/repos/r1/settings/setup");
  });

  it("links to the workspace Setup while the repo still needs an index", async () => {
    wrap(<MobileNav repos={[repo("needs_index")]} />);
    fireEvent.click(screen.getByRole("button", { name: /navigation/i }));
    const link = await screen.findByRole("link", { name: "Setup" });
    expect(link.getAttribute("href")).toBe("/settings/setup");
    expect(mocks.getRepoSetup).not.toHaveBeenCalled();
  });
});
