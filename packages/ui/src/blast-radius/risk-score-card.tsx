import { Card, CardContent } from "../ui/card";
import { cn } from "../lib/cn";

interface RiskScoreCardProps {
  /** 0–10. */
  score: number;
}

export function RiskScoreCard({ score }: RiskScoreCardProps) {
  const color =
    score >= 7
      ? "text-red-500 border-red-500/30 bg-red-500/5"
      : score >= 4
        ? "text-amber-500 border-amber-500/30 bg-amber-500/5"
        : "text-emerald-500 border-emerald-500/30 bg-emerald-500/5";
  const label = score >= 7 ? "High Risk" : score >= 4 ? "Medium Risk" : "Low Risk";

  return (
    <Card className={cn("border", color)}>
      <CardContent className="flex flex-col items-center justify-center py-8 gap-2">
        <span className={cn("text-6xl font-bold tabular-nums", color.split(" ")[0])}>
          {score.toFixed(1)}
        </span>
        <span className={cn("text-sm font-medium", color.split(" ")[0])}>{label}</span>
        <span className="text-xs text-[var(--color-text-tertiary)]">
          Overall Risk Score (0–10)
        </span>
      </CardContent>
    </Card>
  );
}
