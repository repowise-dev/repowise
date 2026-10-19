// @vitest-environment jsdom

import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ActiveJobBannerWrapper } from "./active-job-banner-wrapper";

const mocks = vi.hoisted(() => ({ jobs: [] as Record<string, unknown>[] }));

vi.mock("swr", () => ({ default: () => ({ data: mocks.jobs }) }));

const HOUR = 60 * 60_000;

function failedJob(finishedAgoMs: number) {
  return {
    id: "j1",
    repository_id: "r1",
    status: "failed",
    total_pages: 0,
    completed_pages: 0,
    failed_pages: 0,
    current_level: 0,
    error_message: "API key rejected by openai. Update it in Settings.",
    config: {
      mode: "sync",
      provider_issue: { kind: "auth", message: "API key rejected by openai.", provider: "openai" },
    },
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    started_at: "2026-01-01T00:00:00Z",
    finished_at: new Date(Date.now() - finishedAgoMs).toISOString(),
  };
}

describe("ActiveJobBannerWrapper", () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(cleanup);

  it("keeps a failure up for a day, past the one minute a clean finish gets", () => {
    mocks.jobs = [failedJob(2 * HOUR)];
    render(<ActiveJobBannerWrapper repoId="r1" />);
    expect(screen.getByRole("alert").textContent).toContain("Sync failed");
    expect(screen.getByRole("link", { name: /Provider settings/ }).getAttribute("href")).toBe(
      "/repos/r1/settings#provider",
    );
  });

  it("drops a failure older than a day", () => {
    mocks.jobs = [failedJob(25 * HOUR)];
    render(<ActiveJobBannerWrapper repoId="r1" />);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("remembers a dismissal across reloads, per repo", async () => {
    mocks.jobs = [failedJob(HOUR)];
    const first = render(<ActiveJobBannerWrapper repoId="r1" />);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Dismiss" })));
    expect(screen.queryByRole("alert")).toBeNull();
    first.unmount();

    render(<ActiveJobBannerWrapper repoId="r1" />);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(window.localStorage.getItem("repowise:job-banner-dismissed:r1")).toBe("j1");
  });
});
