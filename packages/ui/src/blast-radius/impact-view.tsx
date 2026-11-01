"use client";

/**
 * Impact view: pick a change, see how far it reaches and what to do about it.
 *
 * With a host that lists changes (`listChanges` + `analyzeChange`), the view
 * opens the most relevant change on arrival (work in progress, else the
 * checked-out branch) and names it in one compact picker at the top. Picking
 * files by hand stays available as a secondary path, and is the only path for
 * a host that does not list changes. Every result reads the same way: a
 * status line with the actions; what to look at, test and ask; where the
 * change reaches; every file; then the details.
 *
 * Presentation and orchestration only: the host injects fetching, analysis,
 * file links and the reviewer slot through an {@link ImpactAdapter}, so web
 * and hosted render the same view from one source.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import useSWR from "swr";
import { ChevronRight, Flame, Plus } from "lucide-react";
import type {
  AffectedSignal,
  BlastRadiusResponse,
  ChangeCandidate,
  ChangeCandidatesResponse,
} from "@repowise-dev/types/blast-radius";

import { Button } from "../ui/button";
import { Skeleton } from "../ui/skeleton";
import { Spinner } from "../ui/spinner";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../ui/select";
import { FilePathPicker } from "../health/file-path-picker";
import { ReviewDetails } from "./review-details";
import { BlastRadiusSteps } from "./blast-radius-steps";
import { BlastRadiusVerdict } from "./blast-radius-verdict";
import { AffectedByModule, ChangedFilesList, type DependentsFocus } from "./affected-files";
import { readReach, type Reach } from "./blast-map-model";
import {
  blockFocusPaths,
  buildStepMap,
  defaultStepBlock,
  stepWords,
  type StepBand,
  type StepBlock,
  type StepMap,
} from "./blast-step-model";
import { ChangePicker, changeName } from "./change-picker";
import { HealthCheckButton, HealthCheckPanel, type HealthCheckState } from "./health-check-panel";
import { count } from "./review-status";
import type { ImpactAdapter } from "./impact-adapter";
import { changeIdFromLink, changeLinkParam } from "./change-links";
import { toFriendlyMessage } from "../lib/errors";
import { disambiguateBasenames } from "../lib/format";
import { FilePath } from "../shared/file-path";
import { EmptyState } from "../shared/empty-state";

type Selection = { kind: "change"; id: string } | { kind: "files"; files: string[] };

interface Analysis {
  /** What was analyzed, so the verdict never names a newer pick than its numbers. */
  selection: Selection;
  result: BlastRadiusResponse & { affected_signals?: AffectedSignal[] };
  changedFiles: string[];
  changedTotal?: number;
}

const DEPTHS: { value: number; label: string }[] = [
  { value: 1, label: "Direct importers only" },
  { value: 2, label: "Up to 2 steps" },
  { value: 3, label: "Up to 3 steps" },
  { value: 4, label: "Up to 4 steps" },
  { value: 5, label: "Up to 5 steps" },
];

export { changeIdFromLink, changeLinkParam } from "./change-links";

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
  if (kind === "pr" && /^\d+$/.test(ref))
    return { ...base, id, kind: "pull_request", label: `#${ref}`, number: Number(ref), head_sha: null, base: null };
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
      <Button size="sm" variant="outline" className="max-sm:h-10" disabled={busy || selected.length === 0} onClick={() => onAnalyze(selected)}>
        {busy ? "Analyzing..." : "Analyze these files"}
      </Button>
    </div>
  );
}

function Section({ title, hint, action, children }: { title: string; hint?: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="space-y-3 border-t border-[var(--color-border-default)] pt-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="flex items-baseline gap-2 text-[15px] font-medium text-[var(--color-text-primary)]">
          {title}
          {hint && <span className="text-xs font-normal tabular-nums text-[var(--color-text-tertiary)]">{hint}</span>}
        </h3>
        {action}
      </div>
      {children}
    </section>
  );
}

function DepthSelect({ value, onChange }: { value: number; onChange: (depth: number) => void }) {
  return (
    <div className="flex items-center gap-2 text-xs text-[var(--color-text-secondary)]">
      <span id="blast-depth-label">How far to look</span>
      <Select value={String(value)} onValueChange={(v) => onChange(Number(v))}>
        <SelectTrigger aria-labelledby="blast-depth-label" className="h-8 w-auto gap-2 px-2.5 text-xs max-sm:h-10">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {DEPTHS.map((d) => (
            <SelectItem key={d.value} value={String(d.value)} className="text-xs">
              {d.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

/** No source file imports the change: one sentence, with the tests that do counted apart. */
function NoReach({ depth, tests }: { depth: number; tests: number }) {
  const within = depth === 1 ? "directly" : `within ${depth} steps`;
  const rest =
    tests > 0
      ? `; only ${count(tests, "test file")} ${tests === 1 ? "does" : "do"}, listed under Run these tests.`
      : ".";
  return (
    <p className="text-xs text-[var(--color-text-secondary)]">
      No source file imports the changed files {within}
      {rest}
    </p>
  );
}

/**
 * Every changed file, folded by default: the risky ones already lead under
 * Look at first, and the page stays short until the reader asks for the rest.
 */
function ChangedFilesSection({ total, children }: { total: number; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <section className="space-y-3 border-t border-[var(--color-border-default)] pt-5">
      <h3>
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="flex items-baseline gap-2 rounded-sm text-[15px] font-medium text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)] max-sm:min-h-10 max-sm:items-center"
        >
          <ChevronRight
            className={`h-3.5 w-3.5 self-center text-[var(--color-text-tertiary)] transition-transform ${open ? "rotate-90" : ""}`}
            aria-hidden
          />
          Changed files
          <span className="text-xs font-normal tabular-nums text-[var(--color-text-tertiary)]">{total.toLocaleString()}</span>
        </button>
      </h3>
      {open && children}
    </section>
  );
}

/** The health check for one change: its state and how to run it. */
interface ChangeCheck {
  state: HealthCheckState | undefined;
  onRun: () => void;
}

/** What a picked block narrows the dependents list to, and how the list names it. */
function stepFocus(block: StepBlock, reach: Reach): DependentsFocus {
  const label =
    block.column === 0 ? `what ${block.label} reaches directly` : `${block.label}, ${stepWords(block.column)} away`;
  return { label, paths: blockFocusPaths(block, reach) };
}

/** A picked band narrows the list to the files it pulls in. */
function bandFocus(band: StepBand, map: { blocks: StepBlock[] }): DependentsFocus {
  const name = (key: string) => map.blocks.find((b) => b.key === key)?.label ?? key;
  return { label: `reached through ${name(band.from)} to ${name(band.to)}`, paths: new Set(band.paths) };
}

/** Steps whose "N more folders" the reader opened, and how to open or fold one. */
function useExpandedColumns() {
  const [expanded, setExpanded] = useState<ReadonlySet<number>>(() => new Set());
  const toggle = useCallback(
    (column: number) =>
      setExpanded((prev) => {
        const next = new Set(prev);
        if (next.has(column)) next.delete(column);
        else next.add(column);
        return next;
      }),
    [],
  );
  return { expanded, toggle };
}

/** The list row under the pointer, read back as the block it sits in. */
function useListHighlight(steps: StepMap) {
  const [hoverPath, setHoverPath] = useState<string | null>(null);
  const blockByPath = useMemo(() => {
    const byPath = new Map<string, string>();
    for (const b of steps.blocks) for (const p of b.paths) byPath.set(p, b.key);
    return byPath;
  }, [steps]);
  return { highlightKey: hoverPath ? (blockByPath.get(hoverPath) ?? null) : null, setHoverPath };
}

/**
 * What the drawing narrows the dependents list to: a picked block (by
 * default the step-1 folder with the most poor-health files) or a picked
 * band, never both. A rebuilt map that drops either shows every file.
 */
function useStepSelection(steps: StepMap, reach: Reach) {
  const [pickedKey, setPickedKey] = useState<string | null>(() => defaultStepBlock(steps)?.key ?? null);
  const [bandKey, setBandKey] = useState<string | null>(null);
  const band = bandKey ? steps.bands.find((b) => b.key === bandKey) : undefined;
  // A band gone from the rebuilt map stays gone, rather than relighting if the map flips back.
  useEffect(() => {
    if (bandKey && !band) setBandKey(null);
  }, [bandKey, band]);
  const selected = pickedKey === null ? undefined : steps.blocks.find((b) => b.key === pickedKey);
  const selectedKey = band ? null : (selected?.key ?? null);
  const focus = useMemo(
    () => (band ? bandFocus(band, steps) : selected ? stepFocus(selected, reach) : undefined),
    [band, steps, selected, reach],
  );
  return {
    selectedKey,
    bandKey: band?.key ?? null,
    focus,
    // Picking the picked block again shows every file.
    pickBlock: (b: StepBlock) => {
      setBandKey(null);
      setPickedKey(selectedKey === b.key ? null : b.key);
    },
    pickBand: (b: StepBand) => setBandKey(bandKey === b.key ? null : b.key),
    clear: () => {
      setPickedKey(null);
      setBandKey(null);
    },
  };
}

function DependentsSection({
  reach,
  fileHref,
  focus,
  onClearFocus,
  onHoverPath,
}: {
  reach: Reach;
  fileHref: ((path: string) => string) | undefined;
  focus: DependentsFocus | undefined;
  onClearFocus: () => void;
  onHoverPath: (path: string | null) => void;
}) {
  const [filter, setFilter] = useState("");
  return (
    <Section title="Files that depend on this" hint={reach.files.length.toLocaleString()}>
      <AffectedByModule
        reach={reach}
        fileHref={fileHref}
        query={filter}
        onQueryChange={setFilter}
        focus={focus}
        onClearFocus={onClearFocus}
        onHoverPath={onHoverPath}
      />
      {reach.tests.length > 0 && (
        <p className="text-xs text-[var(--color-text-tertiary)]">
          Not counted here: {count(reach.tests.length, "test file")} that import the change. They are tests to run.
        </p>
      )}
    </Section>
  );
}

function ReviewBody({
  analysis,
  change,
  check,
  fileHref,
  maxDepth,
  onDepth,
}: {
  analysis: Analysis;
  change: ChangeCandidate | undefined;
  check: ChangeCheck | undefined;
  fileHref: ((path: string) => string) | undefined;
  maxDepth: number;
  onDepth: (depth: number) => void;
}) {
  const { result, changedFiles, changedTotal } = analysis;
  const reach = useMemo(() => readReach(result, changedFiles), [result, changedFiles]);
  const columns = useExpandedColumns();
  const steps = useMemo(() => buildStepMap(reach, columns.expanded), [reach, columns.expanded]);
  const pick = useStepSelection(steps, reach);
  const { highlightKey, setHoverPath } = useListHighlight(steps);
  const reaches = reach.files.length > 0;
  return (
    <>
      <BlastRadiusVerdict
        result={result}
        reach={reach}
        changedFiles={changedFiles}
        changedTotal={changedTotal}
        change={change}
        healthCheck={check?.state?.status === "done" ? check.state.result : undefined}
        extraActions={check && <HealthCheckButton state={check.state} onRun={check.onRun} />}
        fileHref={fileHref}
      />
      {check && <HealthCheckPanel state={check.state} fileHref={fileHref} />}
      <Section title="Where it reaches" action={<DepthSelect value={maxDepth} onChange={onDepth} />}>
        {reaches ? (
          <BlastRadiusSteps
            map={steps}
            selectedKey={pick.selectedKey}
            selectedBand={pick.bandKey}
            highlightKey={highlightKey}
            onSelect={pick.pickBlock}
            onSelectBand={pick.pickBand}
            onToggleColumn={columns.toggle}
            onClear={pick.clear}
          />
        ) : (
          <NoReach depth={maxDepth} tests={reach.tests.length} />
        )}
      </Section>
      {reaches && (
        <DependentsSection
          reach={reach}
          fileHref={fileHref}
          focus={pick.focus}
          onClearFocus={pick.clear}
          onHoverPath={setHoverPath}
        />
      )}
      <ChangedFilesSection total={changedTotal ?? changedFiles.length}>
        <ChangedFilesList result={result} reach={reach} fileHref={fileHref} />
      </ChangedFilesSection>
      <ReviewDetails result={result} reach={reach} fileHref={fileHref} />
    </>
  );
}

function AnalyzingNote({ name, files }: { name: string; files: number | null }) {
  return (
    <p className="flex min-w-0 items-center gap-2 text-xs text-[var(--color-text-secondary)]" role="status">
      <Spinner size="xs" />
      <span className="min-w-0 truncate">
        Reading what depends on {name}
        {files != null ? ` (${count(files, "file")})` : ""}...
      </span>
    </p>
  );
}

const QUIET_BUTTON =
  "rounded-sm text-xs max-sm:min-h-10 text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]";

function selectionKeyOf(selection: Selection | null): string {
  if (!selection) return "";
  return selection.kind === "change" ? `change:${selection.id}` : `files:${selection.files.join("\n")}`;
}

async function analyzeSelection(adapter: ImpactAdapter, sel: Selection, depth: number): Promise<Analysis> {
  if (sel.kind === "change") {
    const r = await adapter.analyzeChange!({ changeId: sel.id, maxDepth: depth });
    return { selection: sel, result: r, changedFiles: r.changed_files, changedTotal: r.changed_files_total };
  }
  const r = await adapter.analyze({ changedFiles: sel.files, maxDepth: depth });
  return { selection: sel, result: r, changedFiles: sel.files };
}

/** Answers kept per page, and how long one for a moving change (work in progress, a branch, a PR) stays good. */
const KEPT_ANALYSES = 20;
const MOVING_MS = 60_000;

/** A commit or a hand-picked file set never changes under the reader; anything else can. */
function keptIsFresh(sel: Selection, at: number): boolean {
  if (sel.kind === "files" || sel.id.startsWith("commit:")) return true;
  return Date.now() - at < MOVING_MS;
}

/** The analysis of the current pick at the current depth; a slower answer for an earlier pick never lands. */
function useAnalysis(adapter: ImpactAdapter, selection: Selection | null, maxDepth: number) {
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [error, setError] = useState<AnalysisError | null>(null);
  // Answers already read on this page, so going back to a change is instant.
  const seen = useRef(new Map<string, { at: number; analysis: Analysis }>());
  const [loading, setLoading] = useState(false);
  const request = useRef(0);
  // Read through a ref: a host that rebuilds its adapter each render must not
  // re-run the analysis each render.
  const adapterRef = useRef(adapter);
  adapterRef.current = adapter;

  const run = useCallback(async (sel: Selection, depth: number, fresh = false) => {
    const ticket = ++request.current;
    const current = () => ticket === request.current;
    const cacheKey = `${selectionKeyOf(sel)}@${depth}`;
    const kept = fresh ? undefined : seen.current.get(cacheKey);
    setError(null);
    if (kept && keptIsFresh(sel, kept.at)) {
      setLoading(false);
      setAnalysis(kept.analysis);
      return;
    }
    setLoading(true);
    try {
      const next = await analyzeSelection(adapterRef.current, sel, depth);
      seen.current.delete(cacheKey);
      seen.current.set(cacheKey, { at: Date.now(), analysis: next });
      // Oldest first in a Map, so the first key is the one to drop.
      if (seen.current.size > KEPT_ANALYSES) seen.current.delete(seen.current.keys().next().value!);
      if (current()) setAnalysis(next);
    } catch (err) {
      if (current()) {
        setAnalysis(null);
        setError(analysisError(err));
      }
    } finally {
      if (current()) setLoading(false);
    }
  }, []);

  const key = selectionKeyOf(selection);
  useEffect(() => {
    if (selection) void run(selection, maxDepth);
    // Keyed by what was picked and the depth; `selection` itself is rebuilt per pick.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, maxDepth, run]);
  const retry = useCallback(() => {
    if (selection) void run(selection, maxDepth, true);
  }, [selection, maxDepth, run]);
  return { analysis, error, loading, retry };
}

interface AnalysisError {
  message: string;
  /** The change is gone (merged, deleted, never indexed): retrying will not bring it back. */
  missing: boolean;
}

/** A failure in words a reader can act on; a missing change says so instead of printing the API's 404. */
function analysisError(err: unknown): AnalysisError {
  const status = (err as { status?: unknown } | null)?.status;
  const raw = toFriendlyMessage(err, "");
  const missing = status === 404 || /\b404\b|unknown (branch|commit|change)/i.test(raw);
  if (missing)
    return {
      missing,
      message:
        "That change is not in this repository any more: it may be merged, deleted or not indexed yet. Pick another above.",
    };
  return { missing, message: raw.replace(/^API error \d+:\s*/i, "") || "The analysis could not run." };
}

function AnalysisFailed({ error, onRetry }: { error: AnalysisError; onRetry: () => void }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs" role="alert">
      <span className="text-[var(--color-text-secondary)]">
        <span className="mr-1.5 inline-block h-2 w-2 rounded-full bg-[var(--color-error)]" aria-hidden />
        {error.message}
      </span>
      {!error.missing && (
        <button type="button" onClick={onRetry} className={QUIET_BUTTON}>
          Try again
        </button>
      )}
    </div>
  );
}

/** The page's shape while the first answer is read, so nothing jumps when it lands. */
function ReviewSkeleton() {
  return (
    <div className="space-y-6" aria-hidden>
      <div className="space-y-3">
        <Skeleton className="h-5 w-72 max-w-full" />
        <div className="flex flex-wrap gap-6">
          <Skeleton className="h-4 w-28" />
          <Skeleton className="h-4 w-56" />
          <Skeleton className="h-4 w-24" />
        </div>
        <div className="flex gap-2">
          <Skeleton className="h-7 w-40 max-sm:h-10" />
          <Skeleton className="h-7 w-36 max-sm:h-10" />
        </div>
      </div>
      <div className="grid gap-x-10 gap-y-6 border-t border-[var(--color-border-default)] pt-5 xl:grid-cols-2">
        {[0, 1].map((col) => (
          <div key={col} className="space-y-2.5">
            <Skeleton className="h-4 w-32" />
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-3.5 w-full" />
            ))}
          </div>
        ))}
      </div>
      <div className="space-y-3 border-t border-[var(--color-border-default)] pt-5">
        <Skeleton className="h-4 w-36" />
        <div className="grid grid-cols-4 gap-6">
          {[0, 1, 2, 3].map((c) => (
            <div key={c} className="space-y-3">
              {Array.from({ length: c === 0 ? 3 : 4 }).map((_, i) => (
                <Skeleton key={i} className="h-6 w-full" />
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/** Health checks by change id: run on demand, kept for the page's life. */
function useHealthChecks(adapter: ImpactAdapter) {
  const [checks, setChecks] = useState<Record<string, HealthCheckState>>({});
  const adapterRef = useRef(adapter);
  adapterRef.current = adapter;
  const run = useCallback(async (changeId: string) => {
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
  return { checks, run };
}

function ReviewHeader({
  listsChanges,
  changes,
  changesLoading,
  picked,
  filesName,
  filesOpen,
  onToggleFiles,
  onPick,
}: {
  listsChanges: boolean;
  changes: ChangeCandidatesResponse | undefined;
  changesLoading: boolean;
  picked: ChangeCandidate | undefined;
  filesName: string;
  filesOpen: boolean;
  onToggleFiles: () => void;
  onPick: (id: string) => void;
}) {
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
      <span className="text-[15px] text-[var(--color-text-secondary)]">Reviewing</span>
      {(filesName || !listsChanges) && (
        <span className="text-[15px] font-medium text-[var(--color-text-primary)]">{filesName || "the files you pick"}</span>
      )}
      {listsChanges && <ChangePicker data={changes} loading={changesLoading} selected={picked} onSelect={onPick} />}
      {listsChanges && (
        <button type="button" aria-expanded={filesOpen} onClick={onToggleFiles} className={QUIET_BUTTON}>
          {filesOpen ? "Hide the file picker" : "Pick files instead"}
        </button>
      )}
    </div>
  );
}

function ResultPane({
  state,
  pickedName,
  pickedFiles,
  empty,
  shownChange,
  check,
  fileHref,
  maxDepth,
  onDepth,
}: {
  state: ReturnType<typeof useAnalysis>;
  pickedName: string;
  /** The picked change's file count from the listing, shown while its analysis is read. */
  pickedFiles: number | null;
  empty: ReactNode;
  shownChange: ChangeCandidate | undefined;
  check: ChangeCheck | undefined;
  fileHref: ((path: string) => string) | undefined;
  maxDepth: number;
  onDepth: (depth: number) => void;
}) {
  const { analysis, error, loading, retry } = state;
  return (
    <section className="min-w-0 space-y-6" aria-live="polite">
      {empty}
      {error && <AnalysisFailed error={error} onRetry={retry} />}
      {loading && pickedName && <AnalyzingNote name={pickedName} files={pickedFiles} />}
      {loading && !analysis && <ReviewSkeleton />}
      {analysis && (
        <div className={loading ? "space-y-6 opacity-60 transition-opacity" : "space-y-6"}>
          <ReviewBody
            // A new pick starts with no module filter left over from the last one.
            key={selectionKeyOf(analysis.selection)}
            analysis={analysis}
            change={shownChange}
            check={check}
            fileHref={fileHref}
            maxDepth={maxDepth}
            onDepth={onDepth}
          />
        </div>
      )}
    </section>
  );
}

const EMPTY_WITH_LIST =
  "There is no uncommitted work, no branch ahead of the default branch, and no indexed commit yet. Pick files to see what depends on them.";
const EMPTY_WITHOUT_LIST =
  "Search or paste the files a change touches to see what depends on them, what usually changes with them, and who should review.";

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
  const state = useAnalysis(adapter, selection, maxDepth);
  const health = useHealthChecks(adapter);

  // A new file seed (following another file's CTA) replaces the selection.
  useEffect(() => {
    if (seedKey) setSelection({ kind: "files", files: seedKey.split("\n") });
  }, [seedKey]);

  // Open the most relevant change once the list arrives, unless a file seed
  // or an earlier click already chose something.
  useEffect(() => {
    if (!selection && changes?.default_id) setSelection({ kind: "change", id: changes.default_id });
  }, [changes, selection]);

  const listed = changes?.changes ?? [];
  const picked = selection?.kind === "change" ? changeFor(selection.id, listed) : undefined;
  // The change the numbers on screen belong to, so they never wear a newer pick's name.
  const analyzed = state.analysis?.selection;
  const shownChange = analyzed?.kind === "change" ? changeFor(analyzed.id, listed) : undefined;
  const filesName = selection?.kind === "files" ? count(selection.files.length, "picked file") : "";
  // Offered only where the host can read both sides of a change (an older server reports a checkout instead).
  const canCheck = changes?.health_check_supported ?? changes?.working_tree_supported ?? false;
  const check: ChangeCheck | undefined =
    shownChange && adapter.checkHealth && canCheck
      ? { state: health.checks[shownChange.id], onRun: () => void health.run(shownChange.id) }
      : undefined;
  const fileHref = adapter.fileHref ? (p: string) => adapter.fileHref!(p) : undefined;
  const pickChange = (id: string) => {
    setSelection({ kind: "change", id });
    setFilesOpen(false);
    onChangePicked?.(changeLinkParam(id));
  };
  const empty = !selection && !changesLoading && (
    <EmptyState title="Pick a change" description={listsChanges ? EMPTY_WITH_LIST : EMPTY_WITHOUT_LIST} />
  );

  return (
    // Bottom room so the floating chat launcher never covers the last row.
    <div className="min-w-0 space-y-6 pb-24" aria-busy={state.loading}>
      <ReviewHeader
        listsChanges={listsChanges}
        changes={changes}
        changesLoading={changesLoading}
        picked={picked}
        filesName={filesName}
        filesOpen={filesOpen}
        onToggleFiles={() => setFilesOpen((v) => !v)}
        onPick={pickChange}
      />
      {changesError && (
        <p className="text-xs text-[var(--color-error)]">{toFriendlyMessage(changesError, "Could not list changes.")}</p>
      )}
      {filesOpen && (
        <FilesForm
          key={seedKey}
          adapter={adapter}
          initial={selection?.kind === "files" ? selection.files : seed}
          busy={state.loading}
          onAnalyze={(files) => setSelection({ kind: "files", files })}
        />
      )}
      <ResultPane
        state={state}
        pickedName={picked ? changeName(picked) : filesName}
        pickedFiles={picked ? picked.files_changed : selection?.kind === "files" ? selection.files.length : null}
        empty={empty}
        shownChange={shownChange}
        check={check}
        fileHref={fileHref}
        maxDepth={maxDepth}
        onDepth={setMaxDepth}
      />
    </div>
  );
}
