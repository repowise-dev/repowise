"use client";

import * as React from "react";
import { cn } from "../lib/cn";
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
  /** Bold lead, e.g. "Health scores changed." */
  title?: React.ReactNode;
  /** One short clause that shares the title's line. Longer copy goes in
   *  `detail`. */
  children: React.ReactNode;
  /** The longer explanation, opened beneath the line by a "What changed"
   *  toggle. */
  detail?: React.ReactNode;
  /** The toggle's words, for hosts that localize. */
  detailLabel?: string;
  hideLabel?: string;
  /** Optional link at the end of the line, e.g. "See what changed". */
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
 * One thin line, so it does not outrank the page it sits on: the title, one
 * short clause as `children`, then the toggle and the link. Anything longer
 * goes in `detail`, which opens beneath the line, or behind `action`. On a
 * phone the line may wrap to two. The visual is the shared
 * {@link DismissibleNotice}; this owns only the line's layout and whether the
 * reader has dismissed it.
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
  const storageKey = dismissalKey(id, version);
  const hostOwned = !!onDismiss || !storageKey;
  // Start dismissed so it does not flash in before the stored answer is read.
  const [dismissed, setDismissed] = React.useState(!hostOwned);
  const [open, setOpen] = React.useState(false);
  const detailId = React.useId();

  React.useEffect(() => {
    if (hostOwned || !storageKey) return;
    try {
      setDismissed(!!window.localStorage.getItem(storageKey));
    } catch {
      setDismissed(false);
    }
  }, [hostOwned, storageKey]);

  if (dismissed) return null;

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
      className={cn("py-1.5", className)}
    >
      {title ? (
        <>
          <span className="font-medium text-[var(--color-text-primary)]">{title}</span>{" "}
        </>
      ) : null}
      {children}
      {detail ? (
        <button
          type="button"
          aria-expanded={open}
          aria-controls={detailId}
          onClick={() => setOpen((o) => !o)}
          className="ml-2 whitespace-nowrap rounded text-[var(--color-accent-primary)] underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
        >
          {open ? hideLabel : detailLabel}
        </button>
      ) : null}
      {action ? <span className="ml-2 whitespace-nowrap">{action}</span> : null}
      {detail ? (
        <div id={detailId} hidden={!open} className="mt-1.5">
          {detail}
        </div>
      ) : null}
    </DismissibleNotice>
  );
}

function dismissalKey(id: string | undefined, version: string | null | undefined): string | null {
  return id ? STORAGE_PREFIX + id + (version ? `:v${version}` : "") : null;
}
