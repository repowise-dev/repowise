import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { GenerationProgress } from "../../src/jobs/generation-progress.js";

describe("GenerationProgress", () => {
  it("renders the queued state for pending jobs", () => {
    render(
      <GenerationProgress
        job={{
          id: "j1",
          status: "pending",
          total_pages: 0,
          completed_pages: 0,
        }}
        log={[]}
        elapsed={1000}
        actualCost={null}
        stuckPending={false}
        cancelling={false}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByText(/Queued/)).toBeTruthy();
  });

  it("shows the stuck-pending banner when set", () => {
    render(
      <GenerationProgress
        job={{
          id: "j1",
          status: "pending",
          total_pages: 0,
          completed_pages: 0,
        }}
        log={[]}
        elapsed={45_000}
        actualCost={null}
        stuckPending={true}
        cancelling={false}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByText(/Job hasn't started/)).toBeTruthy();
  });

  it("renders backend phase labels instead of generation level numbers", () => {
    render(
      <GenerationProgress
        job={{
          id: "j1",
          status: "running",
          total_pages: 10,
          completed_pages: 3,
          current_level: 1,
        }}
        log={[]}
        elapsed={10_000}
        actualCost={null}
        stuckPending={false}
        cancelling={false}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByText("Analysing…")).toBeTruthy();
    expect(screen.queryByText(/Generating level/)).toBeNull();
  });

  it("prefers the live SSE phase label over the level-derived one", () => {
    render(
      <GenerationProgress
        job={{
          id: "j1",
          status: "running",
          total_pages: 10,
          completed_pages: 3,
          current_level: 1,
        }}
        log={[]}
        elapsed={10_000}
        actualCost={null}
        stuckPending={false}
        cancelling={false}
        onCancel={vi.fn()}
        phase="Parsing files"
      />,
    );
    expect(screen.getByText("Parsing files…")).toBeTruthy();
    expect(screen.queryByText("Analysing…")).toBeNull();
  });

  it("renders the cancelled state distinctly from failed, with a restart action", () => {
    const onRetry = vi.fn();
    render(
      <GenerationProgress
        job={{
          id: "j1",
          status: "cancelled",
          total_pages: 10,
          completed_pages: 4,
        }}
        log={[]}
        elapsed={10_000}
        actualCost={null}
        stuckPending={false}
        cancelling={false}
        onCancel={vi.fn()}
        onRetry={onRetry}
      />,
    );
    expect(screen.getByText("Cancelled")).toBeTruthy();
    expect(screen.queryByText(/Generation failed/)).toBeNull();
    const restart = screen.getByRole("button", { name: /Start again/ });
    fireEvent.click(restart);
    expect(onRetry).toHaveBeenCalled();
  });

  it("offers retry and provider settings recovery on failure", () => {
    render(
      <GenerationProgress
        job={{
          id: "j1",
          status: "failed",
          total_pages: 10,
          completed_pages: 4,
          error_message: "provider auth failed",
        }}
        log={[]}
        elapsed={10_000}
        actualCost={null}
        stuckPending={false}
        cancelling={false}
        onCancel={vi.fn()}
        onRetry={vi.fn()}
        settingsHref="/settings"
      />,
    );
    expect(screen.getByText("provider auth failed")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Retry/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Provider settings/ })).toBeTruthy();
  });

  it("tints warning log lines", () => {
    // jsdom has no scrollIntoView; the JobLog autoscroll calls it on mount.
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    render(
      <GenerationProgress
        job={{
          id: "j1",
          status: "running",
          total_pages: 10,
          completed_pages: 3,
        }}
        log={[{ text: "3 files skipped: encoding error", level: "warning" }]}
        elapsed={10_000}
        actualCost={null}
        stuckPending={false}
        cancelling={false}
        onCancel={vi.fn()}
      />,
    );
    const line = screen.getByText(/files skipped/).closest("div");
    expect(line?.className).toContain("--color-warning");
  });
});
