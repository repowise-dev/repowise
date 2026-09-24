"use client";

/**
 * The lens: what the map is currently answering. Blast radius, breaking
 * changes, conformance and the cyclic core each recolour the same canvas, so
 * they are one axis and one control. The blast-radius target picker appears
 * only while that lens is on, because it means nothing otherwise.
 */

import type { SystemNode } from "@repowise-dev/types";
import { MICRO_LABEL, Segmented, type SegmentOption } from "./system-map-filters";

export type SystemMapLens = "none" | "blast" | "breaking" | "conformance" | "core";

export interface SystemMapLensControlProps {
  value: SystemMapLens;
  /** Options after the host has decided counts and disabled reasons. */
  options: SegmentOption<SystemMapLens>[];
  onChange: (lens: SystemMapLens) => void;
  /** Services the blast radius can start from. */
  nodes: readonly SystemNode[];
  blastTarget: string | null;
  onBlastTargetChange: (id: string | null) => void;
  includeBehavioral: boolean;
  onIncludeBehavioralChange: (include: boolean) => void;
}

export function SystemMapLensControl({
  value,
  options,
  onChange,
  nodes,
  blastTarget,
  onBlastTargetChange,
  includeBehavioral,
  onIncludeBehavioralChange,
}: SystemMapLensControlProps) {
  const sorted = [...nodes].sort((a, b) => a.repo.localeCompare(b.repo) || a.name.localeCompare(b.name));

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
      <span className={MICRO_LABEL}>Lens</span>
      <Segmented label="Map lens" value={value} options={options} onChange={onChange} />
      {value === "blast" && (
        <>
          <label className="inline-flex min-w-0 items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
            <span className="sr-only">Service the change starts in</span>
            <span aria-hidden>If</span>
            <select
              value={blastTarget ?? ""}
              onChange={(e) => onBlastTargetChange(e.target.value || null)}
              className="min-w-0 max-w-[16rem] rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2 py-1 text-xs text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
            >
              <option value="">choose a service</option>
              {sorted.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.service_path ? `${n.repo} / ${n.name}` : n.name}
                </option>
              ))}
            </select>
            <span aria-hidden>changes</span>
          </label>
          <label className="inline-flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
            <input
              type="checkbox"
              checked={includeBehavioral}
              onChange={(e) => onIncludeBehavioralChange(e.target.checked)}
              className="accent-[var(--color-accent-primary)]"
            />
            Include co-change
          </label>
        </>
      )}
    </div>
  );
}
