"use client";

import * as React from "react";
import { DismissibleNotice } from "./dismissible-notice";

const STORAGE_PREFIX = "repowise:release-notice-dismissed:";

export interface ReleaseNoticeProps {
  /** Stable id for this notice, e.g. `"health-scoring"`. Combined with
   *  `version` to key the dismissal, so a later release that changes the same
   *  thing again shows its notice once more. */
  id: string;
  /** The release this notice belongs to. Omit only if the notice is not tied
   *  to one, in which case dismissal is permanent. */
  version?: string | null;
  /** The one line the notice reads as. */
  children: React.ReactNode;
  /** The explanation, behind a "What changed" toggle on the same line. */
  detail?: React.ReactNode;
  /** The toggle's words, for hosts that localize. */
  detailLabel?: string;
  hideLabel?: string;
  className?: string;
}

/**
 * A one-time, dismissible "this changed in the release you just installed"
 * line. Lives in the shared package so both the web app and hosted can show
 * it; the web app's `UpgradeBanner` does a different job (an upgrade is
 * *available*) and is not what this replaces.
 *
 * One line, so it does not outrank the page it sits on; the explanation opens
 * in place. The visual is the shared {@link DismissibleNotice}; this owns only
 * whether the reader has dismissed it.
 *
 * Dismissal is best-effort `localStorage`: blocked site data leaves the notice
 * showing rather than throwing, which is the right failure for something whose
 * purpose is to be read once.
 */
export function ReleaseNotice({
  id,
  version,
  children,
  detail,
  detailLabel = "What changed",
  hideLabel = "Hide",
  className,
}: ReleaseNoticeProps) {
  const storageKey = STORAGE_PREFIX + id + (version ? `:v${version}` : "");
  // Start dismissed so it does not flash in before the stored answer is read.
  const [dismissed, setDismissed] = React.useState(true);

  React.useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      setDismissed(!!window.localStorage.getItem(storageKey));
    } catch {
      setDismissed(false);
    }
  }, [storageKey]);

  if (dismissed) return null;

  const dismiss = () => {
    try {
      window.localStorage.setItem(storageKey, "1");
    } catch {
      /* localStorage unavailable - hide for this session only. */
    }
    setDismissed(true);
  };

  return (
    <DismissibleNotice onDismiss={dismiss} {...(className ? { className } : {})}>
      {detail ? (
        <details className="group/release">
          <summary className="cursor-pointer list-none rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]">
            {children}{" "}
            <span className="text-[var(--color-accent-primary)] underline-offset-2 hover:underline">
              <span className="group-open/release:hidden">{detailLabel}</span>
              <span className="hidden group-open/release:inline">{hideLabel}</span>
            </span>
          </summary>
          <div className="mt-1.5">{detail}</div>
        </details>
      ) : (
        children
      )}
    </DismissibleNotice>
  );
}
