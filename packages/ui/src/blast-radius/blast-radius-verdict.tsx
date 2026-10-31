"use client";

import { useMemo, type ReactNode } from "react";
import type { AffectedSignal, BlastRadiusResponse } from "@repowise-dev/types/blast-radius";
import { buildBlastMap, riskySpots } from "./blast-map-model";
import { describeMarks } from "./blast-radius-map";
import { MICRO } from "../health/tokens";

const REACH_WORD: Record<BlastRadiusResponse["structural_impact_band"], string> = {
  localized: "Localized reach",
  moderate: "Moderate reach",
  broad: "Broad reach",
};

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}

function FileLink({ path, href }: { path: string; href: string | undefined }) {
  return href ? (
    <a href={href} className="break-all font-mono text-[var(--color-text-primary)] hover:underline">
      {path}
    </a>
  ) : (
    <span className="break-all font-mono text-[var(--color-text-primary)]">{path}</span>
  );
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="min-w-0 space-y-1.5">
      <p className={MICRO}>{title}</p>
      {children}
    </div>
  );
}

export interface BlastRadiusVerdictProps {
  result: BlastRadiusResponse & { affected_signals?: AffectedSignal[] };
  changedFiles: string[];
  /** Every path the change touches, when more than `changedFiles` were analyzed. */
  changedTotal?: number | undefined;
  /** What the change is called: a branch, a short sha, "Uncommitted changes". */
  subject: ReactNode;
  fileHref?: ((path: string) => string) | undefined;
}

/**
 * The judgement before the picture: how far the change reaches, then what to
 * do about it. Which files to look at first, which tests to run, who to ask,
 * and which usual partners the change left out. Each block says when it has
 * nothing, and why, so an empty list never reads as an all-clear.
 */
export function BlastRadiusVerdict({
  result,
  changedFiles,
  changedTotal,
  subject,
  fileHref,
}: BlastRadiusVerdictProps) {
  const map = useMemo(() => buildBlastMap(result, changedFiles), [result, changedFiles]);
  const spots = riskySpots(map);
  const impact = result.test_impact;
  const tests = impact?.recommendations ?? [];
  const testsLimited =
    !impact || ["unavailable", "degraded"].includes(impact.coverage.status) || impact.analysis.partial || impact.analysis.stale;
  const changedCount = changedTotal ?? changedFiles.length;
  const missingPartners = [...new Map(result.cochange_warnings.map((w) => [w.missing_partner, w])).values()];

  return (
    <div className="space-y-5">
      <div className="space-y-1.5">
        <p className="text-base leading-relaxed text-[var(--color-text-primary)] [text-wrap:pretty]">
          {subject}: {plural(changedCount, "file")} changed
          {changedTotal !== undefined && changedTotal > changedFiles.length
            ? ` (the first ${changedFiles.length.toLocaleString()} are analyzed)`
            : ""}
          .{" "}
          {map.reachedFiles === 0
            ? "Nothing else in the index imports them."
            : `Through imports it reaches ${plural(map.reachedFiles, "file")} in ${plural(map.reachedModules, "module")}, ${plural(
                map.firstHop.length + map.hiddenFirstHop,
                "file",
              )} directly.`}
        </p>
        <p className="text-xs text-[var(--color-text-tertiary)]">
          {REACH_WORD[result.structural_impact_band]} for this repository, from how central the changed files are and how far
          their importers spread. It is a structural reach estimate, not a chance of breakage.
        </p>
      </div>

      <div className="grid gap-x-8 gap-y-5 sm:grid-cols-2">
        <Block title="Look at first">
          {spots.length > 0 ? (
            <ul className="space-y-1 text-xs">
              {spots.map((n) => (
                <li key={n.key} className="space-x-2">
                  <FileLink path={n.path} href={fileHref?.(n.path)} />
                  <span className="text-[var(--color-text-tertiary)]">
                    {n.hop === 0 ? "changed" : "imports it"} · {describeMarks(n).join(" · ")}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-[var(--color-text-secondary)]">
              No changed or directly affected file is a hotspot, in a weak health band, or without test evidence.
            </p>
          )}
        </Block>

        <Block title="Run these tests">
          {tests.length > 0 ? (
            <>
              <ul className="space-y-1 text-xs">
                {tests.slice(0, 5).map((t) => (
                  <li key={`${t.repository_id}:${t.test_id}`} className="space-x-2">
                    <span className="break-all font-mono text-[var(--color-text-primary)]">{t.test_file ?? t.test_id}</span>
                    <span className="text-[var(--color-text-tertiary)]">
                      {t.basis === "measured" ? "coverage shows it runs this code" : "reaches it through imports"}
                    </span>
                  </li>
                ))}
              </ul>
              {(impact?.recommendations_total ?? 0) > 5 && (
                <p className="text-[11px] text-[var(--color-text-tertiary)]">
                  {impact!.recommendations_total.toLocaleString()} tests in all, listed under Tests to run below.
                </p>
              )}
            </>
          ) : (
            <p className="text-xs text-[var(--color-text-secondary)]">
              {testsLimited
                ? "Test analysis is limited for this repository, so no list here does not mean no tests are needed."
                : "No test is known to run or import these files."}
            </p>
          )}
        </Block>

        <Block title="Ask for review">
          {result.recommended_reviewers.length > 0 ? (
              <ul className="space-y-1 text-xs">
                {result.recommended_reviewers.slice(0, 3).map((r) => (
                  <li key={r.email} className="space-x-2">
                    <span className="text-[var(--color-text-primary)]">{r.email}</span>
                    <span className="text-[var(--color-text-tertiary)]">main owner of {plural(r.files, "affected file")}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-[var(--color-text-secondary)]">No owner is recorded for the affected files.</p>
            )}
        </Block>

        <Block title="Usually changes with these">
          {missingPartners.length > 0 ? (
            <>
              <ul className="space-y-1 text-xs">
                {missingPartners.slice(0, 3).map((w) => (
                  <li key={w.missing_partner} className="space-x-2">
                    <FileLink path={w.missing_partner} href={fileHref?.(w.missing_partner)} />
                    <span className="text-[var(--color-text-tertiary)]">not in this change</span>
                  </li>
                ))}
              </ul>
              {missingPartners.length > 3 && (
                <p className="text-[11px] text-[var(--color-text-tertiary)]">
                  {missingPartners.length} in all, under Co-change warnings below.
                </p>
              )}
            </>
          ) : (
            <p className="text-xs text-[var(--color-text-secondary)]">
              No file that history pairs with these is missing from the change.
            </p>
          )}
        </Block>
      </div>
    </div>
  );
}
