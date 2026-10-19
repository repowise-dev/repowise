// @vitest-environment jsdom

import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OperationsPanel } from "./operations-panel";

const mocks = vi.hoisted(() => ({
  jobs: {} as Record<string, Record<string, unknown>>,
  syncRepo: vi.fn(),
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("sonner", () => ({ toast: mocks.toast }));
vi.mock("@/lib/api/repos", () => ({ syncRepo: mocks.syncRepo, fullResyncRepo: vi.fn() }));
vi.mock("@/lib/hooks/use-job", () => ({
  useJob: (id: string) => ({
    job: mocks.jobs[id],
    isActive: false,
    sse: { data: null },
    messages: [],
    phase: null,
  }),
}));

function job(id: string, status: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    repository_id: "r1",
    status,
    total_pages: 3,
    completed_pages: status === "completed" ? 3 : 0,
    failed_pages: 0,
    error_message: status === "failed" ? "boom" : null,
    config: {},
    created_at: "2026-01-01T00:00:00Z",
    started_at: "2026-01-01T00:00:00Z",
    finished_at: "2026-01-01T00:01:00Z",
    ...extra,
  };
}

describe("OperationsPanel", () => {
  afterEach(cleanup);

  it("keeps a failed sync on screen, and a retry reports its own outcome once", async () => {
    mocks.jobs = { j1: job("j1", "failed"), j2: job("j2", "completed") };
    mocks.syncRepo.mockResolvedValueOnce({ job_id: "j1" }).mockResolvedValueOnce({ job_id: "j2" });

    render(<OperationsPanel repoId="r1" repoName="demo" />);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: /Sync/ })));

    expect(screen.getByText("Generation failed")).toBeTruthy();
    expect(mocks.toast.error).toHaveBeenCalledTimes(1);

    await act(async () => fireEvent.click(screen.getByRole("button", { name: /Retry/ })));

    // A fresh panel for the new job: its clean finish toasts once and clears.
    expect(mocks.toast.success).toHaveBeenCalledTimes(1);
    expect(mocks.toast.error).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Generation failed")).toBeNull();
  });
});
