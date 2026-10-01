"use client";

import * as React from "react";
import { Info, X } from "lucide-react";
import { cn } from "../lib/cn";

const STORAGE_PREFIX = "repowise:release-notice-dismissed:";

export interface ReleaseNoticeProps {
  /** Stable id for this notice, e.g. `"health-scoring"`. Combined with
   *  `version` to key the dismissal, so a later release that changes the same
   *  thing again shows its notice once more. */
  id: string;
  /** The release this notice belongs to. Omit only if the notice is not tied
   *  to one, in which case dismissal is permanent. */
  version?: string | null;
  children: React.ReactNode;
  className?: string;
}

/**
 * A one-time, dismissible "this changed in the release you just installed"
 * notice. Lives in the shared package so both the web app and hosted can show
 * it; the web app's `UpgradeBanner` does a different job (an upgrade is
 * *available*) and is not what this replaces.
 *
 * Dismissal is best-effort `localStorage`: blocked site data leaves the notice
 * showing rather than throwing, which is the right failure for something whose
 * purpose is to be read once.
 */
export function ReleaseNotice({ id, version, children, className }: ReleaseNoticeProps) {
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
    <div
      role="status"
      className={cn(
        "flex items-start gap-3 rounded-md border border-[var(--color-border-default)]",
        "bg-[var(--color-bg-elevated)] px-4 py-3 text-sm",
        className,
      )}
    >
      <Info
        className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-text-tertiary)]"
        aria-hidden="true"
      />
      <div className="flex-1 text-[var(--color-text-secondary)]">{children}</div>
      <button
        type="button"
        onClick={dismiss}
        aria-label="Dismiss"
        className="rounded p-1 text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-wash-hover)] hover:text-[var(--color-text-primary)]"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}
