"use client";

/**
 * Impact view: pick a change, see how far it reaches and what to do about it.
 *
 * With a host that lists changes (`listChanges` + `analyzeChange`), the view
 * leads with them: work in progress, branches ahead of the default branch and
 * recent commits, and opens the most relevant one on arrival. Picking files by
 * hand stays available as a secondary path, and is the only path for a host
 * that does not list changes. Every result reads the same way: a verdict with
 * actions, the map, then the detail tables.
 *
 * Presentation and orchestration only: the host injects fetching, analysis,
 * file links and the reviewer slot through an {@link ImpactAdapter}, so web
 * and hosted render the same view from one source.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { Plus, Flame } from "lucide-react";
import type { AffectedSignal, BlastRadiusResponse, ChangeCandidate } from "@repowise-dev/types/blast-radius";

import { Button } from "../ui/button";
import { Skeleton } from "../ui/skeleton";
import { FilePathPicker } from "../health/file-path-picker";
import { BlastRadiusResults } from "./blast-radius-results";
import { BlastRadiusMap } from "./blast-radius-map";
import { BlastRadiusVerdict } from "./blast-radius-verdict";
import { ChangePicker } from "./change-picker";
import { HealthCheckPanel, type HealthCheckState } from "./health-check-panel";
import type { ImpactAdapter } from "./impact-adapter";
import { toFriendlyMessage } from "../lib/errors";
import { disambiguateBasenames } from "../lib/format";
import { FilePath } from "../shared/file-path";
import { EmptyState } from "../shared/empty-state";
import { MICRO } from "../health/tokens";

type Selection = { kind: "change"; id: string } | { kind: "files"; files: string[] };

interface Analysis {
  /** What was analyzed, so the verdict never names a newer pick than its numbers. */
  selection: Selection;
  result: BlastRadiusResponse & { affected_signals?: AffectedSignal[] };
  changedFiles: string[];
  changedTotal?: number;
}

const DEPTHS = [1, 2, 3, 4, 5];

const SHA = /^[0-9a-f]{40}$/;

/**
 * A change's id in a shareable link: `wip`, a branch name, or a full sha.
 * Stable across reloads and machines, unlike a list position.
 */
export function changeLinkParam(changeId: string): string {
  if (changeId === "working-tree") return "wip";
  if (changeId.startsWith("commit:")) return changeId.slice("commit:".length);
  const branch = changeId.replace(/^branch:/, "");
  // A branch named like the other two forms keeps its prefix; `:` never
  // appears in a git branch name, so the prefixed form is unambiguous.
  return branch === "wip" || SHA.test(branch) ? `branch:${branch}` : branch;
}

/** The change id a link's `change` value names. */
export function changeIdFromLink(param: string): string {
  if (param.startsWith("branch:")) return param;
  if (param === "wip") return "working-tree";
  return SHA.test(param) ? `commit:${param}` : `branch:${param}`;
}

/**
 * The candidate an id names: the listed one, else one read off the id itself,
 * so a linked change no longer in the list (a merged branch, an older commit)
 * keeps its name and its diff command.
 */
export function changeFor(id: string, listed: ChangeCandidate[]): ChangeCandidate | undefined {
  const found = listed.find((c) => c.id === id);
  if (found) return found;
  const base = { title: null, author: null, committed_at: null, ahead: null, files_changed: null, is_current: false };
  if (id === "working-tree") return { ...base, id, kind: "working_tree", label: "Uncommitted changes", head_sha: null, base: null };
  const [kind, ...rest] = id.split(":");
  const ref = rest.join(":");
  if (kind === "branch") {
    const trunk = listed.find((c) => c.kind === "branch")?.base ?? null;
    return { ...base, id, kind: "branch", label: ref, head_sha: null, base: trunk };
  }
  if (kind === "commit") return { ...base, id, kind: "commit", label: ref.slice(0, 8), head_sha: ref, base: null };
  return undefined;
}


/** The hand-picked-files form: search, paste, hotspot chips. */
function FilesForm({
  adapter,
  initial,
  busy,
  onAnalyze,
}: {
  adapter: ImpactAdapter;
  initial: string[];
  busy: boolean;
  onAnalyze: (files: string[]) => void;
}) {
  const [selected, setSelected] = useState<string[]>(initial);
  const { data: hotspots } = useSWR(
    `blast-radius-suggestions:${adapter.cacheKey}`,
    () => adapter.listHotspots(8),
    { revalidateOnFocus: false },
  );
  const labels = useMemo(
    () => disambiguateBasenames((hotspots ?? []).map((h) => h.file_path)),
    [hotspots],
  );
  const add = (paths: string[]) =>
    setSelected((prev) => [...new Set([...prev, ...paths.map((p) => p.trim()).filter(Boolean)])]);

  return (
    <div className="space-y-3">
      <FilePathPicker
        selected={selected}
        onAdd={add}
        onRemove={(p) => setSelected((prev) => prev.filter((x) => x !== p))}
        onSearch={(q) => adapter.searchFiles(q)}
      />
      {hotspots && hotspots.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {hotspots.map((h) => (
            <button
              key={h.file_path}
              type="button"
              onClick={() => add([h.file_path])}
              className="inline-flex items-center gap-1 rounded-full border border-[var(--color-border-default)] px-2 py-0.5 font-mono text-[11px] text-[var(--color-text-secondary)] transition-colors hover:text-[var(--color-text-primary)]"
              aria-label={`Add hotspot ${h.file_path}`}
            >
              <Flame className="h-3 w-3" aria-hidden />
              <FilePath path={h.file_path} mode="distinguishing" labels={labels} className="max-w-[180px]" />
              <Plus className="h-3 w-3 opacity-60" aria-hidden />
            </button>
          ))}
        </div>
      )}
      <Button size="sm" disabled={busy || selected.length === 0} onClick={() => onAnalyze(selected)}>
        {busy ? "Analyzing..." : "Analyze these files"}
      </Button>
    </div>
  );
}

export function ImpactView({
  adapter,
  initialFiles,
  initialChange,
  onChangePicked,
}: {
  adapter: ImpactAdapter;
  /**
   * Files to analyze on arrival: the file card's "Blast Radius" button and
   * the symbol drawer's CTA both link here with the path already chosen.
   */
  initialFiles?: string[];
  /** A shared link's change (`wip`, a branch, a full sha); files win when both are given. */
  initialChange?: string | undefined;
  /** Called with the link value when the reader picks a change, so the host can put it in the URL. */
  onChangePicked?: ((linkParam: string) => void) | undefined;
}) {
  const listsChanges = Boolean(adapter.listChanges && adapter.analyzeChange);
  const { data: changes, error: changesError, isLoading: changesLoading } = useSWR(
    listsChanges ? `blast-radius-changes:${adapter.cacheKey}` : null,
    () => adapter.listChanges!(),
    { revalidateOnFocus: false },
  );

  const seed = (initialFiles ?? []).filter(Boolean);
  const seedKey = seed.join("\n");
  const [selection, setSelection] = useState<Selection | null>(() => {
    if (seed.length > 0) return { kind: "files", files: seed };
    if (initialChange && listsChanges) return { kind: "change", id: changeIdFromLink(initialChange) };
    return null;
  });
  const [filesOpen, setFilesOpen] = useState(!listsChanges || seed.length > 0);
  const [maxDepth, setMaxDepth] = useState(3);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const request = useRef(0);
  // Health checks by change id: run on demand, kept for the page's life.
  const [checks, setChecks] = useState<Record<string, HealthCheckState>>({});
  const runCheck = useCallback(async (changeId: string) => {
    const check = adapterRef.current.checkHealth;
    if (!check) return;
    setChecks((prev) => ({ ...prev, [changeId]: { status: "running" } }));
    try {
      const result = await check(changeId);
      setChecks((prev) => ({ ...prev, [changeId]: { status: "done", result } }));
    } catch (err) {
      setChecks((prev) => ({
        ...prev,
        [changeId]: { status: "error", message: toFriendlyMessage(err, "The health check could not run.") },
      }));
    }
  }, []);
  // Read through a ref: a host that rebuilds its adapter each render must not
  // re-run the analysis each render.
  const adapterRef = useRef(adapter);
  adapterRef.current = adapter;

  // A new file seed (following another file's CTA) replaces the selection.
  useEffect(() => {
    if (seedKey) setSelection({ kind: "files", files: seedKey.split("\n") });
  }, [seedKey]);

  // Open the most relevant change once the list arrives, unless a file seed
  // or an earlier click already chose something.
  useEffect(() => {
    if (!selection && changes?.default_id) setSelection({ kind: "change", id: changes.default_id });
  }, [changes, selection]);

  const run = useCallback(
    async (sel: Selection, depth: number) => {
      const ticket = ++request.current;
      setLoading(true);
      setError(null);
      try {
        let next: Analysis;
        if (sel.kind === "change") {
          const r = await adapterRef.current.analyzeChange!({ changeId: sel.id, maxDepth: depth });
          next = { selection: sel, result: r, changedFiles: r.changed_files, changedTotal: r.changed_files_total };
        } else {
          const r = await adapterRef.current.analyze({ changedFiles: sel.files, maxDepth: depth });
          next = { selection: sel, result: r, changedFiles: sel.files };
        }
        // A slower answer for an earlier pick never replaces the current one.
        if (ticket === request.current) setAnalysis(next);
      } catch (err) {
        if (ticket === request.current) {
          setAnalysis(null);
          setError(toFriendlyMessage(err, "Analysis failed."));
        }
      } finally {
        if (ticket === request.current) setLoading(false);
      }
    },
    [],
  );

  const selectionKey = selection
    ? selection.kind === "change"
      ? `change:${selection.id}`
      : `files:${selection.files.join("\n")}`
    : "";
  useEffect(() => {
    if (selection) void run(selection, maxDepth);
    // Keyed by what was picked and the depth; `selection` itself is rebuilt per pick.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectionKey, maxDepth, run]);

  const analyzed = analysis?.selection;
  const selectedChange =
    analyzed?.kind === "change" ? changeFor(analyzed.id, changes?.changes ?? []) : undefined;
  const subject = selectedChange ? (
    <span className={selectedChange.kind === "working_tree" ? "font-medium" : "font-mono"}>
      {selectedChange.label}
    </span>
  ) : (
    "This set of files"
  );
  const checkedChange = selectedChange ? checks[selectedChange.id] : undefined;
  const fileHref = adapter.fileHref ? (p: string) => adapter.fileHref!(p) : undefined;

  return (
    <div className="grid gap-x-8 gap-y-6 lg:grid-cols-[minmax(220px,280px)_minmax(0,1fr)]">
      {/* Below lg the list stacks above the result, so it scrolls in a short box. */}
      <aside className="max-h-72 min-w-0 space-y-5 overflow-y-auto pr-1 lg:max-h-[calc(100vh-8rem)]">
        {listsChanges && (
          <>
            {changesLoading && (
              <div className="space-y-2">
                {Array.from({ length: 5 }).map((_, i) => (
                  <Skeleton key={i} className="h-11 w-full" />
                ))}
              </div>
            )}
            {changesError && (
              <p className="text-xs text-[var(--color-error)]">
                {toFriendlyMessage(changesError, "Could not list changes.")}
              </p>
            )}
            {changes && (
              <ChangePicker
                data={changes}
                selectedId={selection?.kind === "change" ? selection.id : null}
                onSelect={(id) => {
                  setSelection({ kind: "change", id });
                  onChangePicked?.(changeLinkParam(id));
                }}
              />
            )}
          </>
        )}
        <div
          className={
            listsChanges ? "space-y-2 border-t border-[var(--color-border-default)] pt-4" : "space-y-2"
          }
        >
          {listsChanges ? (
            <button
              type="button"
              aria-expanded={filesOpen}
              onClick={() => setFilesOpen((v) => !v)}
              className={`${MICRO} hover:text-[var(--color-text-primary)]`}
            >
              {filesOpen ? "▾" : "▸"} Or pick files
            </button>
          ) : (
            <p className={MICRO}>Changed files</p>
          )}
          {filesOpen && (
            <FilesForm
              key={seedKey}
              adapter={adapter}
              initial={selection?.kind === "files" ? selection.files : seed}
              busy={loading}
              onAnalyze={(files) => setSelection({ kind: "files", files })}
            />
          )}
        </div>
      </aside>

      <section className="min-w-0 space-y-6" aria-live="polite" aria-busy={loading}>
        {!selection && !changesLoading && (
          <EmptyState
            title="Pick a change"
            description={
              listsChanges
                ? "There is no uncommitted work, no branch ahead of the default branch, and no indexed commit yet. Pick files to see what depends on them."
                : "Search or paste the files a change touches to see what depends on them, what usually changes with them, and who should review."
            }
          />
        )}
        {error && <p className="text-xs text-[var(--color-error)]">{error}</p>}
        {loading && !analysis && (
          <div className="space-y-4">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-[360px] w-full" />
          </div>
        )}
        {analysis && (
          <div className={loading ? "space-y-6 opacity-60 transition-opacity" : "space-y-6"}>
            <BlastRadiusVerdict
              result={analysis.result}
              changedFiles={analysis.changedFiles}
              changedTotal={analysis.changedTotal}
              subject={subject}
              change={selectedChange}
              healthCheck={checkedChange?.status === "done" ? checkedChange.result : undefined}
              fileHref={fileHref}
            />
            {/* The depth control stays when nothing is reached: a deeper look may find some. */}
              <div className="space-y-2 border-t border-[var(--color-border-default)] pt-5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className={MICRO}>Blast radius</p>
                  <label className="flex items-center gap-2 text-xs text-[var(--color-text-secondary)]">
                    Follow imports up to
                    <select
                      value={maxDepth}
                      onChange={(e) => setMaxDepth(Number(e.target.value))}
                      className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-1.5 py-0.5 text-xs text-[var(--color-text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-focus-ring)]"
                    >
                      {DEPTHS.map((d) => (
                        <option key={d} value={d}>
                          {d} hop{d === 1 ? "" : "s"}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                {analysis.result.transitive_affected.length > 0 ? (
                  <BlastRadiusMap result={analysis.result} changedFiles={analysis.changedFiles} fileHref={fileHref} />
                ) : (
                  <p className="text-xs text-[var(--color-text-secondary)]">
                    No indexed file imports the changed files within {maxDepth} hop{maxDepth === 1 ? "" : "s"}.
                  </p>
                )}
              </div>
            {selectedChange && adapter.checkHealth && changes?.working_tree_supported && (
              <HealthCheckPanel
                key={selectedChange.id}
                state={checks[selectedChange.id]}
                onRun={() => void runCheck(selectedChange.id)}
                fileHref={fileHref}
              />
            )}
            <BlastRadiusResults
              result={analysis.result}
              changedFiles={analysis.changedFiles}
              reviewersSlot={adapter.renderReviewers?.(analysis.changedFiles)}
              detailsOnly
              fileHref={fileHref}
            />
          </div>
        )}
      </section>
    </div>
  );
}
