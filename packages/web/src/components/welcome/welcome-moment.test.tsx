// @vitest-environment jsdom

import React, { StrictMode } from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { sessionMomentTaken } from "@/lib/release-moment";
import { WelcomeMoment } from "./welcome-moment";

const mocks = vi.hoisted(() => ({
  search: "",
  welcomeSeen: {} as Record<string, string>,
  getRepoSetup: vi.fn(),
  updateOnboardingState: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(mocks.search),
}));
vi.mock("@/lib/api/setup", () => ({
  getRepoSetup: mocks.getRepoSetup,
  updateOnboardingState: mocks.updateOnboardingState,
}));
vi.mock("@/lib/api/meta", () => ({ getMetaVersion: async () => ({ server_version: "0.59.0" }) }));
vi.mock("@/lib/api/dead-code", () => ({ getDeadCodeSummary: async () => Promise.reject(new Error("x")) }));
vi.mock("@/lib/api/blast-radius", () => ({ analyzeBlastRadius: async () => Promise.reject(new Error("x")) }));
vi.mock("@/lib/api/code-health", () => ({ getHealthCoverage: async () => Promise.reject(new Error("x")) }));
vi.mock("@/lib/api/stats", () => ({ getStatsHighlights: async () => Promise.reject(new Error("x")) }));
// The dialog itself is covered in welcome-dialog.test.tsx.
vi.mock("./welcome-dialog", () => ({
  WelcomeDialog: ({ open }: { open: boolean }) => (open ? <div role="dialog">welcome</div> : null),
}));

const FIGURES = { health: null, decisionsToReview: null, docPages: 10, coveragePct: null };

function renderMoment() {
  render(
    <StrictMode>
      <WelcomeMoment repoId="r1" repoName="orbit-api" fileCount={12} figures={FIGURES} hotspotPath={null} />
    </StrictMode>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  mocks.search = "";
  mocks.welcomeSeen = {};
  mocks.getRepoSetup.mockImplementation(async () => ({
    onboarding: { welcome_seen: mocks.welcomeSeen, hints_enabled: true, signed_in: false },
  }));
  mocks.updateOnboardingState.mockResolvedValue({});
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("WelcomeMoment", () => {
  it("opens once for a new browser and records the repo, the version and the session", async () => {
    renderMoment();
    await screen.findByRole("dialog");
    expect(mocks.updateOnboardingState).toHaveBeenCalledTimes(1);
    expect(mocks.updateOnboardingState).toHaveBeenCalledWith({ welcome_seen_repo: "r1" });
    expect(window.localStorage.getItem("repowise:last-seen-server-version")).toBe("0.59.0");
    expect(sessionMomentTaken()).toBe(true);
  });

  it("stays shut for an existing user", async () => {
    window.localStorage.setItem("repowise:last-seen-server-version", "0.58.0");
    renderMoment();
    await waitFor(() => expect(mocks.getRepoSetup).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(mocks.updateOnboardingState).not.toHaveBeenCalled();
  });

  it("replays on demand with ?tour=1 without stamping a repo already seen", async () => {
    window.localStorage.setItem("repowise:last-seen-server-version", "0.58.0");
    mocks.search = "tour=1";
    mocks.welcomeSeen = { r1: "2026-10-20T00:00:00Z" };
    renderMoment();
    await screen.findByRole("dialog");
    expect(mocks.updateOnboardingState).not.toHaveBeenCalled();
    expect(window.localStorage.getItem("repowise:last-seen-server-version")).toBe("0.58.0");
    expect(sessionMomentTaken()).toBe(false);
  });
});
