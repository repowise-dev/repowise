"use client";

import { ArrowLeftRight } from "lucide-react";
import { PERF_BOUNDARY_LABEL } from "@repowise-dev/types/health";
import type { C4IoKind } from "@repowise-dev/types/external-systems";

export type BiomarkerDetailsRecord = Record<string, unknown>;

export interface BiomarkerDetailsProps {
  biomarkerType: string;
  details?: BiomarkerDetailsRecord | null | undefined;
  onPartnerSelect?: ((path: string) => void) | undefined;
  onPartnerHref?: ((path: string) => string) | undefined;
}

function num(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v !== "" && Number.isFinite(Number(v))) {
    return Number(v);
  }
  return null;
}

function str(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}

function pct(v: number | null, digits = 0): string | null {
  return v == null ? null : `${(v * 100).toFixed(digits)}%`;
}

/** Compose " · "-joined stat fragments, dropping empties. */
function joinStats(...parts: Array<string | null | undefined>): string | null {
  const kept = parts.filter((p): p is string => Boolean(p));
  return kept.length ? kept.join(" · ") : null;
}

function StatLine({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-xs text-[var(--color-text-tertiary)] tabular-nums">
      {children}
    </div>
  );
}

function PartnerLink({
  path,
  onPartnerSelect,
  onPartnerHref,
}: {
  path: string;
  onPartnerSelect?: ((path: string) => void) | undefined;
  onPartnerHref?: ((path: string) => string) | undefined;
}) {
  const href = onPartnerHref ? onPartnerHref(path) : undefined;
  const inner = (
    <span className="inline-flex items-center gap-1 font-mono truncate">
      <ArrowLeftRight className="h-3 w-3 shrink-0" aria-hidden="true" />
      <span className="truncate">{path}</span>
    </span>
  );
  if (href) {
    return (
      <a
        href={href}
        onClick={(e) => {
          if (onPartnerSelect) {
            e.preventDefault();
            onPartnerSelect(path);
          }
        }}
        className="text-[var(--color-accent-primary)] hover:underline"
      >
        {inner}
      </a>
    );
  }
  if (onPartnerSelect) {
    return (
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onPartnerSelect(path);
        }}
        className="text-[var(--color-accent-primary)] hover:underline text-left"
      >
        {inner}
      </button>
    );
  }
  return inner;
}

const ERROR_HANDLING_KIND_LABEL: Record<string, string> = {
  swallowed_exception: "swallowed exception",
  bare_except: "bare except",
  broad_except: "broad except",
  unsafe_unwrap: "unsafe unwrap",
  panic_macro: "panic macro",
  discarded_error: "discarded error return",
  empty_catch: "empty catch block",
};

/**
 * Per-type rendering of a finding's ``details_json``. Every biomarker the
 * scorer can emit gets a compact stat line; partner-bearing types
 * (hidden_coupling, dry_violation, duplicated_assertion_block) additionally
 * render the partner file as a link.
 */
export function BiomarkerDetails({
  biomarkerType,
  details,
  onPartnerSelect,
  onPartnerHref,
}: BiomarkerDetailsProps) {
  if (!details) return null;

  if (biomarkerType === "hidden_coupling") {
    const partner = str(details.partner);
    if (!partner) return null;
    const corrPct = pct(num(details.correlation));
    const coChanges = num(details.co_change_count);
    return (
      <div className="text-xs text-[var(--color-text-tertiary)]">
        <PartnerLink
          path={partner}
          onPartnerSelect={onPartnerSelect}
          onPartnerHref={onPartnerHref}
        />
        {corrPct || coChanges != null ? (
          <span className="ml-1 tabular-nums">
            {corrPct ? ` · ${corrPct} co-change` : ""}
            {coChanges != null ? ` · ${coChanges} commits` : ""}
          </span>
        ) : null}
      </div>
    );
  }

  if (biomarkerType === "dry_violation") {
    const partner = str(details.worst_clone_partner);
    const dup = num(details.duplication_pct);
    const stats = joinStats(
      dup != null ? `${dup.toFixed(0)}% duplicated` : null,
      num(details.clone_pair_count) != null
        ? `${num(details.clone_pair_count)} clone pairs`
        : null,
      num(details.worst_clone_lines) != null
        ? `worst clone ${num(details.worst_clone_lines)} lines`
        : null,
      num(details.worst_clone_co_change)
        ? `${num(details.worst_clone_co_change)} co-changes`
        : null,
    );
    if (!partner && !stats) return null;
    return (
      <div className="text-xs text-[var(--color-text-tertiary)]">
        {stats ? <span className="tabular-nums">{stats}</span> : null}
        {partner ? (
          <div>
            <PartnerLink
              path={partner}
              onPartnerSelect={onPartnerSelect}
              onPartnerHref={onPartnerHref}
            />
          </div>
        ) : null}
      </div>
    );
  }

  if (biomarkerType === "duplicated_assertion_block") {
    const partner = str(details.partner_file);
    const lines = Array.isArray(details.assertion_lines)
      ? (details.assertion_lines as unknown[]).map(num).filter((v) => v != null)
      : [];
    return (
      <div className="text-xs text-[var(--color-text-tertiary)]">
        {lines.length === 2 ? (
          <span className="tabular-nums">lines {lines[0]}–{lines[1]}</span>
        ) : null}
        {partner ? (
          <div>
            <PartnerLink
              path={partner}
              onPartnerSelect={onPartnerSelect}
              onPartnerHref={onPartnerHref}
            />
          </div>
        ) : null}
      </div>
    );
  }

  if (biomarkerType === "io_in_loop") {
    const kind = str(details.boundary_kind);
    const label = kind ? (PERF_BOUNDARY_LABEL[kind as C4IoKind] ?? kind) : null;
    const crossFn = details.cross_function === true;
    // The resolved caller -> ... -> sink chain (cross-function N+1 only). Each
    // segment is "file.py::Name"; render the bare function names.
    const path = Array.isArray(details.path)
      ? (details.path as unknown[])
          .map((seg) => (typeof seg === "string" ? seg.split("::").pop() ?? seg : null))
          .filter((s): s is string => Boolean(s))
      : [];
    return (
      <div className="text-xs text-[var(--color-text-tertiary)] space-y-0.5">
        {label ? (
          <div>
            <span className="font-medium text-[var(--color-text-secondary)]">{label}</span>
            {" boundary"}
            {crossFn ? " · cross-function N+1" : " · in loop body"}
          </div>
        ) : null}
        {path.length > 1 ? (
          <div className="font-mono truncate" title={path.join(" → ")}>
            {path.join(" → ")}
          </div>
        ) : null}
      </div>
    );
  }

  if (biomarkerType === "blocking_sync_in_async") {
    const api = str(details.api);
    if (!api) return null;
    return (
      <StatLine>
        <span className="font-mono">{api}</span> blocks the event loop
      </StatLine>
    );
  }

  let line: string | null = null;
  switch (biomarkerType) {
    case "complex_conditional": {
      const ops = num(details.operator_count);
      const construct = str(details.enclosing_construct);
      line =
        ops == null
          ? null
          : `${ops} boolean operators${construct ? ` in ${construct}` : ""}`;
      break;
    }
    case "function_hotspot": {
      const mod = num(details.modification_count) ?? num(details.mod_count);
      const p80 = num(details.repo_p80) ?? num(details.p80);
      line =
        mod == null
          ? null
          : joinStats(
              `mod ${mod}${p80 != null ? ` / p80 ${p80}` : ""}`,
              num(details.ccn) != null ? `CCN ${num(details.ccn)}` : null,
              num(details.max_nesting) != null
                ? `nest ${num(details.max_nesting)}`
                : null,
            );
      break;
    }
    case "code_age_volatility": {
      const age = num(details.median_age_days);
      const recent = num(details.recent_mod_count);
      line = joinStats(
        age != null ? `~${age}d old` : null,
        recent != null ? `${recent} edits/30d` : null,
      );
      break;
    }
    case "brain_method":
      line = joinStats(
        num(details.ccn) != null ? `CCN ${num(details.ccn)}` : null,
        num(details.nloc) != null ? `${num(details.nloc)} NLOC` : null,
        num(details.max_nesting) != null ? `nest ${num(details.max_nesting)}` : null,
        num(details.dependents_count) != null
          ? `${num(details.dependents_count)} dependents`
          : null,
      );
      break;
    case "complex_method":
      line = joinStats(
        num(details.ccn) != null ? `CCN ${num(details.ccn)}` : null,
        num(details.cognitive) != null
          ? `cognitive ${num(details.cognitive)}`
          : null,
        num(details.nloc) != null ? `${num(details.nloc)} NLOC` : null,
      );
      break;
    case "large_method":
      line = joinStats(
        num(details.nloc) != null ? `${num(details.nloc)} NLOC` : null,
        num(details.ccn) != null ? `CCN ${num(details.ccn)}` : null,
      );
      break;
    case "nested_complexity":
      line = joinStats(
        num(details.max_nesting) != null
          ? `nest ${num(details.max_nesting)}`
          : null,
        num(details.ccn) != null ? `CCN ${num(details.ccn)}` : null,
        num(details.cognitive) != null
          ? `cognitive ${num(details.cognitive)}`
          : null,
      );
      break;
    case "bumpy_road":
      line = joinStats(
        num(details.bumps) != null ? `${num(details.bumps)} bumps` : null,
        num(details.ccn) != null ? `CCN ${num(details.ccn)}` : null,
        num(details.max_nesting) != null ? `nest ${num(details.max_nesting)}` : null,
      );
      break;
    case "primitive_obsession": {
      const params = num(details.param_count);
      line = params == null ? null : `${params} parameters`;
      break;
    }
    case "god_class": {
      const name = str(details.class_name);
      const stats = joinStats(
        num(details.method_count) != null
          ? `${num(details.method_count)} methods`
          : null,
        num(details.total_nloc) != null
          ? `${num(details.total_nloc)} NLOC`
          : null,
        num(details.max_method_ccn) != null
          ? `max CCN ${num(details.max_method_ccn)}`
          : null,
      );
      line = stats ? (name ? `${name}: ${stats}` : stats) : null;
      break;
    }
    case "low_cohesion": {
      const name = str(details.class_name);
      const stats = joinStats(
        num(details.lcom4) != null ? `LCOM4 ${num(details.lcom4)}` : null,
        num(details.method_count) != null
          ? `${num(details.method_count)} methods`
          : null,
        num(details.field_count) != null
          ? `${num(details.field_count)} fields`
          : null,
      );
      line = stats ? (name ? `${name}: ${stats}` : stats) : null;
      break;
    }
    case "coverage_gap": {
      const uncovered = num(details.uncovered_lines);
      const total = num(details.total_coverable_lines);
      line = joinStats(
        uncovered != null && total != null
          ? `${uncovered}/${total} lines uncovered`
          : null,
        num(details.line_coverage_pct) != null
          ? `${num(details.line_coverage_pct)?.toFixed(0)}% line coverage`
          : null,
        num(details.branch_coverage_pct) != null
          ? `${num(details.branch_coverage_pct)?.toFixed(0)}% branch`
          : null,
      );
      break;
    }
    case "coverage_gradient":
      line = joinStats(
        num(details.line_coverage_pct) != null
          ? `${num(details.line_coverage_pct)?.toFixed(0)}% line coverage`
          : null,
        pct(num(details.uncovered_fraction)) != null
          ? `${pct(num(details.uncovered_fraction))} uncovered`
          : null,
      );
      break;
    case "untested_hotspot":
      line = joinStats(
        num(details.line_coverage_pct) != null
          ? `${num(details.line_coverage_pct)?.toFixed(0)}% coverage`
          : null,
        details.has_test_file === false ? "no test file" : null,
        num(details.dependents_count) != null
          ? `${num(details.dependents_count)} dependents`
          : null,
        num(details.commit_count_90d) != null
          ? `${num(details.commit_count_90d)} commits/90d`
          : null,
      );
      break;
    case "developer_congestion": {
      const owner = str(details.primary_owner);
      line = joinStats(
        num(details.contributor_count) != null
          ? `${num(details.contributor_count)} authors`
          : null,
        num(details.commit_count_90d) != null
          ? `${num(details.commit_count_90d)} commits/90d`
          : null,
        pct(num(details.primary_owner_share)) != null
          ? `top share ${pct(num(details.primary_owner_share))}${owner ? ` (${owner})` : ""}`
          : null,
      );
      break;
    }
    case "knowledge_loss": {
      const primary = str(details.primary_owner);
      const recent = str(details.recent_owner);
      line = joinStats(
        num(details.bus_factor) != null
          ? `bus factor ${num(details.bus_factor)}`
          : null,
        primary ? `primary ${primary}` : null,
        recent
          ? `recent ${recent}${
              pct(num(details.recent_owner_share))
                ? ` (${pct(num(details.recent_owner_share))})`
                : ""
            }`
          : "no recent owner",
      );
      break;
    }
    case "ownership_risk":
      line = joinStats(
        num(details.minor_contributors) != null &&
          num(details.contributor_count) != null
          ? `${num(details.minor_contributors)} minor of ${num(details.contributor_count)} authors`
          : null,
        pct(num(details.top_owner_share)) != null
          ? `top share ${pct(num(details.top_owner_share))}`
          : null,
        num(details.total_commits) != null
          ? `${num(details.total_commits)} commits`
          : null,
      );
      break;
    case "churn_risk": {
      const added = num(details.lines_added_90d);
      const deleted = num(details.lines_deleted_90d);
      line = joinStats(
        num(details.relative_churn) != null
          ? `relative churn ${num(details.relative_churn)?.toFixed(1)}×`
          : null,
        added != null || deleted != null
          ? `+${added ?? 0}/−${deleted ?? 0} lines/90d`
          : null,
        num(details.commit_count_90d) != null
          ? `${num(details.commit_count_90d)} commits`
          : null,
      );
      break;
    }
    case "change_entropy": {
      const p = num(details.change_entropy_pct);
      line = joinStats(
        p != null ? `top ${Math.max(0, Math.round((1 - p) * 100))}% entropy` : null,
        num(details.commit_count_90d) != null
          ? `${num(details.commit_count_90d)} commits/90d`
          : null,
      );
      break;
    }
    case "co_change_scatter":
      line = joinStats(
        num(details.scatter) != null
          ? `co-changes with ${num(details.scatter)} files`
          : null,
        num(details.commit_count_90d) != null
          ? `${num(details.commit_count_90d)} commits/90d`
          : null,
      );
      break;
    case "prior_defect": {
      const count = num(details.prior_defect_count);
      const windowDays = num(details.window_days);
      line =
        count == null
          ? null
          : `${count} bug ${count === 1 ? "fix" : "fixes"}${
              windowDays != null ? ` in ${windowDays}d` : ""
            }`;
      break;
    }
    case "large_assertion_block": {
      const fn = str(details.function);
      const count = num(details.assertion_count);
      line =
        count == null
          ? null
          : `${fn ? `${fn}: ` : ""}${count} assertions in one block`;
      break;
    }
    case "error_handling": {
      const kind = str(details.kind);
      line = kind ? ERROR_HANDLING_KIND_LABEL[kind] ?? kind.replace(/_/g, " ") : null;
      break;
    }
    case "stale_governance": {
      const title = str(details.decision_title);
      const staleness = num(details.staleness_score);
      line = joinStats(
        title ? `decision “${title}”` : null,
        staleness != null ? `staleness ${staleness.toFixed(2)}` : null,
      );
      break;
    }
    case "contradictory_decision": {
      const src = str(details.src_title);
      const dst = str(details.dst_title);
      line =
        src && dst ? `“${src}” contradicts “${dst}”` : dst ? `contradicts “${dst}”` : null;
      break;
    }
    default:
      line = null;
  }

  if (!line) return null;
  return <StatLine>{line}</StatLine>;
}
