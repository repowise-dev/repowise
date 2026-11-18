import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { GenerationProgress } from "../../src/jobs/generation-progress.js";

describe("GenerationProgress", () => {
  it.each([
    { completed: 241, total: 5, percentage: 100 },
    { completed: -1, total: 5, percentage: 0 },
    { completed: 3, total: 10, percentage: 30 },
    { completed: 5, total: 5, percentage: 100 },
    { completed: 241, total: 0, percentage: 0 },
  ])("shows $percentage% for $completed / $total pages", ({ completed, total, percentage }) => {
    render(
      <GenerationProgress
        job={{
          id: "j1",
          status: "running",
          total_pages: total,
          completed_pages: completed,
        }}
        log={[]}
        elapsed={1000}
        actualCost={null}
        stuckPending={false}
        cancelling={false}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText(`${percentage}%`)).toBeInTheDocument();
    expect(screen.getByText(`${completed} / ${total} pages`)).toBeInTheDocument();
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", String(percentage));
    expect(bar.firstElementChild).toHaveStyle({
      transform: `translateX(${percentage - 100}%)`,
    });
  });

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

  it("shows a completed run whose key was rejected as a warning, with its fixes", () => {
    const onDismiss = vi.fn();
    render(
      <GenerationProgress
        job={{
          id: "j1",
          status: "completed",
          total_pages: 4,
          completed_pages: 0,
          failed_pages: 4,
          config: {
            provider_issue: {
              kind: "auth",
              message: "API key rejected by openai. Update it in Settings.",
            },
          },
        }}
        log={[]}
        elapsed={10_000}
        actualCost={null}
        stuckPending={false}
        cancelling={false}
        onCancel={vi.fn()}
        onRetry={vi.fn()}
        onDismiss={onDismiss}
        settingsHref="/repos/r1/settings/models"
      />,
    );
    expect(screen.getByText("Finished with problems")).toBeTruthy();
    expect(screen.queryByText("Generation complete")).toBeNull();
    expect(screen.getByText("No documentation pages were written.")).toBeTruthy();
    expect(screen.getByText(/API key rejected by openai/)).toBeTruthy();
    expect(screen.getByRole("link", { name: /Provider settings/ })).toHaveAttribute(
      "href",
      "/repos/r1/settings/models",
    );
    fireEvent.click(screen.getByRole("button", { name: /Dismiss/ }));
    expect(onDismiss).toHaveBeenCalled();
  });

  it("counts partly failed pages without offering provider settings it cannot justify", () => {
    render(
      <GenerationProgress
        job={{ id: "j1", status: "completed", total_pages: 10, completed_pages: 7, failed_pages: 3 }}
        log={[]}
        elapsed={10_000}
        actualCost={null}
        stuckPending={false}
        cancelling={false}
        onCancel={vi.fn()}
        settingsHref="/settings"
        labels={{ somePagesFailed: (f, t) => `${f}/${t} lost` }}
      />,
    );
    expect(screen.getByText("3/10 lost")).toBeTruthy();
    expect(screen.queryByRole("link", { name: /Provider settings/ })).toBeNull();
  });

  it("offers settings, not a retry, when docs were skipped, worded by the host", () => {
    render(
      <GenerationProgress
        job={{
          id: "j1",
          status: "completed",
          total_pages: 0,
          completed_pages: 0,
          failed_pages: 0,
          config: { provider_issue: { kind: "docs_skipped", message: "English text" } },
        }}
        log={[]}
        elapsed={10_000}
        actualCost={null}
        stuckPending={false}
        cancelling={false}
        onCancel={vi.fn()}
        onRetry={vi.fn()}
        onDismiss={vi.fn()}
        settingsHref="/s"
        formatIssue={(issue) => `localized ${issue.kind}`}
      />,
    );
    expect(screen.getByText("Documentation was not updated.")).toBeTruthy();
    expect(screen.getByText("localized docs_skipped")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Retry/ })).toBeNull();
    expect(screen.getByRole("button", { name: /Dismiss/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Provider settings/ })).toBeTruthy();
  });

  it("keeps a clean run quiet", () => {
    render(
      <GenerationProgress
        job={{ id: "j1", status: "completed", total_pages: 5, completed_pages: 5, failed_pages: 0 }}
        log={[]}
        elapsed={10_000}
        actualCost={null}
        stuckPending={false}
        cancelling={false}
        onCancel={vi.fn()}
        onDismiss={vi.fn()}
      />,
    );
    expect(screen.getByText("Generation complete")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Dismiss/ })).toBeNull();
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
