"use client";

import * as React from "react";
import { DismissibleNotice } from "./dismissible-notice";

const STORAGE_PREFIX = "repowise:release-notice-dismissed:";

export interface ReleaseNoticeProps {
  /** Stable id for this notice, e.g. `"health-scoring"`. Combined with
   *  `version` to key the dismissal, so a later release that changes the same
   *  thing again shows its notice once more. Not needed when the host owns
   *  dismissal through `onDismiss`. */
  id?: string;
  /** The release this notice belongs to. Omit only if the notice is not tied
   *  to one, in which case dismissal is permanent. */
  version?: string | null;
  /** Bold lead sentence, e.g. "Savings accounting has been upgraded." */
  title?: React.ReactNode;
  /** The body copy. */
  children: React.ReactNode;
  /** The longer explanation, behind a "What changed" toggle. */
  detail?: React.ReactNode;
  /** The toggle's words, for hosts that localize. */
  detailLabel?: string;
  hideLabel?: string;
  /** Optional link or disclosure, e.g. "See what changed". */
  action?: React.ReactNode;
  /** Host-owned dismissal (for example scoped per repository). When given the
   *  notice stays visible until the host unmounts it and nothing is stored. */
  onDismiss?: (() => void) | undefined;
  dismissLabel?: string;
  className?: string;
}

/**
 * The one "this changed in the release you installed" announcement, used on
 * every surface that explains a release-driven change. Warnings and status
 * belong in `Callout`; an upgrade that is merely available is not this either
 * (the web app's `UpgradeBanner` does that job).
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
  title,
  children,
  detail,
  detailLabel = "What changed",
  hideLabel = "Hide",
  action,
  onDismiss,
  dismissLabel,
  className,
}: ReleaseNoticeProps) {
  const storageKey = id ? STORAGE_PREFIX + id + (version ? `:v${version}` : "") : null;
  const hostOwned = !!onDismiss || !storageKey;
  // Start dismissed so it does not flash in before the stored answer is read.
  const [dismissed, setDismissed] = React.useState(!hostOwned);

  React.useEffect(() => {
    if (hostOwned || !storageKey) return;
    try {
      setDismissed(!!window.localStorage.getItem(storageKey));
    } catch {
      setDismissed(false);
    }
  }, [hostOwned, storageKey]);

  if (dismissed) return null;

  const lead = title ? (
    <>
      <span className="font-medium text-[var(--color-text-primary)]">{title}</span>{" "}
    </>
  ) : null;

  const dismiss = () => {
    if (onDismiss) return onDismiss();
    try {
      if (storageKey) window.localStorage.setItem(storageKey, "1");
    } catch {
      /* localStorage unavailable - hide for this session only. */
    }
    setDismissed(true);
  };

  return (
    <DismissibleNotice
      tone="info"
      onDismiss={dismiss}
      {...(dismissLabel ? { dismissLabel } : {})}
      {...(action ? { action } : {})}
      {...(className ? { className } : {})}
    >
      {detail ? (
        <details className="group/release">
          <summary className="cursor-pointer list-none rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]">
            {lead}
            {children}{" "}
            <span className="text-[var(--color-accent-primary)] underline-offset-2 hover:underline">
              <span className="group-open/release:hidden">{detailLabel}</span>
              <span className="hidden group-open/release:inline">{hideLabel}</span>
            </span>
          </summary>
          <div className="mt-1.5">{detail}</div>
        </details>
      ) : (
        <>
          {lead}
          {children}
        </>
      )}
    </DismissibleNotice>
  );
}
