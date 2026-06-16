"use client";

import type { FileSignals } from "@repowise-dev/types/health";

export interface FileSignalsPanelProps {
  signals: FileSignals | null | undefined;
  /** Hide the section heading (e.g. when the host supplies its own). */
  hideHeading?: boolean;
}

/**
 * The per-file process / people / topology signals, grouped and captioned.
 * Pure surfacing of data we already compute — no scoring, no recompute. Each
 * value carries a one-line plain-language caption; an absent value reads "no
 * signal" rather than a misleading zero (the contract is null-on-absent, see
 * `FileSignals`). The whole panel is silent when the file has neither git
 * history nor a graph node. Shared by the dashboard drawer and the file-page
 * Health tab so both render identically.
 */
export function FileSignalsPanel({ signals, hideHeading = false }: FileSignalsPanelProps) {
  if (!signals || !hasAnySignal(signals)) return null;

  const ownerHandoff =
    signals.recent_owner_name != null &&
    signals.primary_owner_name != null &&
    signals.recent_owner_name !== signals.primary_owner_name;

  return (
    <section className="space-y-2">
      {!hideHeading && (
        <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
          Signals
        </h3>
      )}
      <div className="grid gap-3 sm:grid-cols-3">
        <Group label="Process">
          <Row
            value={priorDefectValue(signals.prior_defect_count)}
            caption="bug-fix commits in the last 6 months"
            present={signals.prior_defect_count != null}
          />
          <Row
            value={
              signals.change_entropy_pct == null
                ? null
                : `${Math.round(signals.change_entropy_pct)}th pct`
            }
            caption="change scatter — how spread out its edits are"
            present={signals.change_entropy_pct != null}
          />
          <Row
            value={velocityValue(signals)}
            caption="commits and line churn in the last 90 days"
            present={signals.commit_count_90d != null}
          />
          <Row
            value={signals.age_days == null ? null : `${formatAge(signals.age_days)} old`}
            caption="first seen in git history"
            present={signals.age_days != null}
          />
        </Group>

        <Group label="People">
          <Row
            value={ownerValue(signals.primary_owner_name, signals.primary_owner_commit_pct)}
            caption="primary owner (all-time)"
            present={signals.primary_owner_name != null}
          />
          <Row
            value={
              signals.recent_owner_name == null
                ? "No commits (90d)"
                : ownerValue(signals.recent_owner_name, signals.recent_owner_commit_pct)
            }
            caption={
              ownerHandoff ? "recent owner (90d) — differs from primary" : "recent owner (90d)"
            }
            present={signals.recent_owner_name != null}
            emphasize={ownerHandoff}
          />
        </Group>

        <Group label="Topology">
          <Row
            value={
              signals.in_degree == null
                ? null
                : `${signals.in_degree} ${plural(signals.in_degree, "file")} depend on this`
            }
            caption="inbound dependents"
            present={signals.in_degree != null}
          />
          <Row
            value={
              signals.out_degree == null
                ? null
                : `depends on ${signals.out_degree} ${plural(signals.out_degree, "file")}`
            }
            caption="outbound dependencies"
            present={signals.out_degree != null}
          />
        </Group>
      </div>
    </section>
  );
}

function hasAnySignal(s: FileSignals): boolean {
  return (
    s.prior_defect_count != null ||
    s.change_entropy_pct != null ||
    s.commit_count_90d != null ||
    s.age_days != null ||
    s.primary_owner_name != null ||
    s.recent_owner_name != null ||
    s.in_degree != null ||
    s.out_degree != null
  );
}

function priorDefectValue(n: number | null): string | null {
  if (n == null) return null;
  return n === 0 ? "No bug-fixes" : `${n} bug-fix${n === 1 ? "" : "es"}`;
}

function velocityValue(s: FileSignals): string | null {
  if (s.commit_count_90d == null) return null;
  const added = s.lines_added_90d ?? 0;
  const deleted = s.lines_deleted_90d ?? 0;
  return `${s.commit_count_90d} ${plural(s.commit_count_90d, "commit")} · +${added} / −${deleted}`;
}

function ownerValue(name: string | null, pct: number | null): string | null {
  if (name == null) return null;
  return pct == null ? name : `${name} (${Math.round(pct * 100)}%)`;
}

function formatAge(days: number): string {
  if (days >= 365) {
    const years = days / 365;
    return `${years.toFixed(years >= 10 ? 0 : 1)}y`;
  }
  if (days >= 30) return `${Math.round(days / 30)}mo`;
  return `${days}d`;
}

function plural(n: number, word: string): string {
  return n === 1 ? word : `${word}s`;
}

function Group({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-3 space-y-2">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)]">
        {label}
      </p>
      <div className="space-y-2">{children}</div>
    </div>
  );
}

function Row({
  value,
  caption,
  present,
  emphasize = false,
}: {
  value: string | null;
  caption: string;
  present: boolean;
  emphasize?: boolean;
}) {
  return (
    <div>
      <p
        className={`text-xs font-semibold tabular-nums ${
          present
            ? emphasize
              ? "text-[var(--color-accent-primary)]"
              : "text-[var(--color-text-primary)]"
            : "text-[var(--color-text-tertiary)]"
        }`}
      >
        {present ? value : "No signal"}
      </p>
      <p className="text-[10px] leading-tight text-[var(--color-text-tertiary)]">{caption}</p>
    </div>
  );
}
