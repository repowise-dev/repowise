import {
  AlertTriangle,
  FlaskConical,
  GitCompareArrows,
  Network,
} from "lucide-react";
import type { ReactNode } from "react";
import type { BlastRadiusResponse } from "@repowise-dev/types/blast-radius";
import { cn } from "../lib/cn";

interface BlastRadiusHeaderProps {
  result: BlastRadiusResponse;
  changedFiles?: string[];
}

interface Band {
  label: string;
  color: string; // CSS var
  text: string;
  ring: string;
}

function band(score: number): Band {
  if (score >= 7)
    return {
      label: "High risk",
      color: "var(--color-error)",
      text: "text-[var(--color-error)]",
      ring: "border-[var(--color-error)]/30 bg-[var(--color-error)]/5",
    };
  if (score >= 4)
    return {
      label: "Medium risk",
      color: "var(--color-warning)",
      text: "text-[var(--color-warning)]",
      ring: "border-[var(--color-warning)]/30 bg-[var(--color-warning)]/5",
    };
  return {
    label: "Low risk",
    color: "var(--color-success)",
    text: "text-[var(--color-success)]",
    ring: "border-[var(--color-success)]/30 bg-[var(--color-success)]/5",
  };
}

// --- Half-donut gauge geometry -------------------------------------------
const GW = 200;
const GH = 116;
const GCX = GW / 2;
const GCY = GH - 8;
const GR = 84;
const GSTROKE = 14;

function polar(cx: number, cy: number, r: number, angleDeg: number) {
  const rad = (angleDeg * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

function arcPath(
  cx: number,
  cy: number,
  r: number,
  startAngle: number,
  endAngle: number,
): string {
  const start = polar(cx, cy, r, startAngle);
  const end = polar(cx, cy, r, endAngle);
  const largeArc = endAngle - startAngle <= 180 ? 0 : 1;
  return `M ${start.x} ${start.y} A ${r} ${r} 0 ${largeArc} 1 ${end.x} ${end.y}`;
}

/** A 180° gauge that fills proportionally to the 0–10 score. */
function RiskGauge({ score }: { score: number }) {
  const b = band(score);
  const frac = Math.max(0, Math.min(1, score / 10));
  const valueEnd = 180 + frac * 180;
  return (
    <div className="relative shrink-0" style={{ width: GW, height: GH }}>
      <svg viewBox={`0 0 ${GW} ${GH}`} className="h-auto w-full" aria-hidden="true">
        <path
          d={arcPath(GCX, GCY, GR, 180, 360)}
          fill="none"
          stroke="var(--color-bg-wash)"
          strokeWidth={GSTROKE}
          strokeLinecap="round"
        />
        {frac > 0 && (
          <path
            d={arcPath(GCX, GCY, GR, 180, valueEnd)}
            fill="none"
            stroke={b.color}
            strokeWidth={GSTROKE}
            strokeLinecap="round"
          />
        )}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-end pb-1">
        <span className={cn("text-4xl font-bold leading-none tabular-nums", b.text)}>
          {score.toFixed(1)}
        </span>
        <span className="mt-1 text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
          risk / 10
        </span>
      </div>
    </div>
  );
}

interface Tile {
  label: string;
  value: number;
  icon: ReactNode;
  tone: string; // text color token class
  active: boolean;
}

function StatTile({ tile }: { tile: Tile }) {
  return (
    <div
      className={cn(
        "flex items-center gap-3 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-3 py-2.5",
        tile.active ? "" : "opacity-60",
      )}
    >
      <span className={cn("shrink-0", tile.value > 0 ? tile.tone : "text-[var(--color-text-tertiary)]")}>
        {tile.icon}
      </span>
      <div className="min-w-0">
        <p className="text-xl font-bold leading-none tabular-nums text-[var(--color-text-primary)]">
          {tile.value}
        </p>
        <p className="mt-0.5 truncate text-[11px] text-[var(--color-text-tertiary)]">
          {tile.label}
        </p>
      </div>
    </div>
  );
}

/** Plain-English read of the blast radius, built from the counts. */
function verdict(result: BlastRadiusResponse, changedCount: number): string {
  const direct = result.direct_risks.length;
  const transitive = result.transitive_affected.length;
  const gaps = result.test_gaps.length;
  const reach = direct + transitive;
  const filesWord = changedCount === 1 ? "file" : "files";
  if (reach === 0) {
    return `Changing ${changedCount} ${filesWord} looks self-contained — nothing downstream depends on it within this depth.`;
  }
  const gapClause =
    gaps > 0
      ? ` ${gaps} affected ${gaps === 1 ? "file lacks" : "files lack"} tests.`
      : " Affected files have tests.";
  return `Changing ${changedCount} ${filesWord} puts ${reach} downstream ${
    reach === 1 ? "file" : "files"
  } in scope (${direct} direct, ${transitive} transitive).${gapClause}`;
}

/**
 * The blast-radius headline: a risk gauge, four signal tiles, and a one-line
 * plain-English verdict — replacing the old centred-number card + flat stat
 * grid so the result reads as a conclusion, not a row of figures.
 */
export function BlastRadiusHeader({ result, changedFiles = [] }: BlastRadiusHeaderProps) {
  const b = band(result.overall_risk_score);
  const tiles: Tile[] = [
    {
      label: "Direct risks",
      value: result.direct_risks.length,
      icon: <AlertTriangle className="h-5 w-5" />,
      tone: "text-[var(--color-error)]",
      active: result.direct_risks.length > 0,
    },
    {
      label: "Transitive files",
      value: result.transitive_affected.length,
      icon: <Network className="h-5 w-5" />,
      tone: "text-[var(--color-warning)]",
      active: result.transitive_affected.length > 0,
    },
    {
      label: "Co-change gaps",
      value: result.cochange_warnings.length,
      icon: <GitCompareArrows className="h-5 w-5" />,
      tone: "text-[var(--color-caution)]",
      active: result.cochange_warnings.length > 0,
    },
    {
      label: "Test gaps",
      value: result.test_gaps.length,
      icon: <FlaskConical className="h-5 w-5" />,
      tone: "text-[var(--color-info)]",
      active: result.test_gaps.length > 0,
    },
  ];

  return (
    <div className={cn("rounded-xl border p-4 sm:p-5", b.ring)}>
      <div className="flex flex-col gap-5 sm:flex-row sm:items-center">
        <RiskGauge score={result.overall_risk_score} />

        <div className="min-w-0 flex-1 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className={cn("text-sm font-semibold", b.text)}>{b.label}</span>
            <span className="text-xs text-[var(--color-text-tertiary)]">
              · blast radius of this change
            </span>
          </div>
          <p className="text-sm leading-relaxed text-[var(--color-text-secondary)]">
            {verdict(result, changedFiles.length)}
          </p>
          <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
            {tiles.map((t) => (
              <StatTile key={t.label} tile={t} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
