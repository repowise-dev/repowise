"use client";

import type { AffectedSignal, BlastRadiusResponse } from "@repowise-dev/types/blast-radius";
import { CollapsibleSection } from "../shared/collapsible-section";
import { CochangeTable } from "./cochange-table";
import { CHIP, PathRef } from "./blast-radius-verdict";
import type { Reach } from "./blast-map-model";

type Result = BlastRadiusResponse & { affected_signals?: AffectedSignal[] };

/**
 * The details a review rarely needs first, collapsed and last: every file
 * with no test found (the changed ones on top), and every pair history says
 * usually changes together where this change leaves one out. Each section
 * stays out when it has nothing in it.
 */
export function ReviewDetails({ result, reach, fileHref }: { result: Result; reach: Reach; fileHref: ((path: string) => string) | undefined }) {
  const changed = reach.changed.filter((m) => m.untested).map((m) => m.path);
  const dependents = reach.files.filter((f) => f.untested).map((f) => f.path);
  const untested = [...changed, ...dependents];
  if (untested.length === 0 && result.cochange_warnings.length === 0) return null;
  return (
    <div>
      {untested.length > 0 && (
        <CollapsibleSection title="Files with no test found" hint={untested.length.toLocaleString()}>
          <ul className="space-y-1.5">
            {untested.map((path, i) => (
              <li key={path} className="flex min-w-0 items-center justify-between gap-3 text-xs">
                <PathRef path={path} href={fileHref?.(path)} />
                <span className={CHIP}>{i < changed.length ? "changed" : "depends on it"}</span>
              </li>
            ))}
          </ul>
        </CollapsibleSection>
      )}
      {result.cochange_warnings.length > 0 && (
        <CollapsibleSection
          title="Usually change together, not in this change"
          hint={result.cochange_warnings.length.toLocaleString()}
        >
          <CochangeTable rows={result.cochange_warnings} />
        </CollapsibleSection>
      )}
    </div>
  );
}
