export type EffortBucket = "S" | "M" | "L" | "XL";

export interface RefactoringTarget {
  file_path: string;
  score: number;
  nloc: number;
  primary_biomarker: string;
  primary_severity: "low" | "medium" | "high" | "critical";
  primary_reason: string;
  primary_function: string | null;
  primary_line_start: number | null;
  primary_line_end: number | null;
  total_impact: number;
  finding_count: number;
  biomarkers: string[];
  effort_bucket: EffortBucket;
  impact_per_effort: number;
}

export interface RefactoringCardProps {
  target: RefactoringTarget;
  onSelect?: (target: RefactoringTarget) => void;
}

const severityColor: Record<RefactoringTarget["primary_severity"], string> = {
  critical: "bg-red-500/15 text-red-500",
  high: "bg-amber-500/15 text-amber-500",
  medium: "bg-yellow-500/15 text-yellow-500",
  low: "bg-blue-500/15 text-blue-500",
};

const effortLabel: Record<EffortBucket, string> = {
  S: "Small",
  M: "Medium",
  L: "Large",
  XL: "Extra large",
};

export function RefactoringCard({ target, onSelect }: RefactoringCardProps) {
  const Wrapper = onSelect ? "button" : "div";
  return (
    <Wrapper
      type={onSelect ? "button" : undefined}
      onClick={onSelect ? () => onSelect(target) : undefined}
      className={`w-full text-left rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4 space-y-2 ${
        onSelect ? "hover:border-[var(--color-border-strong)] transition-colors" : ""
      }`}
    >
      <div className="flex items-center gap-2">
        <span
          className={`inline-block rounded px-2 py-0.5 text-[10px] uppercase font-semibold ${severityColor[target.primary_severity]}`}
        >
          {target.primary_severity}
        </span>
        <span className="text-xs font-semibold text-[var(--color-text-primary)]">
          {target.primary_biomarker.replace(/_/g, " ")}
        </span>
        <span className="ml-auto text-xs tabular-nums text-red-500">
          −{target.total_impact.toFixed(2)}
        </span>
      </div>
      <p className="text-sm font-mono text-[var(--color-text-primary)] truncate">
        {target.file_path}
        {target.primary_function ? ` :: ${target.primary_function}` : ""}
      </p>
      <p className="text-xs text-[var(--color-text-secondary)] line-clamp-2">
        {target.primary_reason}
      </p>
      <div className="flex items-center gap-3 pt-1 text-[11px] text-[var(--color-text-tertiary)]">
        <span>Score {target.score.toFixed(1)}/10</span>
        <span>· {target.nloc} NLOC</span>
        <span>· Effort: {effortLabel[target.effort_bucket]}</span>
        <span>· {target.finding_count} findings</span>
        <span className="ml-auto tabular-nums">
          ratio {target.impact_per_effort.toFixed(2)}
        </span>
      </div>
    </Wrapper>
  );
}
