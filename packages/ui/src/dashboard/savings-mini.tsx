import { Sparkles } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { formatCost, formatTokens } from "../lib/format";

/** Structural slice of the savings report this tile renders — the full
 *  savings payload and the overview-summary headline both fit. */
export interface SavingsMiniData {
  available: boolean;
  saved_input_tokens?: number;
  measured_saved_input_tokens?: number;
  inferred_saved_input_tokens?: number;
  unpriced_saved_input_tokens?: number;
  estimated_usd_saved?: number;
}

interface SavingsMiniProps {
  /** The savings report (or the overview-summary headline);
   *  null/undefined when unavailable. */
  data?: SavingsMiniData | null;
  /** Repo id, for the "View costs →" link. */
  repoId: string;
  /**
   * Whether savings can ever be tracked on this deployment. Savings come from
   * a local `.repowise/omissions` sidecar written by the CLI, so hosted has no
   * way to populate them: it passes `false` to get the indexing-cost framing
   * instead of an empty state pitching a CLI the viewer isn't running.
   * Defaults to true (the local CLI dashboard).
   */
  trackable?: boolean;
}

/**
 * Compact overview tile for agent token savings — the headline number, what the
 * priced part of it was worth, and the measured-vs-inferred evidence split,
 * with a jump to the full Costs results page.
 *
 * The evidence split rides on the tile rather than the surface split because a
 * lone headline reads as one confident number, and a counterfactual estimate
 * and a measured before/after are not the same claim.
 */
export function SavingsMini({ data, repoId, trackable = true }: SavingsMiniProps) {
  const total = data?.saved_input_tokens ?? 0;
  const measured = data?.measured_saved_input_tokens ?? 0;
  const inferred = data?.inferred_saved_input_tokens ?? 0;
  const hasData = !!data?.available && total > 0;
  const costsHref = `/repos/${repoId}/costs`;

  const measuredPct = total > 0 ? Math.round((measured / total) * 100) : 0;
  const inferredPct = 100 - measuredPct;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center justify-between">
          <span className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-[var(--color-accent-secondary)]" />
            {trackable ? "Agent savings" : "Costs"}
          </span>
          <a
            href={costsHref}
            className="text-[10px] text-[var(--color-accent-primary)] hover:underline font-normal"
          >
            View costs →
          </a>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        {hasData ? (
          <div className="space-y-3">
            <div className="flex items-baseline gap-2">
              <span className="text-3xl font-semibold tabular-nums text-[var(--color-text-primary)]">
                {formatTokens(total)}
              </span>
              <span className="text-base font-semibold tabular-nums text-[var(--color-success)]">
                {formatCost(data!.estimated_usd_saved ?? 0)}
              </span>
            </div>
            <p className="text-xs text-[var(--color-text-secondary)] -mt-1">
              {inferred > 0 ? "estimated tokens saved for your agent" : "tokens saved for your agent"}
              {(data!.unpriced_saved_input_tokens ?? 0) > 0 ? (
                <span className="text-[var(--color-text-tertiary)]">
                  {" "}
                  · some events carry no recorded rate
                </span>
              ) : null}
            </p>

            <div className="flex h-2 w-full overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
              {measured > 0 && (
                <div
                  className="h-full"
                  style={{ width: `${measuredPct}%`, background: "var(--color-accent-fill)" }}
                  title={`Measured — ${formatTokens(measured)}`}
                />
              )}
              {inferred > 0 && (
                <div
                  className="h-full"
                  style={{ width: `${inferredPct}%`, background: "var(--color-accent-secondary)" }}
                  title={`Inferred — ${formatTokens(inferred)}`}
                />
              )}
            </div>
            <div className="space-y-1 text-xs">
              <div className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-1.5 text-[var(--color-text-secondary)] whitespace-nowrap">
                  <span
                    className="h-2 w-2 rounded-full shrink-0"
                    style={{ background: "var(--color-accent-fill)" }}
                  />{" "}
                  Measured
                </span>
                <span className="tabular-nums text-[var(--color-text-tertiary)] shrink-0">
                  {formatTokens(measured)}
                </span>
              </div>
              <div className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-1.5 text-[var(--color-text-secondary)] whitespace-nowrap">
                  <span
                    className="h-2 w-2 rounded-full shrink-0"
                    style={{ background: "var(--color-accent-secondary)" }}
                  />{" "}
                  Inferred
                </span>
                <span className="tabular-nums text-[var(--color-text-tertiary)] shrink-0">
                  {formatTokens(inferred)}
                </span>
              </div>
            </div>
          </div>
        ) : (
          <div className="space-y-2 py-1">
            <p className="text-xs text-[var(--color-text-secondary)] leading-snug">
              {trackable ? (
                <>
                  Trim what your agent reads with <code>repowise distill</code> and the MCP tools —
                  savings show up here.
                </>
              ) : (
                // Agent savings are written by the local CLI sidecar, so there
                // is nothing to earn here on hosted. Point at what the Costs
                // page *does* show instead of teasing a number that never lands.
                <>What this repo&apos;s indexing and doc generation cost, per sync.</>
              )}
            </p>
            <a
              href={costsHref}
              className="inline-block text-xs text-[var(--color-accent-primary)] hover:underline"
            >
              Open the Costs page →
            </a>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
