"use client";

import * as React from "react";
import { AiPromptButton } from "../health/ai-prompt-button";

/**
 * The anatomy two co-change drawers share: the single-repo coupling pair and
 * the cross-repo workspace pair. Their evidence differs (directional shares
 * and a graph verdict on one side, repositories and contract links on the
 * other), so each drawer owns its sections; these are the parts that must
 * read the same in both.
 */

export const PAIR_MICRO =
  "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

export interface PairFact {
  label: string;
  /** Optional explainer beside the label, e.g. an InfoTip. */
  hint?: React.ReactNode;
  value: React.ReactNode;
}

/** A hairline two-column ribbon, not tiles: different kinds of fact, and
 *  equal-weight boxes would claim they are one. */
export function PairFactGrid({ facts }: { facts: PairFact[] }) {
  return (
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
          <dt className={f.hint ? `${PAIR_MICRO} flex items-center gap-1` : PAIR_MICRO}>
            {f.label}
            {f.hint}
          </dt>
          <dd className="mt-1">{f.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** The drawer's one action, named and explained. */
export function PairAgentSection({
  body,
  label,
  onClick,
}: {
  body: React.ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className={PAIR_MICRO}>Hand this to an agent</h3>
      <p className="text-xs leading-relaxed text-[var(--color-text-secondary)]">{body}</p>
      <AiPromptButton label={label} onClick={onClick} className="w-fit" />
    </section>
  );
}
