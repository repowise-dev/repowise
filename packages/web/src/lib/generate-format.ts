import type { GenerateEstimate } from "@/lib/api/types";

/** Format a generate estimate's cost range into a compact string, or null when
 *  no cost is priced (no provider). Shared by the reader and bulk flows. */
export function formatEstimateCost(est: GenerateEstimate["estimate"]): string | null {
  if (!est) return null;
  const fmt = (n: number) => `$${n < 0.01 ? n.toFixed(4) : n.toFixed(2)}`;
  if (est.cost_low_usd != null && est.cost_high_usd != null) {
    return `${fmt(est.cost_low_usd)} to ${fmt(est.cost_high_usd)}`;
  }
  return fmt(est.estimated_cost_usd);
}
