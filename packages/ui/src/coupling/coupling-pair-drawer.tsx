"use client";

import * as React from "react";
import { ExternalLink } from "lucide-react";
import { AdaptivePanel } from "../shared/adaptive-panel";
import { HealthBadge } from "../health/health-badge";
import { AiPromptButton } from "../health/ai-prompt-button";
import { formatDate, formatDateTime } from "../lib/format";
import {
  couplingClaim,
  dependencyKindPhrase,
  segmentOf,
  type CouplingSegment,
} from "./claim";
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
  /** How many files each end couples with, over the unfiltered edge set. */
  degreeByPath: ReadonlyMap<string, number>;
  /** Resolve a file's detail-page href; when absent, paths render unlinked. */
  linkForPath?: ((path: string) => string) | undefined;
  LinkComponent?: LinkLike | undefined;
  /** Open the AI decouple prompt for this pair. */
  onGeneratePrompt?: (edge: CouplingEdge) => void;
}

/**
 * The graph's verdict as a dot plus a word, and the reasoning behind it.
 *
 * Only `unexplained` takes a semantic color: it is the exception worth
 * marking. Explained and outside-the-graph are ordinary states, and coloring
 * every verdict would make the mark mean nothing.
 */
const VERDICT: Record<CouplingSegment, { word: string; dot: string; body: string }> = {
  unexplained: {
    word: "Unexplained",
    dot: "bg-[var(--color-caution)]",
    body: "Both files are in the dependency graph, and no import, type use, framework wiring, or read connects them. They move together for a reason the code does not state — a shared concept with no owner, a leaky abstraction, or copy-paste.",
  },
  explained: {
    word: "Explained",
    dot: "bg-[var(--color-text-tertiary)]",
    body: "A dependency already connects these files, so the co-change is accounted for. Worth a look only if the dependency itself is the wrong shape.",
  },
  outside: {
    word: "Outside the graph",
    dot: "bg-[var(--color-text-tertiary)]",
    body: "At least one side is a manifest, changelog, config, or doc, and no resolver can emit a dependency edge for it, so there was no edge to look for and its absence is not evidence of anything. The coupling is real, but it is release plumbing rather than a code-structure question.",
  },
};

const MICRO = "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

function pct(v: number | null | undefined): string | null {
  return typeof v === "number" ? `${Math.round(v * 100)}%` : null;
}

function basename(path: string): string {
  return path.split("/").pop() ?? path;
}

/**
 * Everything the page knows about one co-changing pair, in the surface a table
 * row could never hold: the claim spelled out, the graph's verdict and why it
 * does or does not matter, both directional shares side by side, each file's
 * module / health / size / degree, and a route onward to either file page.
 *
 * Shaped like the file-health drawer: a lede sentence, a hairline definition
 * list, then sections under mono micro-headings. A border is reserved for an
 * object you can open or act on, so facts are separated by hairlines and space
 * rather than each taking a card.
 *
 * `AdaptivePanel` makes it a non-modal right panel on desktop, leaving the lit
 * arc visible behind it, and a bottom sheet on mobile.
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
  const verdict = segment ? VERDICT[segment] : null;
  const kindPhrase = edge ? dependencyKindPhrase(edge) : null;

  const moduleA = edge ? (nodeByPath.get(edge.source)?.module ?? null) : null;
  const moduleB = edge ? (nodeByPath.get(edge.target)?.module ?? null) : null;

  const facts: { label: string; value: React.ReactNode }[] = edge
    ? [
        {
          label: "Shared commits",
          value: (
            <span className="text-lg font-semibold tabular-nums text-[var(--color-text-primary)]">
              {edge.support || (
                <span className="text-xs font-normal text-[var(--color-text-tertiary)]">
                  not recorded
                </span>
              )}
            </span>
          ),
        },
        {
          label: "Strength",
          value: (
            <span className="text-xs tabular-nums text-[var(--color-text-primary)]">
              {edge.strength}{" "}
              <span className="text-[var(--color-text-tertiary)]">recency-weighted</span>
            </span>
          ),
        },
        {
          label: "Last together",
          value: (
            <span
              className="text-xs tabular-nums text-[var(--color-text-primary)]"
              title={edge.last_co_change ? formatDateTime(edge.last_co_change) : undefined}
            >
              {edge.last_co_change ? formatDate(edge.last_co_change) : "unknown"}
            </span>
          ),
        },
        {
          label: "Modules",
          value: (
            <span className="block truncate text-xs text-[var(--color-text-primary)]">
              {moduleA && moduleA === moduleB ? (
                <>
                  within <span className="font-mono">{moduleA}</span>
                </>
              ) : moduleA || moduleB ? (
                <span className="font-mono">
                  {moduleA ?? "—"} ↔ {moduleB ?? "—"}
                </span>
              ) : (
                <span className="text-[var(--color-text-tertiary)]">none</span>
              )}
            </span>
          ),
        },
      ]
    : [];

  return (
    <AdaptivePanel
      open={edge !== null}
      onOpenChange={(o) => !o && onClose()}
      modal={false}
      widthClassName="md:max-w-[640px]"
      eyebrow="Change coupling"
      title={
        edge ? (
          <span className="break-all font-mono">
            {basename(edge.source)} ↔ {basename(edge.target)}
          </span>
        ) : (
          ""
        )
      }
    >
      {edge && (
        <div className="flex flex-col gap-6 px-4 py-4">
          {/* Lede: the verdict names the kind of finding, the claim is the
              sentence that makes it mean something. */}
          <div className="flex flex-col gap-2">
            {verdict && (
              <p className="flex items-center gap-1.5">
                <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${verdict.dot}`} aria-hidden />
                <span className={MICRO}>{verdict.word}</span>
                {/* The kind rides in the verdict line, where the reader is
                    already asking what connects them. */}
                {kindPhrase && (
                  <span className={MICRO}>· {kindPhrase.toLowerCase()}</span>
                )}
              </p>
            )}
            <p className="text-[15px] leading-relaxed text-[var(--color-text-primary)] [text-wrap:pretty]">
              {couplingClaim(edge, basename)}
            </p>
            {verdict && (
              <p className="text-xs leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
                {verdict.body}
              </p>
            )}
          </div>

          {/* A hairline ribbon, not tiles: four different kinds of fact, and
              equal-weight boxes would claim they are one. */}
          <dl className="grid grid-cols-2 border-y border-[var(--color-border-default)]">
            {facts.map((f, i) => (
              <div
                key={f.label}
                className={[
                  "min-w-0 px-3 py-2.5 border-[var(--color-border-default)]",
                  i % 2 === 1 ? "border-l" : "",
                  i >= 2 ? "border-t" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
              >
                <dt className={MICRO}>{f.label}</dt>
                <dd className="mt-1">{f.value}</dd>
              </div>
            ))}
          </dl>

          {/* The asymmetry is the finding, so each direction gets its own bar
              rather than collapsing into a single "up to N%". */}
          <section className="flex flex-col gap-2">
            <h3 className={MICRO}>Share of each file&apos;s own commits</h3>
            <div className="flex flex-col gap-3">
              {(
                [
                  { path: edge.source, conf: edge.confidence_ab, other: edge.target },
                  { path: edge.target, conf: edge.confidence_ba, other: edge.source },
                ] as const
              ).map(({ path, conf, other }) => {
                const share = pct(conf);
                return (
                  <div key={path} className="min-w-0">
                    <div className="flex items-baseline justify-between gap-2">
                      <span
                        className="truncate font-mono text-xs text-[var(--color-text-secondary)]"
                        title={path}
                      >
                        {basename(path)}
                      </span>
                      <span className="shrink-0 text-xs font-semibold tabular-nums text-[var(--color-text-primary)]">
                        {share ?? "unknown"}
                      </span>
                    </div>
                    <div
                      className="mt-1.5 h-1 overflow-hidden rounded-full bg-[var(--color-bg-inset)]"
                      role="presentation"
                    >
                      <div
                        className="h-full rounded-full bg-[var(--color-accent-primary)]"
                        style={{ width: share ?? "0%" }}
                      />
                    </div>
                    <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                      {share
                        ? `${share} of its commits also touched ${basename(other)}`
                        : "This file's commit total is not on this index"}
                    </p>
                  </div>
                );
              })}
            </div>
          </section>

          {/* The two files, as a hairline list. Each row is an object you can
              open, so the row itself carries the one action. */}
          <section className="flex flex-col gap-2">
            <h3 className={MICRO}>The two files</h3>
            <ul className="border-y border-[var(--color-border-default)]">
              {[edge.source, edge.target].map((path) => {
                const node = nodeByPath.get(path);
                const degree = degreeByPath.get(path) ?? 0;
                return (
                  <li
                    key={path}
                    className="min-w-0 border-t border-[var(--color-border-default)] px-3 py-2.5 first:border-t-0"
                  >
                    <div className="flex items-start justify-between gap-2">
                      {/* Wrapped, not truncated: the one place the exact file
                          is legible. */}
                      <p className="min-w-0 break-all font-mono text-xs text-[var(--color-text-secondary)]">
                        {path.slice(0, path.length - basename(path).length)}
                        <span className="text-[var(--color-text-primary)]">{basename(path)}</span>
                      </p>
                      <HealthBadge score={node?.score ?? null} />
                    </div>
                    <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--color-text-tertiary)]">
                      {node?.module && <span className="font-mono">{node.module}</span>}
                      {node?.nloc ? <span className="tabular-nums">{node.nloc} lines</span> : null}
                      <span className="tabular-nums">
                        couples with {degree} {degree === 1 ? "file" : "files"}
                      </span>
                      {linkForPath && (
                        <Anchor
                          href={linkForPath(path)}
                          className="inline-flex items-center gap-1 font-medium text-[var(--color-accent-primary)] hover:underline"
                        >
                          <ExternalLink className="h-3 w-3" />
                          Open file page
                        </Anchor>
                      )}
                    </p>
                  </li>
                );
              })}
            </ul>
          </section>

          {/* The one action, named and explained. */}
          {onGeneratePrompt && (
            <section className="flex flex-col gap-2">
              <h3 className={MICRO}>Hand this to an agent</h3>
              <p className="text-xs leading-relaxed text-[var(--color-text-secondary)]">
                Builds a prompt carrying the evidence above, so your agent can judge
                whether the coupling is accidental or legitimate and propose the
                smallest decoupling that holds.
              </p>
              <AiPromptButton
                label="AI decouple prompt"
                onClick={() => onGeneratePrompt(edge)}
                className="w-fit"
              />
            </section>
          )}
        </div>
      )}
    </AdaptivePanel>
  );
}
