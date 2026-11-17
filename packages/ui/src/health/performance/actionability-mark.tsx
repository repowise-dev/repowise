import type { PerformanceActionabilityState } from "@repowise-dev/types/health";

import { MICRO } from "../tokens";
import { ACTIONABILITY_INK, ACTIONABILITY_LABEL } from "./presentation";

/**
 * A cause's actionability as a dot and a word, the way a finding shows its
 * severity (`SeverityMark`): never a filled pill. The ink is the bar's
 * neutral family, so the row and the bar above it agree.
 */
export function ActionabilityMark({ state }: { state: PerformanceActionabilityState }) {
  return (
    <span className={`inline-flex shrink-0 items-center gap-1.5 ${MICRO}`}>
      <span
        aria-hidden
        className="h-1.5 w-1.5 shrink-0 rounded-full"
        style={{ backgroundColor: ACTIONABILITY_INK[state] }}
      />
      {ACTIONABILITY_LABEL[state]}
    </span>
  );
}
