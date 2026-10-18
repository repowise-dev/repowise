import { cn } from "../lib/cn";
import {
  betterCount,
  healthDeltaKind,
  worseCount,
  type HealthDeltaCounts,
  type HealthDeltaKind,
} from "./health-delta";

export type HealthDeltaLabels = Record<HealthDeltaKind | "notCompared" | "partial" | "stale", string>;

const DEFAULT_LABELS: HealthDeltaLabels = {
  improved: "Better",
  worsened: "Worse",
  mixed: "Mixed",
  neutral: "No change",
  notCompared: "Not yet analysed",
  partial: "Part of this change was not compared",
  stale: "From an older analyzer: the direction holds, the counts do not compare",
};

/** Green only for a real improvement, red only for a regression; a mixed or
 *  neutral result stays plain so colour keeps meaning one thing. */
const DOT: Record<HealthDeltaKind, string> = {
  improved: "bg-[var(--color-success)]",
  worsened: "bg-[var(--color-error)]",
  mixed: "bg-[var(--color-border-hover)]",
  neutral: "",
};

const TEXT: Record<HealthDeltaKind, string> = {
  improved: "text-[var(--color-success)]",
  worsened: "text-[var(--color-error)]",
  mixed: "text-[var(--color-text-primary)]",
  neutral: "text-[var(--color-text-tertiary)]",
};

/** "+3", "−2" or "+3 −1": the signed finding counts behind the word. */
export function signedFigures(better: number, worse: number): string {
  return [better > 0 ? `+${better}` : "", worse > 0 ? `−${worse}` : ""].filter(Boolean).join(" ");
}

export interface HealthDeltaMarkProps {
  /** Null when the commit or change was never compared, which is not the
   *  same claim as "changed nothing". */
  delta: HealthDeltaCounts | null | undefined;
  /** Words for each state; packages/ui has no i18n, so hosts pass their own. */
  labels?: Partial<HealthDeltaLabels>;
  /** Off where the counts already lead beside the mark, e.g. a lede figure. */
  figures?: boolean;
  className?: string;
}

/**
 * What a change did to code health, as a dot, a word and the signed finding
 * counts behind it ("Better +3", "Worse -2", "Mixed +3 -1"). A neutral result
 * renders quietly: it is the default state, not something to mark. A stale
 * delta (older analyzer) shows its word and a hollow dot only.
 */
/** The caveat a delta carries, stale before partial; null when it reads as is. */
function caveat(delta: HealthDeltaCounts, l: HealthDeltaLabels): string | null {
  if (delta.stale) return l.stale;
  return delta.status === "partial" ? l.partial : null;
}

/** The dot: hollow when stale, the kind's colour otherwise, none when neutral. */
function MarkDot({ kind, stale }: { kind: HealthDeltaKind; stale: boolean }) {
  if (kind === "neutral") return null;
  return (
    <span
      aria-hidden
      className={cn("h-1.5 w-1.5 shrink-0 rounded-full", stale ? "border border-current" : DOT[kind])}
    />
  );
}

export function HealthDeltaMark({ delta, labels, figures = true, className }: HealthDeltaMarkProps) {
  const l = { ...DEFAULT_LABELS, ...labels };
  if (!delta) {
    return (
      <span className={cn("text-xs text-[var(--color-text-tertiary)]", className)}>
        {l.notCompared}
      </span>
    );
  }
  const kind = healthDeltaKind(delta);
  // A stale delta keeps its direction but drops its colour and figures.
  const stale = !!delta.stale;
  const note = caveat(delta, l);
  const counts = figures && !stale ? signedFigures(betterCount(delta), worseCount(delta)) : "";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap text-xs",
        stale ? "text-[var(--color-text-tertiary)]" : TEXT[kind],
        className,
      )}
      title={note ?? undefined}
    >
      <MarkDot kind={kind} stale={stale} />
      <span className={kind === "neutral" ? undefined : "font-medium"}>
        {l[kind]}
        {delta.status === "partial" && <span aria-hidden>*</span>}
      </span>
      {counts && <span className="font-mono tabular-nums">{counts}</span>}
      {note && <span className="sr-only">{note}</span>}
    </span>
  );
}
