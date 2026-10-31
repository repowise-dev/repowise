import type { CloudEvent } from "@repowise-dev/types/events";
import { SeverityMark } from "../health/severity-mark";
import type { Severity } from "../health/tokens";
import { MICRO } from "../health/tokens";

/** What each event kind means, in English. A host passes its own words through `labels`. */
export const COMMIT_EVENT_KIND_LABEL: Record<string, string> = {
  ownership_shift: "Declared owner changed",
  owner_inactive: "Main owners stopped changing it",
  bus_factor_drop: "Bus factor fell",
  new_hotspot: "Files became hotspots",
  contract_added: "Provided contract appeared",
  contract_broken: "Contract with consumers disappeared",
  decision_stale: "Governing decision went stale",
  dead_code_growth: "Dead-code findings grew",
  test_reach_drop: "Test reach fell",
};

const SEVERITIES: readonly Severity[] = ["critical", "high", "medium", "low"];

export interface CommitEventsLabels {
  title: string;
  description: string;
  /** The component column when an event names none. */
  repository: string;
  kind: (kind: string) => string;
  severity: (severity: Severity) => string;
}

const DEFAULT_LABELS: CommitEventsLabels = {
  title: "Flagged when this commit was indexed",
  description: "What the index run at this commit found had moved since the run before it.",
  repository: "Whole repository",
  kind: (k) => COMMIT_EVENT_KIND_LABEL[k] ?? k,
  severity: (s) => s,
};

function field(event: CloudEvent, key: string): string {
  const value = (event.data as Record<string, unknown>)[key];
  return typeof value === "string" ? value : "";
}

/**
 * The events detected at one commit, for the commit drawer. The host fetches
 * them (already narrowed to the important ones) and passes them in; nothing
 * renders when there are none, so a quiet commit carries no empty section.
 */
export function CommitEvents({
  events,
  labels,
}: {
  events: CloudEvent[];
  labels?: Partial<CommitEventsLabels> | undefined;
}) {
  if (events.length === 0) return null;
  const l = { ...DEFAULT_LABELS, ...labels };
  return (
    <section className="space-y-2 border-t border-[var(--color-border-default)] pt-4">
      <div className="space-y-0.5">
        <p className={MICRO}>{l.title}</p>
        <p className="text-xs text-[var(--color-text-tertiary)]">{l.description}</p>
      </div>
      <ul className="divide-y divide-[var(--color-border-default)] text-xs">
        {events.map((e) => {
          const kind = field(e, "repowise.kind");
          const severity = field(e, "repowise.severity");
          const component = field(e, "repowise.component");
          return (
            <li key={e.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 py-1.5">
              <span className="min-w-0 flex-1 font-medium text-[var(--color-text-primary)]">{l.kind(kind)}</span>
              <span className="font-mono text-[var(--color-text-secondary)]">{component || l.repository}</span>
              {(SEVERITIES as readonly string[]).includes(severity) && (
                <SeverityMark severity={severity as Severity} label={l.severity(severity as Severity)} />
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
