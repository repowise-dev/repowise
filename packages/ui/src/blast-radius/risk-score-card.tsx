import { Card, CardContent } from "../ui/card";
import { cn } from "../lib/cn";

interface RiskScoreCardProps {
  /** 0–10. */
  score: number;
}

export function RiskScoreCard({ score }: RiskScoreCardProps) {
  const tone =
    score >= 7
      ? {
          text: "text-[var(--color-error)]",
          card: "border-[var(--color-error)]/30 bg-[var(--color-error)]/5",
        }
      : score >= 4
        ? {
            text: "text-[var(--color-warning)]",
            card: "border-[var(--color-warning)]/30 bg-[var(--color-warning)]/5",
          }
        : {
            text: "text-[var(--color-success)]",
            card: "border-[var(--color-success)]/30 bg-[var(--color-success)]/5",
          };
  const label = score >= 7 ? "High Risk" : score >= 4 ? "Medium Risk" : "Low Risk";

  return (
    <Card className={cn("border", tone.card)}>
      <CardContent className="flex flex-col items-center justify-center py-8 gap-2">
        <span className={cn("text-5xl font-bold tabular-nums", tone.text)}>
          {score.toFixed(1)}
        </span>
        <span className={cn("text-sm font-medium", tone.text)}>{label}</span>
        <span className="text-xs text-[var(--color-text-tertiary)]">
          Overall Risk Score (0–10)
        </span>
      </CardContent>
    </Card>
  );
}
