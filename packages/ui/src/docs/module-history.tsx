/**
 * Git history for the files a module page covers: who maintains them, where
 * change and fixes concentrate, and what moves with them. Read from the
 * `module_signals` the generator stamps on the page, the same data the page's
 * agent digest states in prose, so the two cannot disagree.
 */

import { cn } from "../lib/cn";

interface ModuleSignals {
  files?: number;
  hotspots?: number;
  bus_factor_one?: number;
  bug_fixes?: number;
  most_fixed_file?: string;
  owners?: { name: string; files: number }[];
  most_active_file?: string;
  most_active_commits_90d?: number;
  co_changes?: { path: string; files: number }[];
}

function readSignals(metadata: Record<string, unknown> | undefined): ModuleSignals | null {
  const raw = metadata?.module_signals;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  return raw as ModuleSignals;
}

function basename(path: string): string {
  return path.split("/").filter(Boolean).pop() ?? path;
}

function Row({
  label,
  value,
  title,
  mono = true,
}: {
  label: string;
  value: string;
  title?: string | undefined;
  /** Counts and paths are machine facts; a person's name is not. */
  mono?: boolean;
}) {
  const full = title ?? value;
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="shrink-0 text-[var(--color-text-tertiary)]">{label}</span>
      {/* The visible value truncates; the full one stays readable to assistive tech. */}
      <span
        className={cn("min-w-0 truncate text-right tabular-nums", mono && "font-mono")}
        title={full}
        aria-hidden
      >
        {value}
      </span>
      <span className="sr-only">{full}</span>
    </div>
  );
}

export function ModuleHistory({ metadata }: { metadata: Record<string, unknown> | undefined }) {
  const s = readSignals(metadata);
  if (!s) return null;
  const of = s.files ? ` of ${s.files}` : "";
  const owner = s.owners?.[0];
  const coChange = s.co_changes?.[0];
  return (
    <div>
      <p className="mb-2.5 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        History
      </p>
      <div className="space-y-1 text-xs text-[var(--color-text-secondary)]">
        {owner && (
          <Row
            label="Owner"
            mono={false}
            value={`${owner.name} · ${owner.files}${of} files`}
            title={s.owners!.map((o) => `${o.name} (${o.files} files)`).join(", ")}
          />
        )}
        {s.hotspots ? <Row label="Hotspots" value={`${s.hotspots}${of} files`} /> : null}
        {s.bus_factor_one ? (
          <Row label="Single maintainer" value={`${s.bus_factor_one}${of} files`} />
        ) : null}
        {s.bug_fixes ? (
          <Row
            label="Bug fixes"
            value={s.most_fixed_file ? `${s.bug_fixes} · most in ${basename(s.most_fixed_file)}` : `${s.bug_fixes}`}
            title={s.most_fixed_file ? `${s.bug_fixes} bug-fix commits, most in ${s.most_fixed_file}` : undefined}
          />
        ) : null}
        {s.most_active_file && (
          <Row
            label="Most active"
            value={`${basename(s.most_active_file)} · ${s.most_active_commits_90d ?? 0} in 90d`}
            title={`${s.most_active_file}: ${s.most_active_commits_90d ?? 0} commits in 90 days`}
          />
        )}
        {coChange && (
          <Row
            label="Changes with"
            value={coChange.path}
            title={s.co_changes!.map((c) => c.path).join(", ")}
          />
        )}
      </div>
    </div>
  );
}
