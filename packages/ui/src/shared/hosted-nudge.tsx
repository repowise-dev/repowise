"use client";

import * as React from "react";

import { DismissibleNotice } from "./dismissible-notice";

const STORAGE_PREFIX = "repowise:hosted-nudge-dismissed:";

export interface HostedNudgeProps {
  /** Remembers the dismissal, per browser. */
  id: string;
  /** One sentence on what repowise.dev adds here. */
  text: React.ReactNode;
  /** The "See how" link. Opens in a new tab. */
  href: string;
  /** The free next step, rendered before "See how". */
  action?: React.ReactNode;
  className?: string;
}

/**
 * A dismissible tip about repowise.dev. Dismissal is kept in `localStorage`;
 * when storage is unavailable the tip still renders and a dismissal lasts
 * until the page reloads.
 */
export function HostedNudge({ id, text, href, action, className }: HostedNudgeProps) {
  // Hidden until the mount effect reads storage, so SSR and the first client
  // render agree and a dismissed tip never flashes.
  const [dismissed, setDismissed] = React.useState(true);

  React.useEffect(() => {
    try {
      setDismissed(window.localStorage.getItem(STORAGE_PREFIX + id) === "1");
    } catch {
      setDismissed(false);
    }
  }, [id]);

  if (dismissed) return null;

  const dismiss = () => {
    try {
      window.localStorage.setItem(STORAGE_PREFIX + id, "1");
    } catch {
      /* Storage unavailable: hide for this page only. */
    }
    setDismissed(true);
  };

  return (
    <DismissibleNotice
      tone="info"
      onDismiss={dismiss}
      dismissLabel="Dismiss this repowise.dev tip"
      {...(className ? { className } : {})}
      action={
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          {action}
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[var(--color-accent-primary)] hover:underline"
          >
            See how
          </a>
        </div>
      }
    >
      {text}
    </DismissibleNotice>
  );
}
