import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
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
});
