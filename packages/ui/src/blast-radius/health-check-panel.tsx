"use client";

import { Loader2, ShieldCheck } from "lucide-react";
import type { ChangeHealthCheckResponse, HealthCheckFinding } from "@repowise-dev/types/blast-radius";
import { Button } from "../ui/button";
import { MICRO } from "../health/tokens";
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
        <span className="text-[10px] uppercase text-[var(--color-text-tertiary)]">{f.level}</span>
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

function Result({ result, fileHref }: { result: ChangeHealthCheckResponse; fileHref: ((path: string) => string) | undefined }) {
  const v = VERDICT[result.gate.verdict];
  const groups = GROUPS.map((g) => ({ ...g, rows: result.findings.filter(g.match) })).filter((g) => g.rows.length > 0);
  return (
    <div className="space-y-3">
      <p className="flex flex-wrap items-center gap-2 text-sm">
        <span className="inline-flex items-center gap-1.5 font-semibold" style={{ color: v.color }}>
          <span className="h-2 w-2 rounded-full" style={{ background: v.color }} aria-hidden />
          {v.word}
        </span>
        <span className="text-xs text-[var(--color-text-tertiary)]">
          {result.gate.failing_count} failing, {result.gate.warning_count} warning · fails on {result.gate.fail_on} at{" "}
          {result.gate.fail_at} · <code className="font-mono">{result.revspec}</code>
        </span>
      </p>
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
            <p className={MICRO}>
              {g.title} <span className="tabular-nums">{g.rows.length}</span>
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

/**
 * The on-demand health check for the picked change: the same check as
 * `repowise health check`, read from git. Never runs by itself; the button
 * runs it, and the result stays for that change until the page reloads.
 */
export function HealthCheckPanel({
  state,
  onRun,
  fileHref,
}: {
  state: HealthCheckState | undefined;
  onRun: () => void;
  fileHref?: ((path: string) => string) | undefined;
}) {
  const running = state?.status === "running";
  return (
    <section className="space-y-3 border-t border-[var(--color-border-default)] pt-5" aria-busy={running}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className={MICRO}>Health check</p>
        <Button size="sm" variant="outline" onClick={onRun} disabled={running}>
          {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : <ShieldCheck className="h-3.5 w-3.5" aria-hidden />}
          {running ? "Checking..." : state?.status === "done" ? "Run again" : "Run health check"}
        </Button>
      </div>
      {!state && (
        <p className="text-xs text-[var(--color-text-tertiary)]">
          Compares code health before and after this change, plus added suppressions, weakened tests and test-only
          branches, the way <code className="font-mono">repowise health check</code> does in CI.
        </p>
      )}
      {running && <p className="text-xs text-[var(--color-text-tertiary)]">Reading both sides of every changed file...</p>}
      {state?.status === "error" && <p className="text-xs text-[var(--color-error)]">{state.message}</p>}
      {state?.status === "done" && <Result result={state.result} fileHref={fileHref} />}
    </section>
  );
}
