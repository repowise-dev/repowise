"use client";

/**
 * The review block's two pieces of the lede: the change-risk line under the
 * verdict, and the list of what the review could not check. Props only; the
 * words come from `change-review-model`.
 */

import type { ChangeRiskBlock } from "@repowise-dev/types/blast-radius";
import { PriorityBadge } from "../commits/priority-badge";
import { riskLede, type Lane } from "./change-review-model";

/**
 * The calibrated score, demoted under the verdict and led by what it means:
 * a rank against this repo's own commits, then what drove it. The scale is
 * in the hover.
 */
export function RiskLine({ risk }: { risk: ChangeRiskBlock }) {
  const lede = riskLede(risk);
  return (
    <p className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-xs text-[var(--color-text-secondary)]" title={lede.detail}>
      {lede.priority && (
        <span className="inline-flex items-baseline gap-1">
          <span className="text-[var(--color-text-tertiary)]">Review priority</span>
          <PriorityBadge priority={lede.priority} />
        </span>
      )}
      <span>{lede.sentence}</span>
      {lede.drivers && <span className="text-[var(--color-text-tertiary)]">{lede.drivers}</span>}
    </p>
  );
}

/**
 * What the review could not check, one line each, in words. Nothing renders
 * when every lane measured: a clean lane is not news.
 */
export function NotChecked({ lanes }: { lanes: Lane[] }) {
  if (lanes.length === 0) return null;
  return (
    <section className="space-y-2" aria-labelledby="review-not-checked">
      <h3 id="review-not-checked" className="text-[15px] font-medium text-[var(--color-text-primary)]">
        Not checked
      </h3>
      <ul className="space-y-1 text-xs">
        {lanes.map((lane) => (
          <li key={lane.key} className="flex flex-wrap gap-x-1.5">
            <span className="text-[var(--color-text-secondary)]">
              {lane.name}: {lane.state}
            </span>
            {lane.reason && <span className="text-[var(--color-text-tertiary)]">({lane.reason})</span>}
          </li>
        ))}
      </ul>
    </section>
  );
}
