"use client";

import { Loader2, ShieldCheck } from "lucide-react";
import type { ChangeHealthCheckResponse, HealthCheckFinding } from "@repowise-dev/types/blast-radius";
import { SeverityMark } from "../health/severity-mark";
import type { Severity } from "../health/tokens";
import { CopyPathButton, FilePath } from "../shared/file-path";

export type HealthCheckState =
  | { status: "running" }
  | { status: "done"; result: ChangeHealthCheckResponse }
  | { status: "error"; message: string };

const VERDICT: Record<ChangeHealthCheckResponse["gate"]["verdict"], { word: string; color: string }> = {
  pass: { word: "Pass", color: "var(--color-success)" },
  warn: { word: "Warn", color: "var(--color-warning)" },
  fail: { word: "Fail", color: "var(--color-error)" },
};

const SEVERITIES: readonly string[] = ["critical", "high", "medium", "low"];

/** The groups a reader acts on, in order: what the change added, made worse, then cleaned up. */
const GROUPS: { title: string; match: (f: HealthCheckFinding) => boolean }[] = [
  { title: "Introduced", match: (f) => f.source === "health" && f.change_kind === "introduced" },
  { title: "Worsened", match: (f) => f.source === "health" && f.change_kind === "worsened" },
  { title: "Diff risk", match: (f) => f.source === "diff-risk" },
  { title: "Fixed", match: (f) => f.source === "health" && (f.change_kind === "resolved" || f.change_kind === "improved") },
];

function FindingRow({ f, fileHref }: { f: HealthCheckFinding; fileHref: ((path: string) => string) | undefined }) {
  const href = f.file_path ? fileHref?.(f.file_path) : undefined;
  const path = <FilePath path={f.file_path} line={f.line_number} accent={Boolean(href)} />;
  return (
    <li className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
      {f.severity && SEVERITIES.includes(f.severity) ? (
        <SeverityMark severity={f.severity as Severity} compact />
      ) : (
        <span className="text-[11px] text-[var(--color-text-tertiary)]">{f.level}</span>
      )}
      <span className="flex min-w-0 items-center">
        {href ? (
          <a href={href} className="min-w-0 hover:underline">
            {path}
          </a>
        ) : (
          path
        )}
        <CopyPathButton text={f.file_path} label="Copy path" copiedLabel="Copied" />
      </span>
      <span className="min-w-0 flex-1 truncate text-[var(--color-text-secondary)]" title={f.message}>
        {f.source === "diff-risk" ? `${f.rule}: ${f.message}` : f.message}
      </span>
    </li>
  );
}

/** A revspec with each full sha cut to 8 characters; the whole one stays in the tooltip. */
export function shortRevspec(revspec: string): string {
  return revspec.replace(/\b[0-9a-f]{40}\b/g, (sha) => sha.slice(0, 8));
}

function Result({ result, fileHref }: { result: ChangeHealthCheckResponse; fileHref: ((path: string) => string) | undefined }) {
  const v = VERDICT[result.gate.verdict];
  const groups = GROUPS.map((g) => ({ ...g, rows: result.findings.filter(g.match) })).filter((g) => g.rows.length > 0);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
        <p className="inline-flex items-center gap-2 text-[15px] font-semibold" style={{ color: v.color }}>
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: v.color }} aria-hidden />
          {v.word}
        </p>
        <p className="flex items-baseline gap-1.5">
          <span className="font-mono text-[15px] font-semibold tabular-nums text-[var(--color-text-primary)]">
            {result.gate.failing_count}
          </span>
          <span className="text-xs text-[var(--color-text-secondary)]">failing</span>
        </p>
        <p className="flex items-baseline gap-1.5">
          <span className="font-mono text-[15px] font-semibold tabular-nums text-[var(--color-text-primary)]">
            {result.gate.warning_count}
          </span>
          <span className="text-xs text-[var(--color-text-secondary)]">{result.gate.warning_count === 1 ? "warning" : "warnings"}</span>
        </p>
        <p className="text-xs text-[var(--color-text-tertiary)]" title={result.revspec}>
          Fails on {result.gate.fail_on} at {result.gate.fail_at}, judged on {shortRevspec(result.revspec)}
        </p>
      </div>
      {result.gate.notes.map((n) => (
        <p key={n} className="text-xs text-[var(--color-text-tertiary)]">
          {n}
        </p>
      ))}
      {groups.length === 0 ? (
        <p className="text-xs text-[var(--color-text-secondary)]">
          {result.explanation ?? "The change introduced, worsened or fixed no health finding."}
        </p>
      ) : (
        groups.map((g) => (
          <div key={g.title} className="space-y-1">
            <p className="text-xs font-medium text-[var(--color-text-secondary)]">
              {g.title} <span className="font-normal tabular-nums text-[var(--color-text-tertiary)]">{g.rows.length}</span>
            </p>
            <ul className="space-y-1">
              {g.rows.slice(0, 12).map((f, i) => (
                <FindingRow key={`${f.fingerprint}:${f.line_number ?? ""}:${i}`} f={f} fileHref={fileHref} />
              ))}
            </ul>
            {g.rows.length > 12 && (
              <p className="text-[11px] text-[var(--color-text-tertiary)]">{g.rows.length - 12} more in this group.</p>
            )}
          </div>
        ))
      )}
    </div>
  );
}

/** A secondary action: outlined, the height of Copy review prompt, never filled. */
const ACTION =
  "inline-flex h-7 items-center gap-1.5 rounded-md border border-[var(--color-border-hover)] bg-transparent px-2.5 text-xs font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-bg-elevated)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)] disabled:opacity-60 max-sm:h-10";

/**
 * The quiet action beside Copy review prompt that runs the health check: the
 * same check as `repowise health check`, on demand only.
 */
export function HealthCheckButton({ state, onRun }: { state: HealthCheckState | undefined; onRun: () => void }) {
  const running = state?.status === "running";
  const verdict = state?.status === "done" ? VERDICT[state.result.gate.verdict] : null;
  return (
    <span className="inline-flex items-center gap-2">
      <button
        type="button"
        onClick={onRun}
        disabled={running}
        className={ACTION}
        title="Compares code health before and after this change, plus added suppressions, weakened tests and test-only branches, the way repowise health check does in CI."
      >
        {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : <ShieldCheck className="h-3.5 w-3.5" aria-hidden />}
        {running ? "Checking..." : verdict ? "Run again" : "Run health check"}
      </button>
      {verdict && (
        <span className="inline-flex items-center gap-1.5 text-xs font-semibold" style={{ color: verdict.color }}>
          <span className="h-2 w-2 rounded-full" style={{ background: verdict.color }} aria-hidden />
          Health check: {verdict.word}
        </span>
      )}
    </span>
  );
}

/**
 * The health check's result for the picked change, once it has run: the
 * verdict as the headline, the counts beside it, then the findings by what
 * the change did. Renders nothing until the check is run.
 */
export function HealthCheckPanel({
  state,
  fileHref,
}: {
  state: HealthCheckState | undefined;
  fileHref?: ((path: string) => string) | undefined;
}) {
  if (!state) return null;
  const running = state.status === "running";
  return (
    <section className="space-y-3 border-t border-[var(--color-border-default)] pt-5" aria-busy={running} aria-live="polite">
      <h3 className="text-[15px] font-medium text-[var(--color-text-primary)]">Health check</h3>
      {running && <p className="text-xs text-[var(--color-text-tertiary)]">Reading both sides of every changed file...</p>}
      {state.status === "error" && <p className="text-xs text-[var(--color-error)]">{state.message}</p>}
      {state.status === "done" && <Result result={state.result} fileHref={fileHref} />}
    </section>
  );
}
