import { useCallback, useEffect, useState, type ReactNode } from "react";
import { ArrowLeft, Layers, Sparkles } from "lucide-react";
import {
  CONFIDENCE_DOT,
  CONFIDENCE_LABEL,
  EFFORT_LABEL,
  typeAccent,
  typeMeta,
} from "@repowise-dev/ui/refactoring/meta";
import { PlanDetail } from "@repowise-dev/ui/refactoring/plan-detail";
import { RefactoringPlanCard } from "@repowise-dev/ui/refactoring/refactoring-plan-card";
import {
  blastCount,
  blastFiles,
  evidenceRows,
  planWins,
  type Confidence,
  type EffortBucket,
  type RefactoringPlan,
  type RefactoringTargets,
} from "@repowise-dev/ui/refactoring/types";
import type { AiPromptFlavor } from "@repowise-dev/ui/health/ai-prompt-builder";
import type { ViewProps } from "../../runtime/mount";
import type { WebviewHost } from "../../runtime/rpc";

/**
 * Refactoring panel. Opened two ways:
 *   - with a `planId` (from a CodeLens or a tree item) → the detail page is the
 *     root and there is no list to fall back to;
 *   - without one → a ranked list of plans; clicking a card opens the detail
 *     in-panel, and a back control returns to the list.
 * All data arrives over the typed host RPC; file references route through
 * `host.openFile`.
 */
export function App({ host, params, refreshToken }: ViewProps<"refactoring">) {
  // A plan opened directly (CodeLens/tree) is the initial selection; a card
  // click sets its own. Back always returns to the list so an entry from a
  // lens or tree is never a dead end: the list is the file's plans when a
  // filePath came along, otherwise every ranked plan.
  const [selectedId, setSelectedId] = useState<string | null>(params.planId ?? null);

  // The host can push new params into an already-open panel; honor them.
  useEffect(() => {
    setSelectedId(params.planId ?? null);
  }, [params.planId]);

  if (selectedId != null) {
    return (
      <PlanDetailView
        host={host}
        planId={selectedId}
        refreshToken={refreshToken}
        onBack={() => setSelectedId(null)}
      />
    );
  }

  return (
    <PlanListView
      host={host}
      filePath={params.filePath}
      refreshToken={refreshToken}
      onOpen={(plan) => setSelectedId(plan.id)}
    />
  );
}

// ── Async state helper ─────────────────────────────────────────────────────

type AsyncState<T> =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ok"; data: T };

function useAsync<T>(load: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({ status: "loading" });
  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    load().then(
      (data) => {
        if (!cancelled) setState({ status: "ok", data });
      },
      (err: unknown) => {
        if (!cancelled) {
          setState({ status: "error", message: err instanceof Error ? err.message : String(err) });
        }
      },
    );
    return () => {
      cancelled = true;
    };
    // load is recreated per render; deps carry the real inputs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

// ── File reference bridge ──────────────────────────────────────────────────
//
// PlanDetail renders file references as anchors via a `fileHref` callback. In a
// webview a real navigation would blow away the panel, so we encode the path
// into a custom-scheme href and intercept the click to call host.openFile.

const FILE_HREF_PREFIX = "repowise-open:";

function fileHref(path: string, line?: number | null): string {
  const query = line != null ? `?line=${line}` : "";
  return `${FILE_HREF_PREFIX}${encodeURIComponent(path)}${query}`;
}

function useFileClickHandler(host: WebviewHost) {
  return useCallback(
    (event: React.MouseEvent<HTMLElement>) => {
      const anchor = (event.target as HTMLElement).closest("a");
      const href = anchor?.getAttribute("href");
      if (!href || !href.startsWith(FILE_HREF_PREFIX)) return;
      event.preventDefault();
      const [encodedPath, query] = href.slice(FILE_HREF_PREFIX.length).split("?");
      const path = decodeURIComponent(encodedPath ?? "");
      const rawLine = query?.startsWith("line=") ? Number(query.slice("line=".length)) : NaN;
      if (Number.isFinite(rawLine)) host.openFile(path, rawLine);
      else host.openFile(path);
    },
    [host],
  );
}

// ── Detail page ────────────────────────────────────────────────────────────

const FLAVORS: { label: string; flavor: AiPromptFlavor }[] = [
  { label: "Generic", flavor: "generic" },
  { label: "Claude Code", flavor: "claude-code" },
  { label: "Claude Code + Repowise MCP", flavor: "claude-code-mcp" },
  { label: "Cursor", flavor: "cursor" },
];

interface PlanDetailViewProps {
  host: WebviewHost;
  planId: string;
  refreshToken: number;
  /** Returns to the plan list; always provided so a detail is never a dead end. */
  onBack?: (() => void) | undefined;
}

function PlanDetailView({ host, planId, refreshToken, onBack }: PlanDetailViewProps) {
  const state = useAsync(() => host.api.refactoringPlan(planId), [planId, refreshToken]);
  const onFileClick = useFileClickHandler(host);

  if (state.status === "loading") return <CenteredNote>Loading plan…</CenteredNote>;
  if (state.status === "error") {
    return <ErrorNote title="Could not load this refactoring plan.">{state.message}</ErrorNote>;
  }

  const plan = state.data;
  const wins = planWins(plan);
  const evidence = evidenceRows(plan);
  const affected = blastFiles(plan).filter((f) => f !== plan.file_path);
  const blast = blastCount(plan);

  return (
    <div className="mx-auto max-w-3xl px-6 py-6" onClick={onFileClick}>
      {onBack ? (
        <button
          type="button"
          onClick={onBack}
          className="mb-4 inline-flex items-center gap-1.5 rounded-md px-1.5 py-1 text-xs font-medium text-[var(--color-text-tertiary)] transition-colors hover:text-[var(--color-text-primary)]"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          All refactoring plans
        </button>
      ) : null}

      <PlanHeader plan={plan} onOpenFile={() => host.openFile(plan.file_path, plan.line_start ?? undefined)} />

      {wins.length > 0 ? (
        <div className="mt-5 flex flex-wrap gap-2">
          {wins.map((win, i) => (
            <span
              key={i}
              className={
                win.hero
                  ? "inline-flex items-center gap-1.5 rounded-full bg-[var(--color-success)]/10 px-3 py-1 text-xs font-semibold text-[var(--color-success)]"
                  : "inline-flex items-center gap-1.5 rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-1 text-xs text-[var(--color-text-secondary)]"
              }
            >
              {win.label}
            </span>
          ))}
        </div>
      ) : null}

      <Section title="The plan">
        <PlanDetail plan={plan} fileHref={fileHref} />
      </Section>

      {evidence.length > 0 ? (
        <Section title="Evidence">
          <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {evidence.map((row) => (
              <div
                key={row.label}
                className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-2"
              >
                <dt className="text-[10px] uppercase tracking-wide text-[var(--color-text-tertiary)]">
                  {row.label}
                </dt>
                <dd className="mt-0.5 font-mono text-sm tabular-nums text-[var(--color-text-primary)]">
                  {row.value}
                </dd>
              </div>
            ))}
          </dl>
        </Section>
      ) : null}

      <Section title="Blast radius">
        <div className="flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
          <Layers className="h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
          {affected.length > 0
            ? `${affected.length} other file${affected.length === 1 ? "" : "s"} to keep consistent`
            : blast > 0
              ? `${blast} dependent${blast === 1 ? "" : "s"} affected`
              : "No dependent files recorded."}
        </div>
        {affected.length > 0 ? (
          <ul className="mt-2 divide-y divide-[var(--color-border-default)] overflow-hidden rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)]">
            {affected.slice(0, 25).map((file) => (
              <li key={file} className="px-3.5 py-2">
                <a
                  href={fileHref(file)}
                  className="block truncate font-mono text-xs text-[var(--color-text-secondary)] underline-offset-2 hover:text-[var(--color-accent-primary)] hover:underline"
                  title={file}
                >
                  {file}
                </a>
              </li>
            ))}
            {affected.length > 25 ? (
              <li className="px-3.5 py-2 text-[11px] text-[var(--color-text-tertiary)]">
                +{affected.length - 25} more
              </li>
            ) : null}
          </ul>
        ) : null}
      </Section>

      <CopyForAgentRow host={host} planId={planId} />
    </div>
  );
}

function PlanHeader({ plan, onOpenFile }: { plan: RefactoringPlan; onOpenFile: () => void }) {
  const meta = typeMeta(plan.refactoring_type);
  const accent = typeAccent(plan.refactoring_type);
  const { Icon } = meta;
  const effort = (plan.effort_bucket || "M") as EffortBucket;
  const confidence = (plan.confidence || "medium") as Confidence;

  return (
    <header>
      <div className="flex flex-wrap items-center gap-3">
        <span
          className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-sm font-semibold"
          style={{ backgroundColor: `color-mix(in srgb, ${accent} 14%, transparent)`, color: accent }}
        >
          <Icon className="h-4 w-4" />
          {meta.label}
        </span>
        {plan.impact_delta > 0 ? (
          <Badge title="Health recovered if applied" tone="success">
            +{plan.impact_delta.toFixed(1)} health
          </Badge>
        ) : null}
        <Badge title={`Effort: ${EFFORT_LABEL[effort]}`}>
          {effort} · {EFFORT_LABEL[effort]}
        </Badge>
        <Badge title={`Detector confidence: ${CONFIDENCE_LABEL[confidence]}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${CONFIDENCE_DOT[confidence]}`} />
          {CONFIDENCE_LABEL[confidence]} confidence
        </Badge>
      </div>

      <h1 className="mt-3 text-lg font-semibold text-[var(--color-text-primary)]">
        {plan.target_symbol || meta.label}
      </h1>
      <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">{meta.blurb}</p>
      <button
        type="button"
        onClick={onOpenFile}
        className="mt-1.5 inline-block max-w-full truncate font-mono text-xs text-[var(--color-text-secondary)] underline-offset-2 hover:text-[var(--color-accent-primary)] hover:underline"
        title={plan.file_path}
      >
        {plan.file_path}
        {plan.line_start != null ? (
          <span className="text-[var(--color-text-tertiary)]">:{plan.line_start}</span>
        ) : null}
      </button>
    </header>
  );
}

function CopyForAgentRow({ host, planId }: { host: WebviewHost; planId: string }) {
  const [busy, setBusy] = useState<AiPromptFlavor | null>(null);
  const [copyError, setCopyError] = useState<string | null>(null);

  async function copy(flavor: AiPromptFlavor, label: string): Promise<void> {
    setBusy(flavor);
    setCopyError(null);
    try {
      const prompt = await host.api.refactoringPrompt(planId, flavor);
      host.copyText(prompt, `Plan prompt copied for ${label}.`);
    } catch (err) {
      setCopyError(err instanceof Error ? err.message : "Could not build the prompt.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="sticky bottom-0 mt-8 -mx-6 border-t border-[var(--color-border-default)] bg-[var(--color-bg-root)]/95 px-6 py-3 backdrop-blur">
      <div className="mb-2 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--color-text-tertiary)]">
        <Sparkles className="h-3.5 w-3.5" />
        Copy for agent
      </div>
      <div className="flex flex-wrap gap-2">
        {FLAVORS.map(({ label, flavor }) => (
          <button
            key={flavor}
            type="button"
            disabled={busy != null}
            onClick={() => void copy(flavor, label)}
            className="inline-flex items-center rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-border-hover)] hover:text-[var(--color-text-primary)] disabled:opacity-50"
          >
            {label}
          </button>
        ))}
      </div>
      {copyError ? (
        <p className="mt-2 text-xs text-[var(--color-error)]">{copyError}</p>
      ) : null}
    </div>
  );
}

// ── List page ──────────────────────────────────────────────────────────────

interface PlanListViewProps {
  host: WebviewHost;
  filePath?: string | undefined;
  refreshToken: number;
  onOpen: (plan: RefactoringPlan) => void;
}

function PlanListView({ host, filePath, refreshToken, onOpen }: PlanListViewProps) {
  const state = useAsync(
    () => host.api.refactoringTargets(filePath),
    [filePath, refreshToken],
  );

  if (state.status === "loading") return <CenteredNote>Loading refactoring plans…</CenteredNote>;
  if (state.status === "error") {
    return <ErrorNote title="Could not load refactoring plans.">{state.message}</ErrorNote>;
  }

  const targets: RefactoringTargets = state.data;
  if (targets.plans.length === 0) {
    return (
      <CenteredNote>
        {filePath ? `No refactoring plans for ${filePath}.` : "No refactoring plans found."}
      </CenteredNote>
    );
  }

  return (
    <div className="mx-auto max-w-5xl px-6 py-6">
      <SummaryStrip summary={targets.summary} filePath={filePath} />
      <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {targets.plans.map((plan) => (
          <RefactoringPlanCard key={plan.id} plan={plan} onOpen={onOpen} />
        ))}
      </div>
    </div>
  );
}

function SummaryStrip({
  summary,
  filePath,
}: {
  summary: RefactoringTargets["summary"];
  filePath?: string | undefined;
}) {
  return (
    <div className="rounded-2xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-5 py-4">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="text-2xl font-semibold tabular-nums text-[var(--color-text-primary)]">
          {summary.total}
        </span>
        <span className="text-sm text-[var(--color-text-secondary)]">
          refactoring plan{summary.total === 1 ? "" : "s"}
          {filePath ? (
            <>
              {" in "}
              <span className="font-mono text-xs text-[var(--color-text-tertiary)]">{filePath}</span>
            </>
          ) : null}
        </span>
      </div>
      {summary.by_type.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5">
          {summary.by_type.map((row) => (
            <span key={row.type} className="inline-flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
              <span
                className="h-2 w-2 rounded-full"
                style={{ backgroundColor: typeAccent(row.type) }}
                aria-hidden
              />
              {typeMeta(row.type).label}
              <span className="tabular-nums text-[var(--color-text-tertiary)]">{row.count}</span>
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

// ── Shared bits ────────────────────────────────────────────────────────────

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-6">
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-tertiary)]">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Badge({
  children,
  title,
  tone = "neutral",
}: {
  children: ReactNode;
  title?: string;
  tone?: "neutral" | "success";
}) {
  const cls =
    tone === "success"
      ? "bg-[var(--color-success)]/10 text-[var(--color-success)]"
      : "border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] text-[var(--color-text-secondary)]";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium tabular-nums ${cls}`}
      title={title}
    >
      {children}
    </span>
  );
}

function CenteredNote({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center px-6 text-center text-sm text-[var(--color-text-tertiary)]">
      {children}
    </div>
  );
}

function ErrorNote({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="m-6 rounded-lg border border-[var(--color-error)] p-4 text-sm">
      <p className="font-medium text-[var(--color-error)]">{title}</p>
      <p className="mt-2 text-[var(--color-text-secondary)]">{children}</p>
    </div>
  );
}
