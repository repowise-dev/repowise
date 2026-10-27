/**
 * Fixtures the file-drawer tests share: a metric row, a finding, a signals
 * row, and opening a collapsed section by its toggle.
 */

import { fireEvent, screen } from "@testing-library/react";
import type { FileSignals } from "@repowise-dev/types/health";
import type {
  HealthDrawerFinding,
  HealthDrawerMetric,
} from "../../src/health/health-file-drawer.js";

export function drawerMetric(partial: Partial<HealthDrawerMetric> = {}): HealthDrawerMetric {
  return {
    file_path: "src/a.py",
    score: 1.0,
    max_ccn: 40,
    max_nesting: 6,
    nloc: 800,
    module: "src",
    has_test_file: false,
    ...partial,
  };
}

let seq = 0;
export function drawerFinding(partial: Partial<HealthDrawerFinding> = {}): HealthDrawerFinding {
  seq += 1;
  return {
    id: `f${seq}`,
    biomarker_type: "brain_method",
    severity: "high",
    function_name: "_run_repo_checks",
    line_start: 120,
    line_end: 487,
    health_impact: 1.5,
    reason: "Oversized, deeply-nested function.",
    ...partial,
  };
}

export function drawerSignals(partial: Partial<FileSignals> = {}): FileSignals {
  return {
    prior_defect_count: 2,
    change_entropy_pct: null,
    lines_added_90d: null,
    lines_deleted_90d: null,
    commit_count_90d: 14,
    age_days: null,
    primary_owner_name: null,
    primary_owner_commit_pct: null,
    recent_owner_name: null,
    recent_owner_commit_pct: null,
    in_degree: 4,
    out_degree: 1,
    bug_magnet: false,
    last_fix_at: null,
    fix_symbol_counts: null,
    ...partial,
  };
}

/** Open a collapsed drawer section by its toggle. */
export function openSection(name: RegExp) {
  fireEvent.click(screen.getByRole("button", { name }));
}
