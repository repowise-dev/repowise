export interface BiomarkerFinding {
  id: string;
  file_path: string;
  biomarker_type: string;
  severity: "low" | "medium" | "high" | "critical";
  function_name: string | null;
  health_impact: number;
  reason: string;
}

export interface BiomarkerListProps {
  findings: BiomarkerFinding[];
}

const severityColor: Record<BiomarkerFinding["severity"], string> = {
  critical: "bg-red-500/15 text-red-500",
  high: "bg-amber-500/15 text-amber-500",
  medium: "bg-yellow-500/15 text-yellow-500",
  low: "bg-blue-500/15 text-blue-500",
};

export function BiomarkerList({ findings }: BiomarkerListProps) {
  if (findings.length === 0) {
    return (
      <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-6 text-sm text-[var(--color-text-secondary)]">
        No biomarker findings.
      </div>
    );
  }
  return (
    <ul className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] divide-y divide-[var(--color-border-default)]">
      {findings.map((f) => (
        <li key={f.id} className="p-3 space-y-1">
          <div className="flex items-center gap-2">
            <span
              className={`inline-block rounded px-2 py-0.5 text-[10px] uppercase font-semibold ${severityColor[f.severity]}`}
            >
              {f.severity}
            </span>
            <span className="text-xs font-medium text-[var(--color-text-primary)]">
              {f.biomarker_type.replace(/_/g, " ")}
            </span>
            <span className="ml-auto text-xs tabular-nums text-red-500">
              −{f.health_impact.toFixed(2)}
            </span>
          </div>
          <p className="text-xs text-[var(--color-text-secondary)] truncate font-mono">
            {f.file_path}
            {f.function_name ? ` :: ${f.function_name}` : ""}
          </p>
          <p className="text-xs text-[var(--color-text-tertiary)] line-clamp-2">{f.reason}</p>
        </li>
      ))}
    </ul>
  );
}
