"use client";

import { SWRConfig } from "swr";

/**
 * App-wide SWR defaults. Without this, SWR refetches every active key whenever
 * the window regains focus or the network reconnects — which makes pages
 * flicker and feel sluggish each time you tab back in. Most call sites already
 * opt into these flags individually; setting them globally covers the rest and
 * keeps behaviour consistent. `dedupingInterval` collapses duplicate requests
 * for the same key fired close together (e.g. several widgets on one page).
 */
export function SWRProvider({ children }: { children: React.ReactNode }) {
  return (
    <SWRConfig
      value={{
        revalidateOnFocus: false,
        revalidateOnReconnect: false,
        dedupingInterval: 60_000,
      }}
    >
      {children}
    </SWRConfig>
  );
}
