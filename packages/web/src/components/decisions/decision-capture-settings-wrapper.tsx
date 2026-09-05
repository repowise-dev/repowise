"use client";

import * as React from "react";
import useSWR from "swr";
import { toast } from "sonner";
import { DecisionCaptureSettings } from "@repowise-dev/ui/decisions/decision-capture-settings";
import type { DecisionSettingsUpdate } from "@repowise-dev/types/decisions";
import {
  getDecisionSettings,
  updateDecisionSettings,
} from "@/lib/api/decisions";

/**
 * The host half of the capture settings: the fetch, the write, and the
 * optimistic rollback.
 *
 * The panel writes through `mutate` with the server's response rather than the
 * value it sent, because the resolver answers a wider question than the switch
 * asked: turning one source off can move the preset to `custom`, and turning
 * the master model switch off changes every source's effective status. Echoing
 * back what was sent would leave those reading stale until the next fetch.
 */
export function DecisionCaptureSettingsWrapper({ repoId }: { repoId: string }) {
  const { data, error, mutate, isLoading } = useSWR(
    [`/api/repos/${repoId}/decisions/settings`],
    () => getDecisionSettings(repoId),
  );

  const onChange = React.useCallback(
    async (update: DecisionSettingsUpdate) => {
      try {
        await mutate(
          () => updateDecisionSettings(repoId, update),
          // No optimistic payload: the resolved policy is not derivable from
          // the patch, so guessing it would flicker a wrong effective status.
          // The controls disable themselves for the duration instead.
          { revalidate: false, rollbackOnError: true },
        );
      } catch (err) {
        // A 409 is the panel's own case and it renders its own sentence for
        // it; anything else is worth a toast, because the control has already
        // rolled back and would otherwise fail silently.
        const message = err instanceof Error ? err.message : "";
        if (!/409|conflict/i.test(message)) {
          toast.error(
            message
              ? `Couldn't save capture settings: ${message}`
              : "Couldn't save capture settings.",
          );
        }
        throw err;
      }
    },
    [repoId, mutate],
  );

  return (
    <DecisionCaptureSettings
      settings={data}
      onChange={onChange}
      error={error}
      isLoading={isLoading}
      onRetry={() => void mutate()}
    />
  );
}
