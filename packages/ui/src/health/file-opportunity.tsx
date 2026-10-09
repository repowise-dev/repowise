"use client";

/**
 * The finding -> opportunity link, for every surface that lists a file's
 * findings.
 *
 * Until now this existed once, for one biomarker family: the performance tab
 * passed `refactoringPlanHref` and the performance queue and drawer used it.
 * Everywhere else a reader who had just been told what is wrong with a file had
 * no way to reach the plan the same analysis wrote for it.
 *
 * Three facts make the general version cheap and honest:
 *
 * - **A file has at most one opportunity.** Measured on the dogfood index: 581
 *   open opportunities over 581 distinct files. So this is one lookup per file
 *   surface, not one per finding, and there is no ambiguity about which record a
 *   finding points at.
 * - **A step names the cause it answers.** The link is offered only where the
 *   opportunity's lead cause or one of its steps actually addresses that
 *   finding's biomarker. Linking every finding to the file's opportunity would
 *   tell a reader their coverage gap has a plan when the plan is to split the
 *   file.
 * - **It is not the performance lens.** These rows carry step counts and the
 *   mechanical split, never a benefit figure or a runtime claim; performance
 *   causes keep their own link, their own semantics and their own lens gate.
 */

import * as React from "react";
import { Wrench } from "lucide-react";

import { stepSummary } from "../refactoring/opportunity";
import type { RefactoringOpportunity } from "@repowise-dev/types/refactoring";

export interface FileOpportunityAdapter {
  /** The file's one composed opportunity, or null. Steps included. */
  getFileOpportunity?(filePath: string): Promise<RefactoringOpportunity | null>;
  /** Deep link into the refactoring surface. Omit and nothing links. */
  refactoringOpportunityHref?(opportunityId: string): string;
}

/**
 * Fetch a file's opportunity once, for the whole findings list.
 *
 * Returns `null` for "asked, and there is none" and `undefined` for "not asked
 * yet or this host cannot ask", so a caller never renders a "no plan" claim it
 * has not established.
 */
export function useFileOpportunity(
  adapter: FileOpportunityAdapter,
  filePath: string | null | undefined,
): RefactoringOpportunity | null | undefined {
  const [value, setValue] = React.useState<RefactoringOpportunity | null | undefined>(
    undefined,
  );
  const fetcher = adapter.getFileOpportunity;

  React.useEffect(() => {
    if (!fetcher || !filePath) {
      setValue(undefined);
      return;
    }
    let live = true;
    setValue(undefined);
    void fetcher(filePath)
      .then((result) => {
        if (live) setValue(result ?? null);
      })
      .catch(() => {
        // A failed lookup is not "no opportunity". Staying undefined keeps the
        // surface silent rather than asserting a clean file.
        if (live) setValue(undefined);
      });
    return () => {
      live = false;
    };
  }, [fetcher, filePath]);

  return value;
}

/**
 * Whether this opportunity answers this finding's cause.
 *
 * The lead biomarker or any step's source biomarker. A step list truncated by
 * the server can only lose a match, never invent one, so the worst case is a
 * link that is not offered.
 */
export function opportunityAddresses(
  opportunity: RefactoringOpportunity | null | undefined,
  biomarkerType: string,
): boolean {
  if (!opportunity) return false;
  if (opportunity.lead_biomarker === biomarkerType) return true;
  return (opportunity.steps ?? []).some((step) => step.source_biomarker === biomarkerType);
}

export interface FindingOpportunityLinkProps {
  opportunity: RefactoringOpportunity | null | undefined;
  biomarkerType: string;
  href?: ((opportunityId: string) => string) | undefined;
  /** Host navigation, for a surface that routes rather than follows an anchor. */
  onNavigate?: ((href: string) => void) | undefined;
}

/** The link itself. Renders nothing unless there is a real, addressing plan. */
export function FindingOpportunityLink({
  opportunity,
  biomarkerType,
  href,
  onNavigate,
}: FindingOpportunityLinkProps) {
  if (!opportunity || !href) return null;
  if (!opportunityAddresses(opportunity, biomarkerType)) return null;
  const target = href(opportunity.opportunity_id);
  if (!target) return null;

  return (
    <a
      href={target}
      onClick={
        onNavigate
          ? (e) => {
              e.preventDefault();
              e.stopPropagation();
              onNavigate(target);
            }
          : (e) => e.stopPropagation()
      }
      className="inline-flex items-center gap-1.5 rounded text-[11.5px] font-medium text-[var(--color-accent-primary)] underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--color-accent-primary)]"
    >
      <Wrench className="h-3 w-3" aria-hidden="true" />
      Refactoring plan: {stepSummary(opportunity)}
    </a>
  );
}
