"use client";

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { getRepoSetup, updateOnboardingState, type SetupResponse } from "@/lib/api/setup";
import { getDeadCodeSummary } from "@/lib/api/dead-code";
import { analyzeBlastRadius } from "@/lib/api/blast-radius";
import { getHealthCoverage } from "@/lib/api/code-health";
import { getStatsHighlights } from "@/lib/api/stats";
import { getMetaVersion } from "@/lib/api/meta";
import { isNewBrowser, markVersionSeen, sessionMomentTaken, takeSessionMoment } from "@/lib/release-moment";
import {
  decideWelcome,
  historyYears,
  welcomeLenses,
  type WelcomeExtras,
  type WelcomeFigures,
  type WelcomeLens,
} from "@/lib/welcome-moment";
import { WelcomeDialog } from "./welcome-dialog";

export interface WelcomeMomentProps {
  repoId: string;
  repoName: string;
  fileCount: number;
  figures: WelcomeFigures;
  /** The busiest file: the risk card's change and an example question. */
  hotspotPath: string | null;
}

interface Loaded {
  setup: SetupResponse | null;
  lenses: WelcomeLens[];
  years: number | null;
}

const settled = <T,>(r: PromiseSettledResult<T>): T | null => (r.status === "fulfilled" ? r.value : null);

/** The figures step 1 needs beyond what the overview loaded. Each one that
 *  fails is left out, and its card with it. */
async function loadExtras(repoId: string, hotspotPath: string | null, needTests: boolean) {
  const [dead, blast, coverage, stats] = await Promise.allSettled([
    getDeadCodeSummary(repoId),
    hotspotPath ? analyzeBlastRadius(repoId, { changed_files: [hotspotPath] }) : Promise.reject(),
    needTests ? getHealthCoverage(repoId, { limit: 1, module_limit: 0 }) : Promise.reject(),
    getStatsHighlights(repoId),
  ]);
  const d = settled(dead);
  const unreachable = Number(d?.by_kind?.unreachable_file ?? 0);
  const b = settled(blast);
  const inferred = settled(coverage)?.inferred;
  const extras: WelcomeExtras = {
    // Never analysed reads as unmeasured, not as zero.
    deadCode: d && (d.analyzed_at || d.total_findings > 0)
      ? { files: unreachable, safe: d.safe_to_delete_count ?? 0 }
      : null,
    risk: b && hotspotPath ? { path: hotspotPath, reached: b.transitive_affected.length } : null,
    testsReached: inferred ? { reached: inferred.files_reached, total: inferred.files_total } : null,
  };
  return { extras, years: historyYears(settled(stats)?.origin.age_days) };
}

/**
 * Opens the welcome on the overview: once per repo per machine for a new
 * browser, or on demand with `?tour=1`. An automatic open marks the repo seen
 * on the server, stores the current version as seen so What's new stays
 * quiet, and takes the session's one interruption.
 */
export function WelcomeMoment({ repoId, repoName, fileCount, figures, hotspotPath }: WelcomeMomentProps) {
  const search = useSearchParams()?.toString() ?? "";
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const decided = useRef<string | null>(null);

  // Once per repo and query: StrictMode runs effects twice in development,
  // and a second run would stamp again. Not cancelled on cleanup for the
  // same reason; a late result after unmount is simply never shown.
  useEffect(() => {
    const key = `${repoId}?${search}`;
    if (decided.current === key) return;
    decided.current = key;
    void prepareWelcome({ repoId, search, fileCount, figures, hotspotPath }).then((next) => {
      if (!next) return;
      setLoaded(next);
      setOpen(true);
      dropTourParam();
    });
  }, [repoId, search, fileCount, figures, hotspotPath]);

  if (!loaded) return null;
  return (
    <WelcomeDialog
      open={open}
      onOpenChange={setOpen}
      repoId={repoId}
      repoName={repoName}
      fileCount={fileCount}
      years={loaded.years}
      lenses={loaded.lenses}
      setup={loaded.setup}
      hotspotPath={hotspotPath}
    />
  );
}

/** Drops `?tour=1` once the tour opens, so a reload does not replay it. */
function dropTourParam(): void {
  const url = new URL(window.location.href);
  if (!url.searchParams.has("tour")) return;
  url.searchParams.delete("tour");
  window.history.replaceState(window.history.state, "", url);
}

/** Decides whether the welcome opens, stamps what an open records, and
 *  loads its figures. Null when it stays shut. */
async function prepareWelcome({
  repoId,
  search,
  fileCount,
  figures,
  hotspotPath,
}: {
  repoId: string;
  search: string;
  fileCount: number;
  figures: WelcomeFigures;
  hotspotPath: string | null;
}): Promise<Loaded | null> {
  const setup = await getRepoSetup(repoId).catch(() => null);
  const seen = setup ? Boolean(setup.onboarding.welcome_seen?.[repoId]) : null;
  const mode = decideWelcome({
    search: search ? `?${search}` : "",
    fileCount,
    seen,
    newBrowser: isNewBrowser(),
    sessionTaken: sessionMomentTaken(),
  });
  if (mode === "none") return null;
  if (mode === "auto") await stampAutoOpen(repoId);
  // A first look through `?tour=1` (the first-index hand-off) still counts as
  // seen; a replay from Setup changes nothing.
  else if (seen === false) await stampRepoSeen(repoId);
  const { extras, years } = await loadExtras(repoId, hotspotPath, figures.coveragePct == null);
  return { setup, lenses: welcomeLenses(figures, extras), years };
}

/** Marks the repo's welcome seen on this machine. Best-effort: a failed
 *  write means it may open once more, never an error. */
function stampRepoSeen(repoId: string): Promise<unknown> {
  return updateOnboardingState({ welcome_seen_repo: repoId }).catch(() => undefined);
}

/** Before an automatic open: the repo seen, the session's one interruption
 *  taken, and the current version stored so What's new stays quiet. */
async function stampAutoOpen(repoId: string): Promise<void> {
  takeSessionMoment();
  try {
    const meta = await getMetaVersion();
    if (meta.server_version) markVersionSeen(meta.server_version);
  } catch {
    /* no version to store: What's new still waits for the next session */
  }
  await stampRepoSeen(repoId);
}
