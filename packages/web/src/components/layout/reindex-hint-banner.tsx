"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, X } from "lucide-react";
import useSWR from "swr";
import { getMetaVersion } from "@/lib/api/meta";

const STORAGE_PREFIX = "repowise:reindex-hint-dismissed:";

export interface ReindexHintBannerProps {
  repoId: string;
}

/**
 * Per-repo notice shown only when the store format of this repo is behind the
 * running build in a way that recommends a reindex. We recommend, never force:
 * this is purely informational and dismissible. Renders nothing in the common
 * case (store compatible) or if the check fails.
 */
export function ReindexHintBanner({ repoId }: ReindexHintBannerProps) {
  const { data: meta } = useSWR(
    ["meta-version", repoId],
    () => getMetaVersion(repoId),
    { revalidateOnFocus: false, revalidateOnReconnect: false },
  );
  const [dismissed, setDismissed] = useState(true);

  const recommended = meta?.reindex_recommended === true;
  const command = meta?.reindex_command ?? null;

  useEffect(() => {
    if (!recommended || typeof window === "undefined") return;
    try {
      setDismissed(!!window.localStorage.getItem(STORAGE_PREFIX + repoId));
    } catch {
      setDismissed(false);
    }
  }, [recommended, repoId]);

  if (!recommended || dismissed) return null;

  const dismiss = () => {
    try {
      window.localStorage.setItem(STORAGE_PREFIX + repoId, "1");
    } catch {
      /* localStorage unavailable - hide for this session only. */
    }
    setDismissed(true);
  };

  return (
    <div
      role="status"
      className="flex items-start gap-3 border-b border-[var(--color-border-default)] bg-[var(--color-accent-muted)] px-4 py-2 text-sm"
    >
      <AlertTriangle
        className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-accent-primary)]"
        aria-hidden="true"
      />
      <div className="flex-1 text-[var(--color-text-primary)]">
        This index was built by an older repowise. It still works, but a reindex is
        recommended to pick up the latest format.
        {command && (
          <>
            {" "}
            <code className="rounded bg-[var(--color-bg-elevated)] px-1.5 py-0.5 text-xs">
              {command}
            </code>
          </>
        )}
      </div>
      <button
        type="button"
        onClick={dismiss}
        aria-label="Dismiss"
        className="rounded p-1 text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}
