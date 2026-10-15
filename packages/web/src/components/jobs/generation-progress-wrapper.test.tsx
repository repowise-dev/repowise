// @vitest-environment jsdom

import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GenerationProgressWrapper } from "./generation-progress-wrapper";

const mocks = vi.hoisted(() => ({
  state: null as unknown as { job: Record<string, unknown>; isActive: boolean },
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("sonner", () => ({ toast: mocks.toast }));
vi.mock("@/lib/hooks/use-job", () => ({
  useJob: () => ({ ...mocks.state, sse: { data: null }, messages: [], phase: null }),
}));

function job(overrides: Record<string, unknown>) {
  return {
    id: "j1",
    repository_id: "r1",
    status: "completed",
    total_pages: 4,
    completed_pages: 0,
    failed_pages: 4,
    error_message: null,
    config: {
      provider_issue: { kind: "auth", message: "API key rejected by openai. Update it in Settings." },
    },
    created_at: "2026-01-01T00:00:00Z",
    started_at: "2026-01-01T00:00:00Z",
    finished_at: "2026-01-01T00:01:00Z",
    ...overrides,
  };
}

describe("GenerationProgressWrapper", () => {
  afterEach(cleanup);
  beforeEach(() => {
    Object.values(mocks.toast).forEach((fn) => fn.mockReset());
  });

  it("waits for the fetched job before settling, so the run summary is known", () => {
    mocks.state = { job: job({}), isActive: true };
    const onDone = vi.fn();

    render(<GenerationProgressWrapper jobId="j1" onDone={onDone} />);

    expect(onDone).not.toHaveBeenCalled();
    expect(mocks.toast.success).not.toHaveBeenCalled();
  });

  it("reports a run that lost every page to a rejected key as a warning", () => {
    mocks.state = { job: job({}), isActive: false };
    const onDone = vi.fn();

    render(<GenerationProgressWrapper jobId="j1" onDone={onDone} onDismiss={vi.fn()} />);

    expect(onDone).toHaveBeenCalledWith("warning");
    expect(mocks.toast.success).not.toHaveBeenCalled();
    expect(mocks.toast.warning).toHaveBeenCalledWith(
      "Finished with problems",
      expect.objectContaining({ description: "API key rejected by openai. Update it in Settings." }),
    );
    expect(screen.getByRole("link", { name: /Provider settings/ }).getAttribute("href")).toBe(
      "/repos/r1/settings#provider",
    );
  });

  it("keeps a clean run a success", () => {
    mocks.state = {
      job: job({ completed_pages: 4, failed_pages: 0, config: {} }),
      isActive: false,
    };
    const onDone = vi.fn();

    render(<GenerationProgressWrapper jobId="j1" onDone={onDone} />);

    expect(onDone).toHaveBeenCalledWith("completed");
    expect(mocks.toast.success).toHaveBeenCalled();
  });
});
