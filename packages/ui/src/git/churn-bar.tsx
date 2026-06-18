import { cn } from "../lib/cn";

interface ChurnBarProps {
  percentile: number;
  className?: string;
}

export function ChurnBar({ percentile, className }: ChurnBarProps) {
  const color =
    percentile >= 75
      ? "bg-[var(--color-error)]"
      : percentile >= 50
        ? "bg-[var(--color-warning)]"
        : "bg-[var(--color-success)]";

  return (
    <div className={cn("h-1.5 w-full rounded-full bg-[var(--color-bg-elevated)]", className)}>
      <div
        className={cn("h-1.5 rounded-full transition-all", color)}
        style={{ width: `${Math.min(100, Math.max(0, percentile))}%` }}
      />
    </div>
  );
}
