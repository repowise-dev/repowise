import { analyzeBlastRadius } from "@repowise-dev/api-client/blast-radius";
import { getReviewerSuggestions } from "@repowise-dev/api-client/git";
import type { BlastRadiusResponse } from "@repowise-dev/types/blast-radius";
import type { ReviewerSuggestion } from "@repowise-dev/api-client/types";
import { CONFIG_SECTION } from "../constants";
import { changeSignature } from "../shared/changeImpact";
import type { ChangeImpactReport } from "../shared/webviewMessages";
import type { RepowiseContext } from "./context";
import { getBranchChangedFiles, getChangedFiles } from "./gitApi";
import * as vscode from "vscode";

/**
 * The one place the extension turns "what changed" into "what it touches". Both
 * the Change Risk panel and the ambient co-change nudge call this, so the blast
 * and reviewer analysis is computed once, cached, and shared.
 *
 * The network result depends only on the CHANGED FILE SET and the indexed
 * commit, never on file contents (blast radius is graph-derived). So it is
 * cached under a signature of the sorted path set, tagged by the indexed head
 * commit. Editing lines in an already-changed file leaves the set unchanged and
 * hits the cache; adding or removing a file from the set triggers one fetch.
 */

export type ChangeScope = ChangeImpactReport["scope"];

/** Upper bound on paths sent to the server; a sprawling set is capped, not refused. */
const MAX_CHANGED_FILES = 400;

/** The cached half: the two network results keyed by the change-set signature. */
interface ImpactData {
  blast: BlastRadiusResponse | null;
  reviewers: ReviewerSuggestion[];
  /** True only when both endpoints succeeded; a degraded result is not cached. */
  complete: boolean;
}

/** Shared across callers so two surfaces analyzing the same set fetch once. */
const inFlight = new Map<string, Promise<ImpactData>>();

/** Resolves the base ref for branch-scope diffing (matches the risk panel). */
function resolveBase(ctx: RepowiseContext): string {
  const configured = vscode.workspace
    .getConfiguration(CONFIG_SECTION)
    .get<string>("risk.baseBranch", "")
    .trim();
  return configured || ctx.repo?.default_branch || "main";
}

function emptyReport(
  scope: ChangeScope,
  gitUnavailable: boolean,
): ChangeImpactReport {
  return {
    changed: [],
    stagedCount: 0,
    workingCount: 0,
    scope,
    blast: null,
    reviewers: [],
    gitUnavailable,
  };
}

/**
 * Analyzes the current change set. Returns a clean (empty) report when the tree
 * has nothing to analyze, and a `gitUnavailable` report when git cannot be read
 * (callers distinguish the two: clean means "nothing to say", unavailable means
 * "cannot say"). Never throws: a failing endpoint degrades its own section.
 */
export async function analyzeChange(
  ctx: RepowiseContext,
  scope: ChangeScope,
): Promise<ChangeImpactReport> {
  const id = ctx.repoId;
  const root = ctx.workspace.repoRoot;
  if (!id || !root || ctx.getExtensionState() !== "ready") {
    return emptyReport(scope, false);
  }

  const files = await getChangedFiles(root);
  if (!files) return emptyReport(scope, true);

  const working = dedupeSorted([...files.staged, ...files.workingTree]);
  let changed = working;
  if (scope === "branch") {
    const branchFiles = await getBranchChangedFiles(root, resolveBase(ctx));
    if (branchFiles) changed = dedupeSorted([...branchFiles, ...working]);
  }
  changed = changed.slice(0, MAX_CHANGED_FILES);

  if (changed.length === 0) {
    const clean = emptyReport(scope, false);
    clean.stagedCount = files.staged.length;
    clean.workingCount = files.workingTree.length;
    return clean;
  }

  const data = await loadImpact(ctx, id, changed);
  return {
    changed,
    stagedCount: files.staged.length,
    workingCount: files.workingTree.length,
    scope,
    blast: data.blast,
    reviewers: data.reviewers,
    gitUnavailable: false,
  };
}

/** Serves the blast+reviewer pair from the tagged cache; misses share a request. */
function loadImpact(
  ctx: RepowiseContext,
  id: string,
  changed: string[],
): Promise<ImpactData> {
  const sig = changeSignature(changed);
  const tag = ctx.repo?.head_commit ?? "";
  const key = `changeIntel:${sig}`;
  const hit = ctx.cache.get<ImpactData>(id, key, tag);
  if (hit !== undefined) return Promise.resolve(hit);

  const flightKey = `${id}|${tag}|${sig}`;
  const pending = inFlight.get(flightKey);
  if (pending) return pending;

  const request = fetchImpact(id, changed)
    .then((data) => {
      // Only persist a complete result. Caching a transient failure would pin
      // the degraded value until the file set or the index moved, with no
      // retry; instead we return it once and re-fetch on the next call.
      if (data.complete) ctx.cache.set(id, key, tag, data);
      return data;
    })
    .finally(() => {
      inFlight.delete(flightKey);
    });
  inFlight.set(flightKey, request);
  return request;
}

/** Runs both endpoints in parallel; either failing degrades to its empty shape. */
async function fetchImpact(
  id: string,
  changed: string[],
): Promise<ImpactData> {
  const [blast, reviewers] = await Promise.allSettled([
    analyzeBlastRadius(id, { changed_files: changed }),
    getReviewerSuggestions(id, changed),
  ]);
  return {
    blast: blast.status === "fulfilled" ? blast.value : null,
    reviewers: reviewers.status === "fulfilled" ? reviewers.value.suggestions : [],
    complete: blast.status === "fulfilled" && reviewers.status === "fulfilled",
  };
}

function dedupeSorted(paths: string[]): string[] {
  return [...new Set(paths)].sort();
}
