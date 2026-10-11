"use client";

/**
 * Trend section — now the shared {@link TrendView} from `@repowise-dev/ui`.
 * It is purely presentational (data fetched once at the page level and passed
 * in), so web and hosted render the same surface with no host wiring. Kept as
 * a re-export under the original name so the page import is unchanged.
 */

export { TrendView as TrendSection } from "@repowise-dev/ui/health";
