import { cn } from "../lib/cn";

interface ChurnBarProps {
  percentile: number;
  className?: string;
}

export function ChurnBar({ percentile, className }: ChurnBarProps) {
  const color =
    percentile >= 75
      ? "bg-red-500"
      : percentile >= 50
        ? "bg-yellow-500"
        : "bg-green-500";

  return (
    <div className={cn("h-1.5 w-full rounded-full bg-[var(--color-bg-elevated)]", className)}>
      <div
        className={cn("h-1.5 rounded-full transition-all", color)}
        style={{ width: `${Math.min(100, Math.max(0, percentile))}%` }}
      />
    </div>
  );
}
