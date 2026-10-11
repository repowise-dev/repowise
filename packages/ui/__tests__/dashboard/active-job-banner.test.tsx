import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ActiveJobBanner } from "../../src/dashboard/active-job-banner.js";
import type { Job } from "@repowise-dev/types/jobs";

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: "j1",
    repository_id: "r1",
    status: "running",
    provider_name: "openai",
    model_name: "gpt-5.4-mini",
    total_pages: 10,
    completed_pages: 3,
    failed_pages: 0,
    current_level: 0,
    error_message: null,
    config: { mode: "sync" },
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    started_at: "2026-01-01T00:00:00Z",
    finished_at: null,
    ...overrides,
  };
}

describe("ActiveJobBanner", () => {
  it("renders nothing when job is null", () => {
    const { container } = render(<ActiveJobBanner job={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders running state with progress fraction", () => {
    render(<ActiveJobBanner job={makeJob()} />);
    expect(screen.getByText(/Sync · Indexing/)).toBeInTheDocument();
    expect(screen.getByText("3/10")).toBeInTheDocument();
  });

  it("renders failed state with error message", () => {
    render(
      <ActiveJobBanner
        job={makeJob({ status: "failed", error_message: "boom" })}
      />,
    );
    expect(screen.getByText("Sync failed")).toBeInTheDocument();
    expect(screen.getByText("boom")).toBeInTheDocument();
  });

  it("renders details link only when href is provided", () => {
    const { rerender } = render(
      <ActiveJobBanner
        job={makeJob({ status: "completed", completed_pages: 10, finished_at: "2026-01-01T00:01:00Z" })}
      />,
    );
    expect(screen.queryByText("Details")).not.toBeInTheDocument();
    rerender(
      <ActiveJobBanner
        job={makeJob({ status: "completed", completed_pages: 10, finished_at: "2026-01-01T00:01:00Z" })}
        detailsHref="/repos/abc/overview"
      />,
    );
    expect(screen.getByText("Details")).toBeInTheDocument();
  });
});
