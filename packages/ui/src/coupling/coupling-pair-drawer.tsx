"use client";

import * as React from "react";
import { ArrowRight, GitCommitHorizontal } from "lucide-react";
import { AdaptivePanel } from "../shared/adaptive-panel";
import { StatGrid, StatTile } from "../shared/stat-grid";
import { HealthBadge } from "../health/health-badge";
import { AiPromptButton } from "../health/ai-prompt-button";
import { cn } from "../lib/cn";
import { formatDate, formatDateTime } from "../lib/format";
import { couplingClaim, peakConfidence, segmentOf } from "./claim";
import type { CouplingEdge, CouplingNode } from "@repowise-dev/types/coupling";

/** Injected link component (e.g. Next's Link); defaults to a plain anchor. */
type LinkLike = React.ElementType<{
  href: string;
  className?: string;
  children: React.ReactNode;
}>;

export interface CouplingPairDrawerProps {
  /** The pair to describe; `null` closes the panel. */
  edge: CouplingEdge | null;
  onClose: () => void;
  /** Node facts by path, for module / health / size. */
  nodeByPath: ReadonlyMap<string, CouplingNode>;
  /** How many files each end couples with, for "one of N couplings". */
  degreeByPath: ReadonlyMap<string, number>;
  /** Resolve a file's detail-page href; when absent, paths render unlinked. */
  linkForPath?: ((path: string) => string) | undefined;
  LinkComponent?: LinkLike | undefined;
  /** Open the AI decouple prompt for this pair. */
  onGeneratePrompt?: (edge: CouplingEdge) => void;
}

/** The verdict, as a sentence plus the reason it is or is not a finding. */
const VERDICT: Record<string, { title: string; body: string; tone: string }> = {
  unexplained: {
    title: "Nothing in the dependency graph connects these files",
    body: "Both are parsed into the graph, and there is no import, type use, framework wiring, or read between them. They move together for a reason the code does not state — a shared concept with no owner, a leaky abstraction, or copy-paste.",
    tone: "border-[var(--color-caution)]/40 bg-[var(--color-caution)]/10",
  },
  explained: {
    title: "A dependency already connects these files",
    body: "The graph accounts for the co-change, so this is not hidden coupling. Worth a look only if the dependency itself is the wrong shape.",
    tone: "border-[var(--color-border-default)] bg-[var(--color-bg-inset)]",
  },
  outside: {
    title: "At least one side is outside the dependency graph",
    body: "A file the parser never ingested — a lockfile, changelog, config, or doc — has no edge to find, so its absence is not evidence of anything. The coupling is real, but it is release plumbing rather than a code-structure question.",
    tone: "border-[var(--color-border-default)] bg-[var(--color-bg-inset)]",
  },
};

function pct(v: number | null | undefined): string {
  return typeof v === "number" ? `${Math.round(v * 100)}%` : "—";
}

/**
 * Everything the page knows about one co-changing pair, in the surface the
 * table row could never hold: the claim spelled out, the graph's verdict and
 * why it matters, both directional shares side by side, each file's module /
 * health / size / degree, and a route onward to either file's page.
 *
 * A right-side panel on desktop (non-modal, so the lit arc stays visible
 * behind it) and a swipe-dismissable bottom sheet on mobile, via the shared
 * `AdaptivePanel` — the same surface every other entity drawer uses.
 */
export function CouplingPairDrawer({
  edge,
  onClose,
  nodeByPath,
  degreeByPath,
  linkForPath,
  LinkComponent,
  onGeneratePrompt,
}: CouplingPairDrawerProps) {
  const Anchor: LinkLike = LinkComponent ?? "a";
  const segment = edge ? segmentOf(edge) : null;
  const verdict = segment ? VERDICT[segment] : undefined;

  const fileBlock = (path: string, side: "A" | "B") => {
    const node = nodeByPath.get(path);
    const degree = degreeByPath.get(path) ?? 0;
    const name = path.split("/").pop() ?? path;
    const dir = path.slice(0, path.length - name.length).replace(/\/$/, "");
    return (
      <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
              File {side}
            </p>
            {/* The full path, wrapped rather than truncated: this is the one
                place a reader can see exactly which file is meant. */}
            <p className="mt-0.5 break-all font-mono text-xs text-[var(--color-text-secondary)]">
              {dir ? `${dir}/` : ""}
              <span className="text-[var(--color-text-primary)]">{name}</span>
            </p>
          </div>
          <HealthBadge score={node?.score ?? null} size="sm" />
        </div>
        <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--color-text-tertiary)]">
          {node?.module && (
            <div className="flex gap-1">
              <dt>Module</dt>
              <dd className="text-[var(--color-text-secondary)]">{node.module}</dd>
            </div>
          )}
          {node?.nloc ? (
            <div className="flex gap-1">
              <dt>Lines</dt>
              <dd className="tabular-nums text-[var(--color-text-secondary)]">{node.nloc}</dd>
            </div>
          ) : null}
          <div className="flex gap-1">
            <dt>Couples with</dt>
            <dd className="tabular-nums text-[var(--color-text-secondary)]">
              {degree} {degree === 1 ? "file" : "files"}
            </dd>
          </div>
        </dl>
        {linkForPath && (
          <Anchor
            href={linkForPath(path)}
            className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-[var(--color-accent-primary)] hover:underline"
          >
            Open file page
            <ArrowRight className="h-3 w-3" />
          </Anchor>
        )}
      </div>
    );
  };

  return (
    <AdaptivePanel
      open={edge !== null}
      onOpenChange={(o) => !o && onClose()}
      modal={false}
      widthClassName="md:max-w-[560px]"
      eyebrow="Change coupling"
      title={
        edge ? (
          <span className="break-all font-mono text-sm">
            {edge.source.split("/").pop()} ↔ {edge.target.split("/").pop()}
          </span>
        ) : (
          ""
        )
      }
    >
      {edge && (
        <div className="space-y-4 p-4">
          {/* The claim, at reading size rather than as table small-print. */}
          <p className="text-sm leading-relaxed text-[var(--color-text-primary)]">
            {couplingClaim(edge, (p) => p.split("/").pop() ?? p)}
          </p>

          {verdict && (
            <div className={cn("rounded-lg border p-3", verdict.tone)}>
              <p className="text-xs font-semibold text-[var(--color-text-primary)]">
                {verdict.title}
              </p>
              <p className="mt-1 text-xs leading-relaxed text-[var(--color-text-secondary)]">
                {verdict.body}
              </p>
            </div>
          )}

          <StatGrid columns={2}>
            <StatTile
              label="Shared commits"
              value={edge.support || "—"}
              hint="Commits that touched both files"
            />
            <StatTile
              label="Strongest direction"
              value={pct(peakConfidence(edge))}
              hint="Of one file's own commits"
            />
            <StatTile
              label="Strength"
              value={edge.strength}
              hint="Recency-weighted, not a percentage"
            />
            <StatTile
              label="Last together"
              value={edge.last_co_change ? formatDate(edge.last_co_change) : "—"}
              {...(edge.last_co_change
                ? { hint: formatDateTime(edge.last_co_change) }
                : {})}
            />
          </StatGrid>

          {/* Both directions side by side: the asymmetry is the whole point,
              and a single "up to N%" hides which side it belongs to. */}
          <div className="rounded-lg border border-[var(--color-border-default)] p-3">
            <p className="flex items-center gap-1.5 text-xs font-medium text-[var(--color-text-secondary)]">
              <GitCommitHorizontal className="h-3.5 w-3.5" />
              How much of each file's own history the pair accounts for
            </p>
            <div className="mt-2 space-y-2">
              {(
                [
                  { path: edge.source, conf: edge.confidence_ab, other: edge.target },
                  { path: edge.target, conf: edge.confidence_ba, other: edge.source },
                ] as const
              ).map(({ path, conf, other }) => (
                <div key={path}>
                  <div className="flex items-baseline justify-between gap-2 text-xs">
                    <span className="truncate font-mono text-[var(--color-text-secondary)]" title={path}>
                      {path.split("/").pop()}
                    </span>
                    <span className="shrink-0 tabular-nums text-[var(--color-text-primary)]">
                      {pct(conf)}
                    </span>
                  </div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
                    <div
                      className="h-full rounded-full bg-[var(--color-accent-primary)]"
                      style={{ width: `${typeof conf === "number" ? Math.round(conf * 100) : 0}%` }}
                    />
                  </div>
                  <p className="mt-0.5 text-[11px] text-[var(--color-text-tertiary)]">
                    {typeof conf === "number"
                      ? `${pct(conf)} of its commits also touched ${other.split("/").pop()}`
                      : "This file's commit total is unknown on this index"}
                  </p>
                </div>
              ))}
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            {fileBlock(edge.source, "A")}
            {fileBlock(edge.target, "B")}
          </div>

          {onGeneratePrompt && (
            <div className="rounded-lg border border-[var(--color-border-default)] p-3">
              <p className="text-xs text-[var(--color-text-secondary)]">
                Hand this pair to your AI agent with the evidence above already in the
                prompt — it diagnoses whether the coupling is accidental or legitimate,
                and proposes the smallest decoupling that holds.
              </p>
              <AiPromptButton
                label="AI decouple prompt"
                onClick={() => onGeneratePrompt(edge)}
                className="mt-2 w-full justify-center"
              />
            </div>
          )}
        </div>
      )}
    </AdaptivePanel>
  );
}
