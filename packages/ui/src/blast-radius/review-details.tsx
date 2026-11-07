"use client";

import type { AffectedSignal, BlastRadiusResponse } from "@repowise-dev/types/blast-radius";
import { CollapsibleSection } from "../shared/collapsible-section";
import { CochangeTable } from "./cochange-table";
import { PathRef } from "./blast-radius-verdict";
import type { Reach } from "./blast-map-model";
import { formatNumber } from "../lib/format";

type Result = BlastRadiusResponse & { affected_signals?: AffectedSignal[] };

/**
 * The details a review rarely needs first, collapsed and last: changed files
 * with no test, and the files history says
 * usually change with these that this change leaves out. Each section stays
 * out when it has nothing in it.
 */
function UntestedList({ paths, fileHref }: { paths: string[]; fileHref: ((path: string) => string) | undefined }) {
  return (
    <ul className="space-y-1.5">
      {paths.map((path) => (
        <li key={path} className="flex min-w-0 items-center justify-between gap-x-3 gap-y-0.5 text-xs max-sm:flex-wrap">
          <PathRef path={path} href={fileHref?.(path)} />
        </li>
      ))}
    </ul>
  );
}

export function ReviewDetails({ result, reach, fileHref }: { result: Result; reach: Reach; fileHref: ((path: string) => string) | undefined }) {
  // One meaning per number: changed files with no test. Tests and files the
  // index does not have yet are not in it.
  const changed = reach.changed.filter((m) => m.untested).map((m) => m.path);
  const partners = new Set(result.cochange_warnings.map((w) => w.missing_partner)).size;
  if (changed.length === 0 && partners === 0) return null;
  return (
    <div>
      {changed.length > 0 && (
        <CollapsibleSection title="Changed files with no test" hint={formatNumber(changed.length)}>
          <UntestedList paths={changed} fileHref={fileHref} />
        </CollapsibleSection>
      )}
      {partners > 0 && (
        <CollapsibleSection
          title="Usually change together, not in this change"
          hint={`${formatNumber(partners)} ${partners === 1 ? "file" : "files"}`}
        >
          <CochangeTable rows={result.cochange_warnings} />
        </CollapsibleSection>
      )}
    </div>
  );
}
