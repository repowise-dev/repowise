import { useEffect, useMemo, useState } from "react";
import { FileText, Landmark, Lightbulb } from "lucide-react";
import { Badge, Card, CardContent } from "@repowise-dev/ui/ui";
import { EmptyState } from "@repowise-dev/ui/shared";
import { WikiMarkdown } from "@repowise-dev/ui/wiki/wiki-markdown";
import { formatRelativeTime, stripMarkdown } from "@repowise-dev/ui/lib/format";
import type { DecisionRecordResponse } from "@repowise-dev/api-client/types";
import type { ViewProps } from "../../runtime/mount";

type Status = DecisionRecordResponse["status"];

const STATUS_ORDER: readonly Status[] = ["active", "proposed", "deprecated", "superseded"];

/** Left dot / accent per status, matching the shared decisions widget. */
const STATUS_DOT: Record<Status, string> = {
  active: "var(--color-success)",
  proposed: "var(--color-info)",
  deprecated: "var(--color-error)",
  superseded: "var(--color-text-tertiary)",
};

function statusLabel(status: Status): string {
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function StatusBadge({ status }: { status: Status }) {
  const color = STATUS_DOT[status];
  return (
    <span
      className="inline-flex items-center rounded border px-2 py-0.5 text-[11px] font-medium"
      style={{ color, borderColor: `color-mix(in srgb, ${color} 35%, transparent)` }}
    >
      {statusLabel(status)}
    </span>
  );
}

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
        <div className="w-80 shrink-0 space-y-2 border-r border-[var(--color-border-default)] p-3">
          {Array.from({ length: 7 }).map((_, i) => (
            <div
              key={i}
              className="space-y-2 rounded-lg border border-[var(--color-border-default)] p-3"
            >
              <div className="h-4 w-3/4 animate-pulse rounded bg-[var(--color-bg-inset)]" />
              <div className="h-3 w-1/2 animate-pulse rounded bg-[var(--color-bg-inset)]" />
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
        <h1 className="flex items-center gap-2 text-lg font-semibold text-[var(--color-text-primary)]">
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
                    style={{ backgroundColor: STATUS_DOT[d.status] }}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-[var(--color-text-primary)]">
                      {stripMarkdown(d.title)}
                    </span>
                    <span className="mt-0.5 block text-[11px] text-[var(--color-text-tertiary)]">
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
        <StatusBadge status={decision.status} />
        <h2 className="text-xl font-semibold leading-snug text-[var(--color-text-primary)]">
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
          <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-tertiary)]">
            {s.heading}
          </h3>
          <div className="prose-sm max-w-none text-sm text-[var(--color-text-secondary)]">
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
          <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-tertiary)]">
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
        <Card>
          <CardContent className="py-3">
            <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-tertiary)]">
              Evidence
            </h3>
            <FileRef
              host={host}
              path={decision.evidence_file}
              line={decision.evidence_line ?? undefined}
            />
          </CardContent>
        </Card>
      )}
    </article>
  );
}

function ListSection({ heading, items }: { heading: string; items: string[] }) {
  return (
    <section className="space-y-1.5">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-tertiary)]">
        {heading}
      </h3>
      <ul className="list-disc space-y-1 pl-5 text-sm text-[var(--color-text-secondary)]">
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
      className="inline-flex items-center gap-1.5 text-sm text-[var(--color-accent-primary)] hover:underline"
    >
      <FileText className="h-3.5 w-3.5 shrink-0" />
      <span className="break-all text-left">
        {path}
        {line != null ? `:${line}` : ""}
      </span>
    </button>
  );
}
