"use client";

import * as React from "react";

import { formatTokens } from "../lib/format";
import type { SavingsView } from "./types";

export interface OpportunityItem {
  /**
   * Stable list key. Separate from `title` because a ledger kind is an open
   * vocabulary: two kinds can render the same phrase, and an empty kind
   * renders an empty one, so keying on the rendered text collides.
   */
  id: string;
  /** What was observed, as a heading-weight phrase. */
  title: string;
  /** The evidence, in a sentence. */
  detail: React.ReactNode;
  /** The potential, pre-formatted. Omit where there is no estimate. */
  potential?: string | undefined;
  /** An ordinary documentation link. Deliberately not an action: see below. */
  href?: string | undefined;
  linkLabel?: string | undefined;
}

export interface OpportunityListProps {
  items: OpportunityItem[];
  LinkComponent?: React.ElementType | undefined;
}

/**
 * Behaviour that could have been optimised and was not.
 *
 * Never part of the savings total, and the section says so rather than
 * relying on the reader to infer it from placement.
 *
 * **Every row here is guidance, and is built so it cannot look like anything
 * else.** The previous version rendered "Enable auto-capture" in a bordered,
 * amber, icon-led block that read as an in-product control and only opened a
 * documentation page when pressed. Decoration teaches a reader that an element
 * responds, so a dead path styled as a control is a lie told in CSS. These
 * rows are plain text with, at most, an ordinary link.
 *
 * If one of these ever earns a real action -- something that performs a local
 * setup rather than describing one -- it gets a real button then, and the
 * button goes here rather than this styling going anywhere else.
 */
export function OpportunityList({ items, LinkComponent }: OpportunityListProps) {
  if (items.length === 0) return null;
  const A = LinkComponent ?? "a";

  return (
    <ul className="flex flex-col">
      {items.map((item) => (
        <li
          key={item.id}
          className="flex flex-col gap-1 border-b border-[var(--color-border-default)] py-3.5 last:border-b-0"
        >
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <span className="min-w-0 text-sm font-medium text-[var(--color-text-primary)]">
              {item.title}
            </span>
            {item.potential && (
              <span className="shrink-0 text-sm tabular-nums text-[var(--color-text-secondary)]">
                {item.potential}
              </span>
            )}
          </div>
          <p className="max-w-[72ch] text-[13px] leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
            {item.detail}
            {item.href && (
              <>
                {" "}
                <A
                  href={item.href}
                  {...(isExternal(item.href)
                    ? { target: "_blank", rel: "noreferrer" }
                    : {})}
                  className="text-[var(--color-accent-primary)] hover:underline"
                >
                  {item.linkLabel ?? "Read more"}
                </A>
              </>
            )}
          </p>
        </li>
      ))}
    </ul>
  );
}

function isExternal(href: string): boolean {
  return /^https?:\/\//.test(href);
}

export interface BuildOpportunitiesOptions {
  /** Where the distillation setup guide lives. */
  distillDocsHref?: string | undefined;
}

/**
 * The transcript-mined opportunities, as rows.
 *
 * A function rather than prop-drilling five fields through the component, and
 * exported so the copy is asserted directly by a test instead of through a
 * render. The wording is cautious on purpose: these are observations of what
 * was not saved, and the page must not claim a Repowise call *would have*
 * replaced them.
 */
export function buildOpportunities(
  data: SavingsView,
  { distillDocsHref }: BuildOpportunitiesOptions = {},
): OpportunityItem[] {
  const items: OpportunityItem[] = [];

  if (data.missed_events > 0) {
    items.push({
      id: "missed_distill",
      title: `${data.missed_events.toLocaleString()} command${
        data.missed_events === 1 ? "" : "s"
      } bypassed distillation`,
      potential: `Observed opportunity: ~${formatTokens(data.missed_tokens_est)} tokens`,
      detail: (
        <>
          Commands that ran raw {windowPhrase(data.missed_window_days)} would have been
          candidates for distillation. Nothing was saved on them.
        </>
      ),
      ...(distillDocsHref
        ? {
            href: distillDocsHref,
            linkLabel: "See the distillation setup guide",
          }
        : {}),
    });
  }

  if (data.reread_events > 0) {
    items.push({
      id: "reread",
      title: `${data.reread_events.toLocaleString()} unchanged-file re-read${
        data.reread_events === 1 ? "" : "s"
      }`,
      // "Potentially avoid" promised a future saving from an observation of
      // past behaviour. Every row in this section is the same kind of claim
      // and now says so the same way.
      potential: `Observed opportunity: ~${formatTokens(data.reread_tokens_est)} tokens`,
      detail: (
        <>
          {data.reread_events === 1 ? "One full re-read" : "Full re-reads"} of files that had
          not changed {data.reread_events === 1 ? "was" : "were"} observed.
        </>
      ),
    });
  }

  for (const row of data.per_opportunity_kind) {
    // The count goes in the sentence, not in front of the kind. Opportunity
    // kinds are an open vocabulary read straight off the ledger, so
    // `"12 " + slug` yields "12 hook replacement declined" for any kind that
    // is not already plural. Naming the kind and counting it separately stays
    // grammatical whatever a future surface records, without this file
    // keeping a label map of kinds it cannot know.
    items.push({
      id: `kind:${row.kind}`,
      title: sentenceCase(row.kind.replace(/_/g, " ")) || "Unnamed opportunity",
      potential: `Observed opportunity: ~${formatTokens(
        row.estimated_potential_input_tokens,
      )} tokens`,
      detail: (
        <>
          Observed {row.observations.toLocaleString()} time
          {row.observations === 1 ? "" : "s"}, and recorded by a capture surface as an
          opportunity rather than a saving.
        </>
      ),
    });
  }

  return items;
}

function sentenceCase(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/**
 * Name the miner's window without asserting a day count it does not have.
 *
 * `missed_window_days` is a float on the wire, so a freshly indexed
 * repository can report a fraction of a day. Rounding it printed "over the
 * last 0 days", which reads as a bug and understates the window besides.
 */
function windowPhrase(days: number): string {
  if (!Number.isFinite(days) || days <= 0) return "recently";
  if (days < 1) return "in the last day";
  const whole = Math.round(days);
  return `over the last ${whole} day${whole === 1 ? "" : "s"}`;
}
