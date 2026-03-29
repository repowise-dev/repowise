import { truncatePath } from "@/lib/utils/format";

interface CoChangeListProps {
  partners: Array<{ file_path: string; co_change_count: number }>;
}

export function CoChangeList({ partners }: CoChangeListProps) {
  if (partners.length === 0) return null;

  const maxCount = Math.max(...partners.map((p) => p.co_change_count));

  return (
    <div className="space-y-1.5">
      {partners.slice(0, 5).map((p) => (
        <div key={p.file_path} className="space-y-0.5">
          <div className="flex items-center justify-between text-xs">
            <span className="font-mono text-[var(--color-text-secondary)] truncate flex-1 min-w-0" title={p.file_path}>
              {truncatePath(p.file_path, 45)}
            </span>
            <span className="text-[var(--color-text-tertiary)] tabular-nums shrink-0 ml-1">
              {p.co_change_count}&times;
            </span>
          </div>
          <div className="h-1 w-full rounded-full bg-[var(--color-bg-elevated)]">
            <div
              className="h-1 rounded-full bg-[var(--color-accent-primary)] transition-all"
              style={{
                width: `${maxCount > 0 ? (p.co_change_count / maxCount) * 100 : 0}%`,
              }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
