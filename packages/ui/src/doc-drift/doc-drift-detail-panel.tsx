"use client";

/**
 * One drifted assertion, in full.
 *
 * The table can only carry the document, the target and a tier. Everything
 * that makes a finding *actionable* is the detail the resolver already
 * recorded and the row has no room for: the line as the document actually
 * wrote it, where in the document it sits, and what the resolver checked to
 * conclude the claim no longer holds. All of it arrives on the wire with every
 * finding; before this panel existed the tab fetched it and dropped it.
 *
 * Built on {@link AdaptivePanel}, the same primitive the health and coupling
 * drawers use, so this reads as the product's one detail surface rather than a
 * second one invented for drift.
 */

import { ExternalLink } from "lucide-react";
import {
  docDriftConfidenceTier,
  docDriftKindLabel,
  type DocDriftFinding,
} from "@repowise-dev/types/doc-drift";

import { AdaptivePanel } from "../shared/adaptive-panel";
import { AiPromptButton } from "../health/ai-prompt-button";

/** The evidence line carrying the enclosing heading trail. */
const TRAIL_PREFIX = "under: ";

const TIER_LABEL: Record<string, string> = {
  high: "Near-certain",
  medium: "Medium",
  low: "Low",
};

export interface DocDriftDetailPanelProps {
  finding: DocDriftFinding | null;
  onOpenChange: (open: boolean) => void;
  /** Link to the document itself. */
  documentHref: (path: string, line?: number) => string;
  /** Hand this one finding to an agent. */
  onPrompt: (finding: DocDriftFinding) => void;
  /** Client-side routing for the document link, when the host has a router. */
  navigate?: ((href: string) => void) | undefined;
  /** What a finding does and does not claim, from the engine. */
  basis: string;
}

export function DocDriftDetailPanel({
  finding,
  onOpenChange,
  documentHref,
  onPrompt,
  navigate,
  basis,
}: DocDriftDetailPanelProps) {
  if (!finding) {
    // AdaptivePanel needs a title even while closed; rendering nothing until
    // there is a finding keeps the closed state from inventing one.
    return null;
  }

  const tier = docDriftConfidenceTier(finding.confidence);
  const trail = finding.evidence.find((line) => line.startsWith(TRAIL_PREFIX));
  const trace = finding.evidence.filter((line) => !line.startsWith(TRAIL_PREFIX));

  return (
    <AdaptivePanel
      open
      onOpenChange={onOpenChange}
      eyebrow="Drifted assertion"
      title={`${finding.file_path}:${finding.line_number}`}
      widthClassName="md:max-w-[620px]"
    >
      <div className="flex flex-col gap-6 px-4 py-4">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          <span className="text-[var(--color-text-secondary)]">
            {docDriftKindLabel(finding.kind)}
          </span>
          <span className="text-[var(--color-text-tertiary)]">·</span>
          <span
            className={
              tier === "high"
                ? "text-[var(--color-warning)]"
                : "text-[var(--color-text-secondary)]"
            }
          >
            {TIER_LABEL[tier]}{" "}
            <span className="font-mono text-[var(--color-text-tertiary)]">
              {finding.confidence.toFixed(2)}
            </span>
          </span>
        </div>

        <Section title="What this document claims">
          <p className="font-mono text-xs break-all text-[var(--color-text-primary)]">
            {finding.target}
          </p>
          <p className="mt-2 text-xs leading-relaxed text-[var(--color-text-secondary)]">
            {finding.reason}
          </p>
        </Section>

        {finding.context && (
          <Section title="The line as written">
            <pre className="overflow-x-auto rounded-md bg-[var(--color-bg-inset)] p-3 text-[11px] leading-relaxed text-[var(--color-text-primary)]">
              <code className="font-mono whitespace-pre-wrap break-all">
                {finding.context}
              </code>
            </pre>
            {finding.raw && finding.raw !== finding.context && (
              <p className="mt-2 font-mono text-[11px] break-all text-[var(--color-text-tertiary)]">
                Reference: {finding.raw}
              </p>
            )}
          </Section>
        )}

        {trail && (
          <Section title="Where in the document">
            <p className="text-xs text-[var(--color-text-secondary)]">
              {trail.slice(TRAIL_PREFIX.length)}
            </p>
          </Section>
        )}

        {trace.length > 0 && (
          <Section title="What was checked">
            <ul className="flex flex-col gap-1">
              {trace.map((line) => (
                <li
                  key={line}
                  className="font-mono text-[11px] break-all text-[var(--color-text-tertiary)]"
                >
                  {line}
                </li>
              ))}
            </ul>
          </Section>
        )}

        {/* Beside the actions, not in a footnote: this is where someone is
            about to edit a file on the strength of the finding. */}
        <p className="text-xs leading-relaxed text-[var(--color-text-tertiary)] [text-wrap:pretty]">
          {basis}
        </p>

        <div className="flex flex-wrap items-center gap-2">
          <AiPromptButton
            onClick={() => onPrompt(finding)}
            label="Fix with an agent"
          />
          <a
            href={documentHref(finding.file_path, finding.line_number)}
            // Stays a real anchor so it can be opened in a new tab; the router
            // only takes over a plain left click.
            onClick={(event) => {
              if (!navigate || event.metaKey || event.ctrlKey || event.shiftKey) return;
              event.preventDefault();
              navigate(documentHref(finding.file_path, finding.line_number));
            }}
            className="inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border-default)] px-2.5 py-1 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-border-hover)] hover:text-[var(--color-text-primary)]"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            Open document
          </a>
        </div>
      </div>
    </AdaptivePanel>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-1.5">
      <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        {title}
      </h3>
      {children}
    </section>
  );
}
