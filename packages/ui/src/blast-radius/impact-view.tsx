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
import { Plus, Flame } from "lucide-react";
import type {
  AffectedSignal,
  BlastRadiusResponse,
  ChangeCandidate,
  ChangeCandidatesResponse,
} from "@repowise-dev/types/blast-radius";

import { Button } from "../ui/button";
import { Skeleton } from "../ui/skeleton";
import { Spinner } from "../ui/spinner";
import { FilePathPicker } from "../health/file-path-picker";
import { ReviewDetails } from "./review-details";
import { BlastRadiusMap } from "./blast-radius-map";
import { BlastRadiusVerdict } from "./blast-radius-verdict";
import { AffectedByModule, ChangedFilesList } from "./affected-files";
import { readReach } from "./blast-map-model";
import { ChangePicker, changeName } from "./change-picker";
import { count } from "./review-status";
import type { ImpactAdapter } from "./impact-adapter";
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
      <Button size="sm" variant="outline" disabled={busy || selected.length === 0} onClick={() => onAnalyze(selected)}>
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
    <label className="flex items-center gap-2 text-xs text-[var(--color-text-secondary)]">
      How far to look
      <select
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-1.5 py-0.5 text-xs text-[var(--color-text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-focus-ring)]"
      >
        {DEPTHS.map((d) => (
          <option key={d.value} value={d.value}>
            {d.label}
          </option>
        ))}
      </select>
    </label>
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

function ReviewBody({
  analysis,
  change,
  fileHref,
  maxDepth,
  onDepth,
}: {
  analysis: Analysis;
  change: ChangeCandidate | undefined;
  fileHref: ((path: string) => string) | undefined;
  maxDepth: number;
  onDepth: (depth: number) => void;
}) {
  const { result, changedFiles, changedTotal } = analysis;
  const reach = useMemo(() => readReach(result, changedFiles), [result, changedFiles]);
  const [filter, setFilter] = useState("");
  const dependents = useRef<HTMLDivElement>(null);
  const pickModule = (name: string | null) => {
    setFilter(name ?? "");
    if (name) dependents.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  };
  return (
    <>
      <BlastRadiusVerdict
        result={result}
        reach={reach}
        changedFiles={changedFiles}
        changedTotal={changedTotal}
        change={change}
        fileHref={fileHref}
      />
      <Section title="Where it reaches" action={<DepthSelect value={maxDepth} onChange={onDepth} />}>
        {reach.files.length > 0 ? (
          <BlastRadiusMap reach={reach} fileHref={fileHref} selectedModule={filter || null} onSelectModule={pickModule} />
        ) : (
          <NoReach depth={maxDepth} tests={reach.tests.length} />
        )}
      </Section>
      <Section title="Changed files" hint={(changedTotal ?? changedFiles.length).toLocaleString()}>
        <ChangedFilesList result={result} reach={reach} fileHref={fileHref} />
      </Section>
      {reach.files.length > 0 && (
        <div ref={dependents} className="scroll-mt-4">
          <Section title="Files that depend on this" hint={reach.files.length.toLocaleString()}>
            <AffectedByModule reach={reach} fileHref={fileHref} query={filter} onQueryChange={setFilter} />
            {reach.tests.length > 0 && (
              <p className="text-xs text-[var(--color-text-tertiary)]">
                Not counted here: {count(reach.tests.length, "test file")} that import the change. They are tests to run.
              </p>
            )}
          </Section>
        </div>
      )}
      <ReviewDetails result={result} reach={reach} fileHref={fileHref} />
    </>
  );
}

function AnalyzingNote({ name }: { name: string }) {
  return (
    <p className="flex items-center gap-2 text-xs text-[var(--color-text-secondary)]" role="status">
      <Spinner size="xs" />
      Reading what depends on {name}...
    </p>
  );
}

const QUIET_BUTTON =
  "rounded-sm text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]";

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

/** The analysis of the current pick at the current depth; a slower answer for an earlier pick never lands. */
function useAnalysis(adapter: ImpactAdapter, selection: Selection | null, maxDepth: number) {
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const request = useRef(0);
  // Read through a ref: a host that rebuilds its adapter each render must not
  // re-run the analysis each render.
  const adapterRef = useRef(adapter);
  adapterRef.current = adapter;

  const run = useCallback(async (sel: Selection, depth: number) => {
    const ticket = ++request.current;
    const current = () => ticket === request.current;
    setLoading(true);
    setError(null);
    try {
      const next = await analyzeSelection(adapterRef.current, sel, depth);
      if (current()) setAnalysis(next);
    } catch (err) {
      if (current()) {
        setAnalysis(null);
        setError(toFriendlyMessage(err, "Analysis failed."));
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
  return { analysis, error, loading };
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
          {filesOpen ? "Hide file picker" : "Or pick files"}
        </button>
      )}
    </div>
  );
}

function ResultPane({
  state,
  pickedName,
  empty,
  shownChange,
  fileHref,
  maxDepth,
  onDepth,
}: {
  state: ReturnType<typeof useAnalysis>;
  pickedName: string;
  empty: ReactNode;
  shownChange: ChangeCandidate | undefined;
  fileHref: ((path: string) => string) | undefined;
  maxDepth: number;
  onDepth: (depth: number) => void;
}) {
  const { analysis, error, loading } = state;
  return (
    <section className="min-w-0 space-y-6" aria-live="polite">
      {empty}
      {error && <p className="text-xs text-[var(--color-error)]">{error}</p>}
      {loading && pickedName && <AnalyzingNote name={pickedName} />}
      {loading && !analysis && (
        <div className="space-y-4">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-[360px] w-full" />
        </div>
      )}
      {analysis && (
        <div className={loading ? "space-y-6 opacity-60 transition-opacity" : "space-y-6"}>
          <ReviewBody
            // A new pick starts with no module filter left over from the last one.
            key={selectionKeyOf(analysis.selection)}
            analysis={analysis}
            change={shownChange}
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
    <div className="min-w-0 space-y-6" aria-busy={state.loading}>
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
        empty={empty}
        shownChange={shownChange}
        fileHref={fileHref}
        maxDepth={maxDepth}
        onDepth={setMaxDepth}
      />
    </div>
  );
}
