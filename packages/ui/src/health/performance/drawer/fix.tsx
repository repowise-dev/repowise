"use client";

import type { PerformanceOpportunity, PerformanceOpportunitySibling } from "@repowise-dev/types/health";

import { SourceExcerpt } from "../../../refactoring/source-excerpt";
import { WORK_UNIT_LABEL } from "../../labels";
import { RelatedWork, useRelatedWork } from "../../related-work";
import type { PerformanceViewAdapter } from "../adapter";
import {
  effortLabel,
  humanizeToken,
  INTERVENTION_KIND_LABEL,
  interventionKind,
  loopHeaderAbove,
  moduleScopeLabel,
  siblingFixLabel,
} from "../presentation";
import { Field, Section } from "./layout";

/** The fix itself: where it goes, what it buys, the code it changes, and the
 *  other causes on the same lines. */

/**
 * Under the title: where the edit goes. The sink is not repeated here; it is
 * named once, under the code it is reached from.
 */
export function WhereToEdit({ opportunity }: { opportunity: PerformanceOpportunity }) {
  const kind = interventionKind(opportunity);
  const symbol = opportunity.intervention_symbol?.trim();
  const where = symbol ? (moduleScopeLabel(symbol) ?? symbol) : opportunity.file_path;
  return (
    <p className="text-xs text-[var(--color-text-tertiary)]">
      Edit {kind ? `${INTERVENTION_KIND_LABEL[kind].toLowerCase()}, ` : ""}
      <span className="break-all font-mono text-[var(--color-text-secondary)]">{where}</span>
    </p>
  );
}

export function FixSummary({ opportunity }: { opportunity: PerformanceOpportunity }) {
  const effort = effortLabel(opportunity);
  return (
    <Section title={`The ${WORK_UNIT_LABEL.perf_fix}`}>
      <dl className="grid grid-cols-1 gap-x-4 gap-y-4 sm:grid-cols-2">
        <Field
          label="Gain"
          value={opportunity.gain_text ?? "Not stated by this server"}
          detail="The repeated work the fix removes. Not a measured time saving."
        />
        <Field
          label="Effort"
          value={effort ?? "Not sized"}
          {...(effort ? {} : { detail: "No stored plan, so the change was not sized." })}
        />
      </dl>
    </Section>
  );
}

/**
 * The code: the loop's call, read from the checkout at the line the first
 * observation stored, and the sink it reaches. The sink is a symbol without a
 * stored line, so it is named rather than excerpted.
 */
export function CodeSection({
  opportunity,
  adapter,
}: {
  opportunity: PerformanceOpportunity;
  adapter: PerformanceViewAdapter;
}) {
  const first = opportunity.evidence.find((item) => item.line_start != null);
  const sink = opportunity.terminal_sink?.trim();
  if (!first && !sink) return null;
  return (
    <Section title="The code">
      {first ? (
        <div className="space-y-1.5">
          <p className="text-xs text-[var(--color-text-tertiary)]">
            Where the loop makes the call,{" "}
            <span className="break-all font-mono text-[var(--color-text-secondary)]">
              {first.file_path}:{first.line_start}
            </span>
          </p>
          {adapter.readSource ? (
            <SourceExcerpt
              path={first.file_path}
              start={first.line_start!}
              end={(first.line_end ?? first.line_start!) + 2}
              readSource={adapter.readSource}
              widen={(fileLines) => loopHeaderAbove(fileLines, first.line_start!)}
            />
          ) : null}
        </div>
      ) : null}
      {sink ? (
        <p className="mt-3 text-xs text-[var(--color-text-tertiary)]">
          It reaches{" "}
          <span className="break-all font-mono text-[var(--color-text-secondary)]">
            {moduleScopeLabel(sink) ?? sink}
          </span>
          .
        </p>
      ) : null}
    </Section>
  );
}

/** What the other lenses hold for the file this cause is fixed in. */
export function RelatedSection({
  adapter,
  filePath,
}: {
  adapter: PerformanceViewAdapter;
  filePath: string;
}) {
  const data = useRelatedWork(adapter.getRelatedWork, [filePath]);
  return (
    <RelatedWork
      related={data?.files?.[0]}
      relatedWorkHref={adapter.relatedWorkHref}
      onNavigate={adapter.navigate}
      exclude={["performance"]}
      headingLevel="h4"
    />
  );
}

/**
 * Where this cause sits on the one map.
 *
 * A link rather than a second canvas: the galaxy already exists, it already
 * knows how to guarantee a file a node, and drawing a small copy of it here
 * would put two fields with two geometries on one screen.
 */
export function MapLink({
  adapter,
  opportunity,
}: {
  adapter: PerformanceViewAdapter;
  opportunity: PerformanceOpportunity;
}) {
  const href = adapter.mapHref?.(opportunity.opportunity_id, opportunity.file_path);
  if (!href || !opportunity.file_path) return null;
  return (
    <a
      href={href}
      className="inline-block rounded text-sm font-medium text-[var(--color-accent-primary)] underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
    >
      Show this cause on the map
    </a>
  );
}

/** One sibling's fix, as a link when the drawer can open another opportunity,
 *  plain text otherwise (an agent-facing or read-only host, say). */
function SiblingLink({
  sibling,
  label,
  onSelect,
}: {
  sibling: PerformanceOpportunitySibling;
  label: string;
  onSelect?: ((opportunityId: string) => void) | undefined;
}) {
  if (!onSelect) {
    return <span className="font-medium text-[var(--color-text-secondary)]">{label}</span>;
  }
  return (
    <button
      type="button"
      onClick={() => onSelect(sibling.opportunity_id)}
      className="rounded font-medium text-[var(--color-accent-primary)] underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
    >
      {label}
    </button>
  );
}

/** Other causes on the same lines, one quiet line. A `preferred` sibling says
 *  plainly that its fix is the stronger one; the rest are just named. */
export function SiblingsNote({
  siblings,
  onSelect,
}: {
  siblings: PerformanceOpportunitySibling[];
  onSelect?: ((opportunityId: string) => void) | undefined;
}) {
  const preferred = siblings.filter((sibling) => sibling.relation === "preferred");
  const rest = siblings.filter((sibling) => sibling.relation !== "preferred");
  return (
    <p className="mt-3 text-xs text-[var(--color-text-tertiary)]">
      {preferred.map((sibling) => (
        <span key={sibling.opportunity_id} className="block">
          {siblingFixLabel(sibling)} is the stronger fix here; see{" "}
          <SiblingLink sibling={sibling} label="this cause" onSelect={onSelect} />.
        </span>
      ))}
      {rest.length > 0 ? (
        <span className="block">
          Also flagged on these lines:{" "}
          {rest.map((sibling, index) => (
            <span key={sibling.opportunity_id}>
              {index > 0 ? ", " : ""}
              <SiblingLink
                sibling={sibling}
                label={humanizeToken(sibling.biomarker_type)}
                onSelect={onSelect}
              />
            </span>
          ))}
          .
        </span>
      ) : null}
    </p>
  );
}
