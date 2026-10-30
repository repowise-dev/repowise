import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { hydrateRoot } from "react-dom/client";
import type { OverviewSummaryResponse } from "@repowise-dev/types/overview";

import { formatRelativeTime } from "../../src/lib/format.js";
import { RelativeTime } from "../../src/shared/relative-time.js";
import { OverviewBody } from "../../src/overview/overview-body.js";

const RENDERED_AT = new Date("2026-03-20T12:00:00Z");
// The cached HTML is hydrated two days later, across a unit boundary.
const HYDRATED_AT = new Date("2026-03-22T12:00:00Z");
const PREVIOUS_INDEX = "2026-03-19T11:30:00Z";

/** Server-render at one clock and locale, hydrate at another, and return the
 *  recoverable errors React raised plus the hydrated container. */
async function renderThenHydrate(element: React.ReactElement) {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(RENDERED_AT);
  const html = renderToString(element);

  vi.setSystemTime(HYDRATED_AT);
  // A viewer whose locale groups digits differently (en-IN: 12,61,599).
  const toLocaleString = Number.prototype.toLocaleString;
  Number.prototype.toLocaleString = function (this: number) {
    return toLocaleString.call(this, "en-IN");
  };

  const container = document.createElement("div");
  container.innerHTML = html;
  document.body.appendChild(container);
  const errors: unknown[] = [];
  const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
  try {
    let root: ReturnType<typeof hydrateRoot> | undefined;
    await act(async () => {
      root = hydrateRoot(container, element, { onRecoverableError: (e) => errors.push(e) });
    });
    const text = container.textContent ?? "";
    await act(async () => root?.unmount());
    return { errors, html, text };
  } finally {
    Number.prototype.toLocaleString = toLocaleString;
    consoleError.mockRestore();
    container.remove();
  }
}

/** Computes its text while rendering, as a real component does. */
function Ago({ prefix = "" }: { prefix?: string }) {
  return <>{prefix + formatRelativeTime(PREVIOUS_INDEX)}</>;
}

afterEach(() => {
  vi.useRealTimers();
});

describe("RelativeTime", () => {
  it("catches the mismatch it exists for: bare relative text fails to hydrate", async () => {
    const { errors } = await renderThenHydrate(<span><Ago /></span>);
    expect(errors.length).toBeGreaterThan(0);
  });

  it("renders the UTC date on the server, then the relative time once hydrated", async () => {
    const { errors, html, text } = await renderThenHydrate(
      <span>
        <RelativeTime date={PREVIOUS_INDEX} />
      </span>,
    );
    expect(errors).toEqual([]);
    expect(html).toContain("Mar 19, 2026");
    expect(text).toBe("3d ago");
  });

  it("keeps host-worded text and its stable twin", async () => {
    const { errors, html, text } = await renderThenHydrate(
      <RelativeTime date={PREVIOUS_INDEX} stable="since Mar 19">
        <Ago prefix="since " />
      </RelativeTime>,
    );
    expect(errors).toEqual([]);
    expect(html).toContain("since Mar 19");
    expect(text).toBe("since 3d ago");
  });

  it("renders the fallback for a missing date on both sides", async () => {
    const { errors, text } = await renderThenHydrate(<RelativeTime date={null}>never</RelativeTime>);
    expect(errors).toEqual([]);
    expect(text).toBe("never");
  });
});

function summary(): OverviewSummaryResponse {
  return {
    repo: {
      id: "r1",
      name: "acme",
      local_path: "/src/acme",
      default_branch: "main",
      head_commit: "abc1234",
      updated_at: null,
    },
    stats: {
      file_count: 1_261_599,
      symbol_count: 40000,
      entry_point_count: 12,
      doc_page_count: 300,
      doc_prose_page_count: 200,
      doc_coverage_pct: 80,
      freshness_score: 90,
      dead_export_count: 42,
      hotspot_count: 105_900,
      silo_count: 3,
      module_count: 20,
      total_nloc: 500000,
      deltas: { average_health: 0.4, hotspot_health: null, file_count: 123_456 },
    },
    health: {
      average_health: 8.04,
      hotspot_health: 6.66,
      maintainability_average: 8.6,
      performance_average: 9.7,
      worst_performer_path: null,
      worst_performer_score: null,
      open_findings: 10,
      severity_breakdown: {},
      last_indexed_at: null,
      snapshot_count: 2,
      history: [
        { taken_at: PREVIOUS_INDEX, average_health: 7.6 },
        { taken_at: "2026-03-20T11:00:00Z", average_health: 8.04 },
      ],
    },
    languages: [{ language: "python", file_count: 6000 }],
    attention: [],
    onboarding_targets: [],
    top_hotspots: [
      {
        file_path: "src/core/engine.py",
        commit_count_90d: 40,
        commit_count_30d: 12,
        churn_percentile: 99,
        primary_owner: "ada",
        is_hotspot: true,
        is_stable: false,
        bus_factor: 1,
        contributor_count: 3,
        lines_added_90d: 900,
        lines_deleted_90d: 300,
        avg_commit_size: 30,
        commit_categories: {},
        last_fix_at: "2026-03-20T10:59:00Z",
        bug_magnet: true,
      },
    ],
    recent_decisions: [],
    savings: { available: false },
    sync: {
      last_sync_at: null,
      last_resync_at: null,
      last_sync_model: null,
      active_job_id: null,
      page_count: 300,
      index_storage_bytes: 1_000_000,
    },
  } as unknown as OverviewSummaryResponse;
}

describe("OverviewBody hydration", () => {
  it("hydrates a cached render under a later clock and another locale without a mismatch", async () => {
    const { errors, html, text } = await renderThenHydrate(
      <OverviewBody summary={summary()} routes={{ base: "/repos/r1" }} />,
    );
    expect(errors).toEqual([]);
    // Figures stay en-US for every viewer.
    expect(html).toContain("1,261,599");
    expect(html).not.toContain("12,61,599");
    // The change line and the hotspot row word their age once hydrated.
    expect(html).toContain("Since the previous index, Mar 19, 2026");
    expect(text).toContain("Since the previous index, 3d ago");
    expect(text).toContain("2d ago");
  });
});
