import { coverageColor as color } from "./tokens";

export interface CoverageBarProps {
  value: number | null | undefined;
  label?: string;
  size?: "sm" | "md";
}

export function CoverageBar({ value, label, size = "md" }: CoverageBarProps) {
  const pct = value == null ? 0 : Math.max(0, Math.min(100, value));
  const has = value != null;
  const h = size === "sm" ? "h-1.5" : "h-2";
  return (
    <div className="flex items-center gap-2">
      <div
        className={`relative flex-1 ${h} rounded-full bg-[var(--color-bg-muted)] overflow-hidden`}
      >
        {has && (
          <div
            className={`absolute inset-y-0 left-0 ${color(pct)} rounded-full transition-all`}
            style={{ width: `${pct}%` }}
          />
        )}
      </div>
      <span className="text-xs tabular-nums text-[var(--color-text-secondary)] w-12 text-right">
        {has ? `${pct.toFixed(0)}%` : "—"}
      </span>
      {label && (
        <span className="text-xs text-[var(--color-text-tertiary)]">{label}</span>
      )}
    </div>
  );
}
