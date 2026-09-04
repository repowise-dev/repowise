import { useEffect, useMemo, useState } from "react";
import { FileText, Landmark, Lightbulb } from "lucide-react";
import { Badge } from "@repowise-dev/ui/ui";
import { EmptyState } from "@repowise-dev/ui/shared";
import {
  DecisionStatusMark,
  decisionStatusColor,
} from "@repowise-dev/ui/decisions/decision-status-mark";
import { WikiMarkdown } from "@repowise-dev/ui/wiki/wiki-markdown";
import { formatRelativeTime, stripMarkdown } from "@repowise-dev/ui/lib/format";
import {
  DECISION_STATUSES,
  DECISION_STATUS_LABELS,
} from "@repowise-dev/types/decisions";
import type { DecisionRecordResponse } from "@repowise-dev/api-client/types";
import type { ViewProps } from "../../runtime/mount";

type Status = DecisionRecordResponse["status"];

// The shared ladder, not a local copy. The copy that was here ordered
// deprecated ahead of superseded while the list endpoint ordered them the
// other way, so the same rows ranked differently depending on the surface.
const STATUS_ORDER: readonly Status[] = DECISION_STATUSES;

function statusLabel(status: Status): string {
  // `status` is a strict union here but an unconstrained string on the wire,
  // so fall back to the raw value: a chip carrying a count needs a word beside
  // it, and a blank label is worse than an unfamiliar one.
  return DECISION_STATUS_LABELS[status] ?? status;
}

/** Record section labels. Uppercase micro-labels are mono: it separates a
 *  machine-shaped label from the prose under it without spending a colour. */
const SECTION_LABEL =
  "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

function recencyKey(d: DecisionRecordResponse): number {
  const raw = d.updated_at || d.created_at;
  const t = raw ? new Date(raw).getTime() : 0;
  return Number.isNaN(t) ? 0 : t;
}

/** Loading placeholder that matches the master-detail layout so the panel does
 *  not reflow when the decisions land. */
function DecisionsSkeleton() {
  return (
    <div className="flex h-full flex-col" aria-hidden>
      <header className="border-b border-[var(--color-border-default)] px-6 py-4">
        <div className="h-6 w-32 animate-pulse rounded bg-[var(--color-bg-inset)]" />
        <div className="mt-3 flex gap-2">
          {[56, 64, 72, 60].map((w, i) => (
            <div
              key={i}
              className="h-7 animate-pulse rounded-full bg-[var(--color-bg-inset)]"
              style={{ width: w }}
            />
          ))}
        </div>
      </header>
      <div className="flex min-h-0 flex-1">
        {/* Borderless rows, matching the loaded list: a skeleton drawing seven
            boxes for content that lands as seven plain rows reflows the panel
            at exactly the moment it is meant to steady it. */}
        <div className="w-80 shrink-0 space-y-1 border-r border-[var(--color-border-default)] p-3">
          {Array.from({ length: 7 }).map((_, i) => (
            <div key={i} className="flex items-start gap-3 px-3 py-2.5">
              <div className="mt-1 h-2.5 w-2.5 shrink-0 animate-pulse rounded-full bg-[var(--color-bg-inset)]" />
              <div className="min-w-0 flex-1 space-y-1.5">
                <div className="h-4 w-3/4 animate-pulse rounded bg-[var(--color-bg-inset)]" />
                <div className="h-3 w-1/2 animate-pulse rounded bg-[var(--color-bg-inset)]" />
              </div>
            </div>
          ))}
        </div>
        <div className="min-w-0 flex-1 space-y-4 p-6">
          <div className="h-6 w-2/3 animate-pulse rounded bg-[var(--color-bg-inset)]" />
          <div className="h-4 w-1/3 animate-pulse rounded bg-[var(--color-bg-inset)]" />
          <div className="space-y-2 pt-2">
            {Array.from({ length: 7 }).map((_, i) => (
              <div
                key={i}
                className="h-3 animate-pulse rounded bg-[var(--color-bg-inset)]"
                style={{ width: `${92 - i * 7}%` }}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export function App({ host, refreshToken }: ViewProps<"decisions">) {
  const [decisions, setDecisions] = useState<DecisionRecordResponse[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Status | "all">("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    host.api
      .decisionsList()
      .then((list) => {
        if (cancelled) return;
        const sorted = [...list].sort((a, b) => recencyKey(b) - recencyKey(a));
        setDecisions(sorted);
        setSelectedId((prev) =>
          prev && sorted.some((d) => d.id === prev) ? prev : (sorted[0]?.id ?? null),
        );
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Could not load decisions.");
      });
    return () => {
      cancelled = true;
    };
  }, [host, refreshToken]);

  const counts = useMemo(() => {
    const c = new Map<Status, number>();
    for (const d of decisions ?? []) c.set(d.status, (c.get(d.status) ?? 0) + 1);
    return c;
  }, [decisions]);

  const visible = useMemo(
    () => (decisions ?? []).filter((d) => filter === "all" || d.status === filter),
    [decisions, filter],
  );

  const selected = useMemo(
    () => visible.find((d) => d.id === selectedId) ?? visible[0] ?? null,
    [visible, selectedId],
  );

  if (error) {
    return (
      <div className="p-6">
        <EmptyState
          icon={<Lightbulb className="h-8 w-8" />}
          title="Could not load decisions"
          description={error}
        />
      </div>
    );
  }

  if (decisions === null) {
    return <DecisionsSkeleton />;
  }

  if (decisions.length === 0) {
    return (
      <div className="p-6">
        <EmptyState
          icon={<Lightbulb className="h-8 w-8" />}
          title="No decisions recorded"
          description="No architectural decisions have been detected in this repository yet."
        />
      </div>
    );
  }

  const chips: Array<{ key: Status | "all"; label: string; count: number }> = [
    { key: "all", label: "All", count: decisions.length },
    ...STATUS_ORDER.filter((s) => (counts.get(s) ?? 0) > 0).map((s) => ({
      key: s,
      label: statusLabel(s),
      count: counts.get(s) ?? 0,
    })),
  ];

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-[var(--color-border-default)] px-6 py-4">
        <h1 className="flex items-center gap-2 text-[22px] font-semibold text-[var(--color-text-primary)]">
          <Landmark className="h-5 w-5 text-[var(--color-text-secondary)]" />
          Decisions
        </h1>
        <div className="mt-3 flex flex-wrap gap-2">
          {chips.map((chip) => {
            const on = filter === chip.key;
            return (
              <button
                key={chip.key}
                type="button"
                onClick={() => setFilter(chip.key)}
                className={
                  "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors " +
                  (on
                    ? "border-[var(--color-accent-primary)] bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
                    : "border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:border-[var(--color-border-hover)]")
                }
              >
                {chip.label}
                <span className="text-[var(--color-text-tertiary)]">{chip.count}</span>
              </button>
            );
          })}
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <ul className="w-80 shrink-0 overflow-y-auto border-r border-[var(--color-border-default)] p-3">
          {visible.map((d) => {
            const on = selected?.id === d.id;
            return (
              <li key={d.id}>
                <button
                  type="button"
                  onClick={() => setSelectedId(d.id)}
                  className={
                    "flex w-full items-start gap-3 rounded-lg px-3 py-2.5 text-left transition-colors " +
                    (on
                      ? "bg-[var(--color-bg-elevated)]"
                      : "hover:bg-[var(--color-bg-elevated)]")
                  }
                >
                  <span
                    className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full"
                    style={{ backgroundColor: decisionStatusColor(d.status) }}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block text-[15px] font-medium leading-snug text-[var(--color-text-primary)]">
                      {stripMarkdown(d.title)}
                    </span>
                    <span className="mt-0.5 block text-xs text-[var(--color-text-tertiary)]">
                      {formatRelativeTime(d.updated_at || d.created_at)}
                      {d.source ? ` · ${d.source.replace(/_/g, " ")}` : ""}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>

        <div className="min-w-0 flex-1 overflow-y-auto p-6">
          {selected ? <Detail host={host} decision={selected} /> : null}
        </div>
      </div>
    </div>
  );
}

function Detail({
  host,
  decision,
}: {
  host: ViewProps<"decisions">["host"];
  decision: DecisionRecordResponse;
}) {
  const sections: Array<{ heading: string; body: string }> = [
    { heading: "Context", body: decision.context },
    { heading: "Decision", body: decision.decision },
    { heading: "Rationale", body: decision.rationale },
  ].filter((s) => s.body && s.body.trim().length > 0);

  return (
    <article className="mx-auto max-w-2xl space-y-5">
      <div className="space-y-3">
        <DecisionStatusMark status={decision.status} className="capitalize" />
        <h2 className="text-[22px] font-semibold leading-snug text-[var(--color-text-primary)]">
          {stripMarkdown(decision.title)}
        </h2>
        {decision.tags.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {decision.tags.map((tag) => (
              <Badge key={tag} variant="outline">
                {tag}
              </Badge>
            ))}
          </div>
        )}
      </div>

      {sections.map((s) => (
        <section key={s.heading} className="space-y-1.5">
          <h3 className={SECTION_LABEL}>
            {s.heading}
          </h3>
          {/* No `prose` wrapper: WikiMarkdown themes every element it emits
              through our tokens, so the plugin only imposes a second font
              scale and set of margins over one that is already set. */}
          <div className="max-w-none text-[var(--color-text-secondary)]">
            <WikiMarkdown content={s.body} />
          </div>
        </section>
      ))}

      {decision.alternatives.length > 0 && (
        <ListSection heading="Alternatives considered" items={decision.alternatives} />
      )}
      {decision.consequences.length > 0 && (
        <ListSection heading="Consequences" items={decision.consequences} />
      )}

      {decision.affected_files.length > 0 && (
        <section className="space-y-1.5">
          <h3 className={SECTION_LABEL}>
            Affected files
          </h3>
          <ul className="space-y-1">
            {decision.affected_files.map((path) => (
              <li key={path}>
                <FileRef host={host} path={path} />
              </li>
            ))}
          </ul>
        </section>
      )}

      {decision.evidence_file && (
        <section className="space-y-1.5 border-t border-[var(--color-border-default)] pt-4">
          <h3 className={SECTION_LABEL}>Evidence</h3>
          <FileRef
            host={host}
            path={decision.evidence_file}
            line={decision.evidence_line ?? undefined}
          />
        </section>
      )}
    </article>
  );
}

function ListSection({ heading, items }: { heading: string; items: string[] }) {
  return (
    <section className="space-y-1.5">
      <h3 className={SECTION_LABEL}>
        {heading}
      </h3>
      <ul className="list-disc space-y-1 pl-5 text-[15px] text-[var(--color-text-secondary)]">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </section>
  );
}

function FileRef({
  host,
  path,
  line,
}: {
  host: ViewProps<"decisions">["host"];
  path: string;
  line?: number;
}) {
  return (
    <button
      type="button"
      onClick={() => host.openFile(path, line)}
      className="inline-flex items-center gap-1.5 text-[15px] text-[var(--color-accent-primary)] hover:underline"
    >
      <FileText className="h-3.5 w-3.5 shrink-0" />
      <span className="break-all text-left">
        {path}
        {line != null ? `:${line}` : ""}
      </span>
    </button>
  );
}
