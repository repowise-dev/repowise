"use client";

import { useMemo, type ReactNode } from "react";
import type { AffectedSignal, BlastRadiusResponse } from "@repowise-dev/types/blast-radius";
import { buildBlastMap, riskySpots, type BlastMap } from "./blast-map-model";
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

type Href = ((path: string) => string) | undefined;
type Result = BlastRadiusVerdictProps["result"];

const NONE = "text-xs text-[var(--color-text-secondary)]";
const MORE = "text-[11px] text-[var(--color-text-tertiary)]";

/** The lead sentence: files changed, then how far imports carry the change. */
function reachSentence(map: BlastMap, changedCount: number, analyzed: number): string {
  const capped = changedCount > analyzed ? ` (the first ${analyzed.toLocaleString()} are analyzed)` : "";
  const reach =
    map.reachedFiles === 0
      ? "Nothing else in the index imports them."
      : `Through imports it reaches ${plural(map.reachedFiles, "file")} in ${plural(map.reachedModules, "module")}, ${plural(
          map.firstHop.length + map.hiddenFirstHop,
          "file",
        )} directly.`;
  return `${plural(changedCount, "file")} changed${capped}. ${reach}`;
}

function LookFirst({ map, fileHref }: { map: BlastMap; fileHref: Href }) {
  const spots = riskySpots(map);
  if (spots.length === 0)
    return <p className={NONE}>No changed or directly affected file is a hotspot, in a weak health band, or without test evidence.</p>;
  return (
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
  );
}

function testsLimited(impact: Result["test_impact"]): boolean {
  if (!impact) return true;
  return ["unavailable", "degraded"].includes(impact.coverage.status) || impact.analysis.partial || impact.analysis.stale;
}

function TestsToRun({ impact }: { impact: Result["test_impact"] }) {
  const tests = impact?.recommendations ?? [];
  if (tests.length === 0)
    return (
      <p className={NONE}>
        {testsLimited(impact)
          ? "Test analysis is limited for this repository, so no list here does not mean no tests are needed."
          : "No test is known to run or import these files."}
      </p>
    );
  const total = impact?.recommendations_total ?? tests.length;
  return (
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
      {total > 5 && <p className={MORE}>{total.toLocaleString()} tests in all, listed under Tests to run below.</p>}
    </>
  );
}

function Reviewers({ reviewers }: { reviewers: Result["recommended_reviewers"] }) {
  if (reviewers.length === 0) return <p className={NONE}>No owner is recorded for the affected files.</p>;
  return (
    <ul className="space-y-1 text-xs">
      {reviewers.slice(0, 3).map((r) => (
        <li key={r.email} className="space-x-2">
          <span className="text-[var(--color-text-primary)]">{r.email}</span>
          <span className="text-[var(--color-text-tertiary)]">main owner of {plural(r.files, "affected file")}</span>
        </li>
      ))}
    </ul>
  );
}

function MissingPartners({ warnings, fileHref }: { warnings: Result["cochange_warnings"]; fileHref: Href }) {
  const partners = [...new Set(warnings.map((w) => w.missing_partner))];
  if (partners.length === 0) return <p className={NONE}>No file that history pairs with these is missing from the change.</p>;
  return (
    <>
      <ul className="space-y-1 text-xs">
        {partners.slice(0, 3).map((p) => (
          <li key={p} className="space-x-2">
            <FileLink path={p} href={fileHref?.(p)} />
            <span className="text-[var(--color-text-tertiary)]">not in this change</span>
          </li>
        ))}
      </ul>
      {partners.length > 3 && <p className={MORE}>{partners.length} in all, under Co-change warnings below.</p>}
    </>
  );
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
  return (
    <div className="space-y-5">
      <div className="space-y-1.5">
        <p className="text-base leading-relaxed text-[var(--color-text-primary)] [text-wrap:pretty]">
          {subject}: {reachSentence(map, changedTotal ?? changedFiles.length, changedFiles.length)}
        </p>
        <p className="text-xs text-[var(--color-text-tertiary)]">
          {REACH_WORD[result.structural_impact_band]} for this repository, from how central the changed files are and how far
          their importers spread. It is a structural reach estimate, not a chance of breakage.
        </p>
      </div>

      <div className="grid gap-x-8 gap-y-5 sm:grid-cols-2">
        <Block title="Look at first">
          <LookFirst map={map} fileHref={fileHref} />
        </Block>
        <Block title="Run these tests">
          <TestsToRun impact={result.test_impact} />
        </Block>
        <Block title="Ask for review">
          <Reviewers reviewers={result.recommended_reviewers} />
        </Block>
        <Block title="Usually changes with these">
          <MissingPartners warnings={result.cochange_warnings} fileHref={fileHref} />
        </Block>
      </div>
    </div>
  );
}
