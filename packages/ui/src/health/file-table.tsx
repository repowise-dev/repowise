export interface HealthFileRow {
  file_path: string;
  score: number;
  max_ccn: number;
  max_nesting: number;
  nloc: number;
  has_test_file: boolean;
}

export interface HealthFileTableProps {
  files: HealthFileRow[];
}

function scoreBadge(score: number): string {
  if (score < 4) return "bg-red-500/15 text-red-500";
  if (score < 7) return "bg-amber-500/15 text-amber-500";
  return "bg-emerald-500/15 text-emerald-500";
}

export function HealthFileTable({ files }: HealthFileTableProps) {
  if (files.length === 0) {
    return (
      <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-6 text-sm text-[var(--color-text-secondary)]">
        No health metrics yet. Run <code>repowise init</code> or <code>repowise health</code>.
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-[var(--color-bg-elevated)] text-[var(--color-text-tertiary)] text-xs uppercase tracking-wider">
          <tr>
            <th className="text-left px-3 py-2 font-medium">File</th>
            <th className="text-right px-3 py-2 font-medium">Score</th>
            <th className="text-right px-3 py-2 font-medium">CCN</th>
            <th className="text-right px-3 py-2 font-medium">Nest</th>
            <th className="text-right px-3 py-2 font-medium">NLOC</th>
            <th className="text-center px-3 py-2 font-medium">Tests</th>
          </tr>
        </thead>
        <tbody>
          {files.map((f) => (
            <tr
              key={f.file_path}
              className="border-t border-[var(--color-border-default)] hover:bg-[var(--color-bg-elevated)]"
            >
              <td className="px-3 py-2 text-[var(--color-text-primary)] font-mono text-xs truncate max-w-[400px]">
                {f.file_path}
              </td>
              <td className="px-3 py-2 text-right">
                <span
                  className={`inline-block rounded px-2 py-0.5 text-xs font-semibold tabular-nums ${scoreBadge(f.score)}`}
                >
                  {f.score.toFixed(1)}
                </span>
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-[var(--color-text-secondary)]">
                {f.max_ccn}
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-[var(--color-text-secondary)]">
                {f.max_nesting}
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-[var(--color-text-secondary)]">
                {f.nloc}
              </td>
              <td className="px-3 py-2 text-center text-xs">
                {f.has_test_file ? (
                  <span className="text-emerald-500">✓</span>
                ) : (
                  <span className="text-[var(--color-text-tertiary)]">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
