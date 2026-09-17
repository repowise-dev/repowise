// @vitest-environment jsdom

import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  useJob: vi.fn(),
}));

vi.mock("@/lib/hooks/use-job", () => ({ useJob: mocks.useJob }));

import { LiveJobProgress } from "./live-job-progress";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("LiveJobProgress denominator", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the fraction while the phase reports a total", () => {
    mocks.useJob.mockReturnValue({
      job: { id: "j1", completed_pages: 3, total_pages: 5 },
      sse: { data: undefined },
    });
    render(<LiveJobProgress jobId="j1" initialCompleted={0} initialTotal={5} />);
    expect(screen.getByText("3/5 pages")).toBeTruthy();
  });

  it("drops the denominator when the phase reports no total", () => {
    // An unknown-total phase writes 0 (job_executor._async_update), so the
    // previous phase's denominator cannot outlive the phase boundary. The
    // readout must not become "241/0 pages", which is what the raw
    // interpolation produced before this guard.
    mocks.useJob.mockReturnValue({
      job: { id: "j2", completed_pages: 241, total_pages: 0 },
      sse: { data: undefined },
    });
    render(<LiveJobProgress jobId="j2" initialCompleted={0} initialTotal={0} />);
    expect(screen.queryByText("241/0 pages")).toBeNull();
    expect(screen.getByText("241 pages")).toBeTruthy();
  });
});
