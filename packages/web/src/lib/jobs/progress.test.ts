import { describe, expect, it } from "vitest";
import type { JobProgressEvent, JobResponse } from "@/lib/api/types";
import { computeElapsedMs, mergeJobProgress } from "./progress";

function job(overrides: Partial<JobResponse> = {}): JobResponse {
  return {
    id: "job-1",
    repository_id: "repo-1",
    status: "running",
    provider_name: "mock",
    model_name: "mock",
    total_pages: 10,
    completed_pages: 1,
    failed_pages: 0,
    current_level: 0,
    error_message: null,
    config: {},
    created_at: "2026-06-15T10:00:00.000Z",
    updated_at: "2026-06-15T10:01:00.000Z",
    started_at: "2026-06-15T10:00:30.000Z",
    finished_at: null,
    ...overrides,
  };
}

describe("job progress helpers", () => {
  it("derives running elapsed time from persisted start time", () => {
    expect(computeElapsedMs(job(), Date.parse("2026-06-15T10:02:00.000Z"))).toBe(90_000);
  });

  it("freezes completed elapsed time at finished_at", () => {
    expect(
      computeElapsedMs(
        job({
          status: "completed",
          finished_at: "2026-06-15T10:03:00.000Z",
        }),
        Date.parse("2026-06-15T10:10:00.000Z"),
      ),
    ).toBe(150_000);
  });

  it("merges SSE progress into a stale polled job snapshot", () => {
    const event: JobProgressEvent = {
      event: "progress",
      job_id: "job-1",
      status: "running",
      completed_pages: 7,
      total_pages: 12,
      failed_pages: 2,
      current_level: 2,
    };

    expect(mergeJobProgress(job(), event)).toMatchObject({
      status: "running",
      completed_pages: 7,
      total_pages: 12,
      failed_pages: 2,
      current_level: 2,
    });
  });

  it("ignores SSE progress for a different job", () => {
    const current = job();
    expect(
      mergeJobProgress(current, {
        event: "progress",
        job_id: "other-job",
        completed_pages: 9,
        total_pages: 9,
      }),
    ).toBe(current);
  });
});
