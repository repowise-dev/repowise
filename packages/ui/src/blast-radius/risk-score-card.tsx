import { Card, CardContent } from "../ui/card";
import { cn } from "../lib/cn";
import type { BlastRadiusResponse } from "@repowise-dev/types/blast-radius";

interface RiskScoreCardProps {
  /** Uncalibrated structural-impact heuristic, 0–10. */
  score: number;
  /** Server-classified band. Omit for legacy payloads; no client threshold is inferred. */
  band?: BlastRadiusResponse["structural_impact_band"];
}

/** Legacy component name retained for callers; copy uses structural semantics. */
export function RiskScoreCard({ score, band }: RiskScoreCardProps) {
  const tone =
    band === "broad"
      ? {
          text: "text-[var(--color-error)]",
          card: "border-[var(--color-error)]/30 bg-[var(--color-error)]/5",
        }
      : band === "moderate"
        ? {
            text: "text-[var(--color-warning)]",
            card: "border-[var(--color-warning)]/30 bg-[var(--color-warning)]/5",
          }
        : band === "localized"
          ? {
            text: "text-[var(--color-success)]",
            card: "border-[var(--color-success)]/30 bg-[var(--color-success)]/5",
            }
          : {
              text: "text-[var(--color-info)]",
              card: "border-[var(--color-info)]/30 bg-[var(--color-info)]/5",
            };
  const label =
    band === "broad"
      ? "Broad structural impact"
      : band === "moderate"
        ? "Moderate structural impact"
        : band === "localized"
          ? "Localized structural impact"
          : "Structural impact";

  return (
    <Card className={cn("border", tone.card)}>
      <CardContent className="flex flex-col items-center justify-center py-8 gap-2">
        <span className={cn("text-5xl font-bold tabular-nums", tone.text)}>
          {score.toFixed(1)}
        </span>
        <span className={cn("text-sm font-medium", tone.text)}>{label}</span>
        <span className="text-xs text-[var(--color-text-tertiary)]">
          Uncalibrated structural heuristic (0–10)
        </span>
      </CardContent>
    </Card>
  );
}
