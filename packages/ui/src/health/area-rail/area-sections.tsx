"use client";

/**
 * The rows an area card is made of. Every row that names a place is a real
 * button, so the rail is the map's keyboard and screen-reader route: an area
 * row opens that area on the map, a file row opens the file in the rail.
 */

import type { ReactNode } from "react";
import { HEALTH_BAND_LABEL, bandForScore, formatScore } from "@repowise-dev/types/health";
import type { AreaFileRow, AreaFinding } from "@repowise-dev/types/areas";

import { cn } from "../../lib/cn";
import { biomarkerInfo } from "../biomarker-glossary";
import { formatDelta, scoreTextColor } from "../tokens";
import type { MapArea } from "../map/areas";
import { formatNumber } from "../../lib/format";

export function RailSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-1.5">
      <h4 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">{title}</h4>
      {children}
    </section>
  );
}

const ROW =
  "flex w-full min-w-0 items-baseline gap-2 rounded px-1.5 py-1 text-left transition-colors hover:bg-[var(--color-bg-elevated)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]";

function Score({ score }: { score: number | null }) {
  return (
    <span
      className={cn("shrink-0 font-mono text-xs tabular-nums", score == null ? "text-[var(--color-text-tertiary)]" : scoreTextColor(score))}
      title={score == null ? undefined : HEALTH_BAND_LABEL[bandForScore(score)]}
    >
      {score == null ? "—" : formatScore(score)}
    </span>
  );
}

/** Files, worst first: name leads, the directory is the small fact beside it. */
export function FileRows({
  files,
  selectedPath,
  onSelect,
}: {
  files: AreaFileRow[];
  selectedPath?: string | null | undefined;
  onSelect: (path: string) => void;
}) {
  return (
    <ul className="flex flex-col">
      {files.map((f) => {
        const cut = f.path.lastIndexOf("/");
        return (
          <li key={f.path}>
            <button
              type="button"
              onClick={() => onSelect(f.path)}
              aria-current={f.path === selectedPath ? "true" : undefined}
              className={ROW}
              title={f.path}
            >
              <span className="min-w-0 flex-1 truncate text-xs text-[var(--color-text-primary)]">
                {f.path.slice(cut + 1)}
                <span className="ml-1.5 font-mono text-[10px] text-[var(--color-text-tertiary)]">{f.path.slice(0, cut + 1)}</span>
              </span>
              <Score score={f.score} />
            </button>
          </li>
        );
      })}
    </ul>
  );
}

/** Areas: name, size, and the score that put them on the list. */
export function AreaRows({
  areas,
  nameOf,
  onSelect,
  showDelta = false,
}: {
  areas: MapArea[];
  nameOf: (path: string) => string;
  onSelect: (path: string) => void;
  showDelta?: boolean;
}) {
  return (
    <ul className="flex flex-col">
      {areas.map((a) => (
        <li key={a.path}>
          <button type="button" onClick={() => onSelect(a.path)} className={ROW} title={a.path}>
            <span className="min-w-0 flex-1 truncate font-mono text-xs text-[var(--color-text-primary)]">{nameOf(a.path)}</span>
            <span className="shrink-0 font-mono text-[10px] tabular-nums text-[var(--color-text-tertiary)]">
              {formatNumber(a.fileCount)}
            </span>
            {showDelta && a.delta30d != null ? (
              <span className="shrink-0 font-mono text-xs tabular-nums text-[var(--color-success)]">{formatDelta(a.delta30d)}</span>
            ) : (
              <Score score={a.health} />
            )}
          </button>
        </li>
      ))}
    </ul>
  );
}

/** Findings: what it is, where, and the file it opens. */
export function FindingRows({
  findings,
  onSelectFile,
}: {
  findings: AreaFinding[];
  onSelectFile: (path: string) => void;
}) {
  return (
    <ul className="flex flex-col">
      {findings.map((f) => {
        const name = f.file_path.slice(f.file_path.lastIndexOf("/") + 1);
        return (
          <li key={f.id}>
            <button type="button" onClick={() => onSelectFile(f.file_path)} className={cn(ROW, "flex-col items-start gap-0")} title={f.file_path}>
              <span className="text-xs text-[var(--color-text-primary)]">
                {biomarkerInfo(f.biomarker_type).label}
                <span className="ml-1.5 text-[10px] capitalize text-[var(--color-text-tertiary)]">{f.severity}</span>
              </span>
              <span className="max-w-full truncate font-mono text-[10px] text-[var(--color-text-tertiary)]">
                {name}
                {f.function_name ? ` · ${f.function_name}` : ""}
                {f.line_start ? `:${f.line_start}` : ""}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
