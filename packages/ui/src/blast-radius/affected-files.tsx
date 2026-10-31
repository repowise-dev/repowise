"use client";

import { useMemo, useState, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import type { AffectedSignal, BlastRadiusResponse, DirectRiskEntry } from "@repowise-dev/types/blast-radius";
import { HealthBadge } from "../health/health-badge";
import { CopyPathButton, FilePath } from "../shared/file-path";
import { cn } from "../lib/cn";
import { attention, moduleOf, type FileMarks } from "./blast-map-model";

type Result = BlastRadiusResponse & { affected_signals?: AffectedSignal[] };
type Href = ((path: string) => string) | undefined;

const CHIP =
  "inline-flex h-[18px] items-center gap-1 rounded border border-[var(--color-border-default)] px-1.5 text-[10px] text-[var(--color-text-secondary)]";

function marksOf(path: string, signals: Map<string, AffectedSignal>, gaps: Set<string>, hot?: boolean): FileMarks {
  const s = signals.get(path);
  return {
    path,
    healthScore: s?.health_score ?? null,
    isHotspot: s ? s.is_hotspot : (hot ?? null),
    untested: gaps.has(path),
  };
}

function Path({ path, href }: { path: string; href: string | undefined }) {
  const label = <FilePath path={path} accent={Boolean(href)} className="max-w-full" />;
  return (
    <span className="flex min-w-0 items-center">
      {href ? (
        <a href={href} className="min-w-0 hover:underline">
          {label}
        </a>
      ) : (
        label
      )}
      <CopyPathButton text={path} label="Copy path" copiedLabel="Copied" />
    </span>
  );
}

function Marks({ m, extra }: { m: FileMarks; extra?: ReactNode }) {
  return (
    <span className="flex shrink-0 items-center gap-1">
      {extra}
      <HealthBadge score={m.healthScore} />
      {m.isHotspot && <span className={CHIP}>▲ hot</span>}
      {m.untested && <span className={`${CHIP} border-dashed`}>no tests</span>}
    </span>
  );
}

/** "Top 3%": the churn rank as a reader says it; the raw sum rides in the tooltip. */
function churnRank(d: DirectRiskEntry): string {
  const top = Math.max(1, Math.round((1 - (d.churn_percentile ?? 0)) * 100));
  return `top ${top}%`;
}

/**
 * The changed files, each with what makes it risky in plain words: health,
 * how often it changed lately (as a rank), whether many files lean on it,
 * and test evidence. The raw scores sit in tooltips for whoever wants them.
 */
export function ChangedFilesList({ result, changedFiles, fileHref }: { result: Result; changedFiles: string[]; fileHref: Href }) {
  const signals = useMemo(() => new Map((result.affected_signals ?? []).map((s) => [s.path, s])), [result]);
  const gaps = useMemo(() => new Set(result.test_gaps), [result]);
  const direct = useMemo(() => new Map(result.direct_risks.map((d) => [d.path, d])), [result]);
  // Within this change: the most depended-on third, by pagerank. Pagerank is
  // tiny and repo-relative, so it is compared here, never printed as a percent.
  const centralCut = useMemo(() => {
    const ranks = result.direct_risks.map((d) => d.centrality).sort((a, b) => b - a);
    return ranks.length >= 3 ? (ranks[Math.floor(ranks.length / 3) - 1] ?? Infinity) : Infinity;
  }, [result]);
  const rows = changedFiles
    .map((p) => ({ m: marksOf(p, signals, gaps, direct.get(p)?.is_hotspot), d: direct.get(p) }))
    .sort((a, b) => attention(b.m) - attention(a.m) || (b.d?.structural_score ?? 0) - (a.d?.structural_score ?? 0));
  return (
    <ul className="space-y-1">
      {rows.map(({ m, d }) => (
        <li key={m.path} className="flex min-w-0 items-center justify-between gap-2 text-xs">
          <Path path={m.path} href={fileHref?.(m.path)} />
          <Marks
            // The churn rank chip below already says hotspot, with its rank.
            m={d?.is_hotspot ? { ...m, isHotspot: false } : m}
            extra={
              <>
                {d && d.centrality > 0 && d.centrality >= centralCut && (
                  <span className={CHIP} title={`Among the most depended-on files in this change (pagerank ${d.centrality.toExponential(2)})`}>
                    central
                  </span>
                )}
                {d?.is_hotspot && (
                  <span className={CHIP} title={`Changed often lately: ${churnRank(d)} of files by recent churn (raw ${d.temporal_hotspot.toFixed(1)})`}>
                    ▲ {churnRank(d)} churn
                  </span>
                )}
              </>
            }
          />
        </li>
      ))}
    </ul>
  );
}

interface Group {
  name: string;
  depth: number;
  files: (FileMarks & { depth: number })[];
  risk: number;
}

/**
 * Every file the change reaches, by module: each module a row with its count,
 * nearest hop and risk marks, opening to its files nearest first. A filter
 * narrows modules and files by path.
 */
export function AffectedByModule({ result, fileHref }: { result: Result; fileHref: Href }) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<Set<string>>(new Set());
  const groups = useMemo(() => {
    const signals = new Map((result.affected_signals ?? []).map((s) => [s.path, s]));
    const gaps = new Set(result.test_gaps);
    const byName = new Map<string, Group>();
    for (const t of result.transitive_affected) {
      const name = moduleOf(t.path, signals.get(t.path));
      const g = byName.get(name) ?? { name, depth: t.depth, files: [], risk: 0 };
      const m = { ...marksOf(t.path, signals, gaps), depth: t.depth };
      g.files.push(m);
      g.depth = Math.min(g.depth, t.depth);
      g.risk += attention(m);
      byName.set(name, g);
    }
    for (const g of byName.values()) g.files.sort((a, b) => a.depth - b.depth || attention(b) - attention(a) || a.path.localeCompare(b.path));
    return [...byName.values()].sort((a, b) => a.depth - b.depth || b.risk - a.risk || b.files.length - a.files.length);
  }, [result]);

  const q = query.trim().toLowerCase();
  const shown = q
    ? groups
        .map((g) => ({ ...g, files: g.name.toLowerCase().includes(q) ? g.files : g.files.filter((f) => f.path.toLowerCase().includes(q)) }))
        .filter((g) => g.files.length > 0)
    : groups;
  const toggle = (name: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  return (
    <div className="space-y-2">
      <input
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Filter by path or module"
        aria-label="Filter affected files"
        className="w-full max-w-xs rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2 py-1 text-xs text-[var(--color-text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-focus-ring)]"
      />
      <ul className="divide-y divide-[var(--color-border-default)]">
        {shown.map((g) => {
          const expanded = open.has(g.name) || (q !== "" && shown.length <= 3);
          const hot = g.files.filter((f) => f.isHotspot).length;
          const untested = g.files.filter((f) => f.untested).length;
          return (
            <li key={g.name}>
              <button
                type="button"
                aria-expanded={expanded}
                onClick={() => toggle(g.name)}
                className="flex w-full min-w-0 items-center gap-2 py-1.5 text-left text-xs hover:bg-[var(--color-bg-elevated)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
              >
                <ChevronRight
                  className={cn("h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)] transition-transform", expanded && "rotate-90")}
                  aria-hidden
                />
                <span className="min-w-0 flex-1 truncate font-mono text-[var(--color-text-primary)]">{g.name}</span>
                <span className="shrink-0 tabular-nums text-[var(--color-text-secondary)]">{g.files.length} files</span>
                <span className="w-14 shrink-0 text-right text-[var(--color-text-tertiary)]">
                  {g.depth === 1 ? "direct" : `${g.depth} hops`}
                </span>
                <span className="flex w-28 shrink-0 justify-end gap-1">
                  {hot > 0 && <span className={CHIP} title={`${hot} hotspot files`}>▲ {hot}</span>}
                  {untested > 0 && <span className={`${CHIP} border-dashed`} title={`${untested} files without test evidence`}>{untested}</span>}
                </span>
              </button>
              {expanded && (
                <ul className="space-y-1 pb-2 pl-6">
                  {g.files.map((f) => (
                    <li key={f.path} className="flex min-w-0 items-center justify-between gap-2 text-xs">
                      <Path path={f.path} href={fileHref?.(f.path)} />
                      <Marks m={f} extra={<span className="text-[10px] text-[var(--color-text-tertiary)]">{f.depth === 1 ? "direct" : `${f.depth} hops`}</span>} />
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
      {shown.length === 0 && <p className="text-xs text-[var(--color-text-tertiary)]">No affected file matches.</p>}
    </div>
  );
}
