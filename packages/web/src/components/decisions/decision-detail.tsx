"use client";

import * as React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Badge } from "@/components/ui/badge";
import { patchDecision } from "@/lib/api/decisions";
import type { DecisionRecordResponse } from "@/lib/api/types";

const STATUS_VARIANT: Record<string, "default" | "fresh" | "stale" | "outdated" | "outline" | "accent"> = {
  active: "fresh",
  proposed: "accent",
  deprecated: "outdated",
  superseded: "outline",
};

interface DecisionDetailProps {
  decision: DecisionRecordResponse;
  repoId: string;
}

export function DecisionDetail({ decision, repoId }: DecisionDetailProps) {
  const [status, setStatus] = React.useState(decision.status);
  const [loading, setLoading] = React.useState(false);

  const handleStatusChange = async (newStatus: string) => {
    setLoading(true);
    try {
      await patchDecision(repoId, decision.id, { status: newStatus });
      setStatus(newStatus as typeof status);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="space-y-2">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-semibold text-[var(--color-text-primary)]">
            {decision.title}
          </h1>
          <Badge variant={STATUS_VARIANT[status] ?? "outline"}>{status}</Badge>
        </div>
        <div className="flex gap-4 text-sm text-[var(--color-text-tertiary)]">
          <span>Source: {decision.source}</span>
          <span>Confidence: {Math.round(decision.confidence * 100)}%</span>
          {decision.staleness_score > 0 && (
            <span className={decision.staleness_score > 0.5 ? "text-red-500" : ""}>
              Staleness: {decision.staleness_score.toFixed(2)}
            </span>
          )}
          <span>Created: {new Date(decision.created_at).toLocaleDateString()}</span>
        </div>
      </div>

      {/* Stale warning */}
      {decision.staleness_score > 0.5 && (
        <div className="rounded-md border border-amber-400/30 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:bg-amber-900/20 dark:text-amber-200">
          This decision may be stale — affected files have changed significantly since it was recorded.
        </div>
      )}

      {/* Content sections */}
      <div className="space-y-4">
        {decision.context && (
          <Section title="Context" text={decision.context} />
        )}
        {decision.decision && (
          <Section title="Decision" text={decision.decision} />
        )}
        {decision.rationale && (
          <Section title="Rationale" text={decision.rationale} />
        )}
        {decision.alternatives.length > 0 && (
          <ListSection title="Alternatives Rejected" items={decision.alternatives} />
        )}
        {decision.consequences.length > 0 && (
          <ListSection title="Consequences & Tradeoffs" items={decision.consequences} />
        )}
      </div>

      {/* Metadata */}
      <div className="grid gap-4 sm:grid-cols-2">
        {decision.affected_files.length > 0 && (
          <div className="rounded-lg border border-[var(--color-border-default)] p-4">
            <h3 className="mb-2 text-sm font-medium text-[var(--color-text-secondary)]">
              Affected Files ({decision.affected_files.length})
            </h3>
            <ul className="space-y-1 text-sm text-[var(--color-text-tertiary)]">
              {decision.affected_files.slice(0, 10).map((f) => (
                <li key={f} className="truncate font-mono text-xs" title={f}>{f}</li>
              ))}
              {decision.affected_files.length > 10 && (
                <li className="text-xs">...and {decision.affected_files.length - 10} more</li>
              )}
            </ul>
          </div>
        )}

        <div className="rounded-lg border border-[var(--color-border-default)] p-4">
          <h3 className="mb-2 text-sm font-medium text-[var(--color-text-secondary)]">Evidence</h3>
          <div className="space-y-1 text-sm text-[var(--color-text-tertiary)]">
            <div>Source: {decision.source}</div>
            {decision.evidence_file && (
              <div className="font-mono text-xs">
                {decision.evidence_file}
                {decision.evidence_line && `:${decision.evidence_line}`}
              </div>
            )}
            {decision.evidence_commits.length > 0 && (
              <div>Commits: {decision.evidence_commits.map((c) => c.slice(0, 8)).join(", ")}</div>
            )}
          </div>
        </div>
      </div>

      {/* Tags */}
      {decision.tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {decision.tags.map((tag) => (
            <span
              key={tag}
              className="inline-block rounded-full bg-[var(--color-bg-elevated)] px-2.5 py-0.5 text-xs font-medium text-[var(--color-text-secondary)]"
            >
              {tag}
            </span>
          ))}
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-2 border-t border-[var(--color-border-default)] pt-4">
        {status === "proposed" && (
          <>
            <button
              onClick={() => handleStatusChange("active")}
              disabled={loading}
              className="rounded-md bg-green-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-50"
            >
              Confirm
            </button>
            <button
              onClick={() => handleStatusChange("deprecated")}
              disabled={loading}
              className="rounded-md border border-[var(--color-border-default)] px-3 py-1.5 text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] disabled:opacity-50"
            >
              Dismiss
            </button>
          </>
        )}
        {status === "active" && (
          <button
            onClick={() => handleStatusChange("deprecated")}
            disabled={loading}
            className="rounded-md border border-red-300 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50 dark:border-red-800 dark:text-red-400 dark:hover:bg-red-900/20 disabled:opacity-50"
          >
            Deprecate
          </button>
        )}
      </div>
    </div>
  );
}

function Section({ title, text }: { title: string; text: string }) {
  return (
    <div>
      <h3 className="mb-1 text-sm font-medium text-[var(--color-text-secondary)]">{title}</h3>
      <div className="text-sm text-[var(--color-text-primary)] leading-relaxed prose prose-sm prose-invert max-w-none [&>p]:mb-2 [&>p:last-child]:mb-0">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
      </div>
    </div>
  );
}

function ListSection({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <h3 className="mb-1 text-sm font-medium text-[var(--color-text-secondary)]">{title}</h3>
      <ul className="list-disc space-y-0.5 pl-5 text-sm text-[var(--color-text-primary)]">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
