"use client";

import { useEffect, useState } from "react";
import { ArrowUpCircle, X } from "lucide-react";
import { useMetaVersion } from "@/lib/hooks/use-meta-version";
import { WhatsNewModal } from "./whats-new-modal";

const STORAGE_PREFIX = "repowise:upgrade-banner-dismissed:v";

/**
 * Global, non-blocking "newer release available" banner. Dismissal is keyed per
 * version, so a fresh release re-shows it once. Never an error if the version
 * check fails - it simply renders nothing.
 */
export function UpgradeBanner() {
  const { meta } = useMetaVersion();
  const [dismissed, setDismissed] = useState(true);
  const [showWhatsNew, setShowWhatsNew] = useState(false);

  const latest = meta?.latest_version ?? null;
  const updateAvailable = meta?.update_available === true && latest !== null;

  useEffect(() => {
    if (!updateAvailable || typeof window === "undefined") return;
    try {
      setDismissed(!!window.localStorage.getItem(STORAGE_PREFIX + latest));
    } catch {
      setDismissed(false);
    }
  }, [updateAvailable, latest]);

  if (!updateAvailable || dismissed) return null;

  const dismiss = () => {
    try {
      window.localStorage.setItem(STORAGE_PREFIX + latest, "1");
    } catch {
      /* localStorage unavailable - hide for this session only. */
    }
    setDismissed(true);
  };

  return (
    <>
      <div
        role="status"
        className="flex items-center gap-3 border-b border-[var(--color-border-default)] bg-[var(--color-accent-muted)] px-4 py-2 text-sm"
      >
        <ArrowUpCircle
          className="h-4 w-4 shrink-0 text-[var(--color-accent-primary)]"
          aria-hidden="true"
        />
        <p className="flex-1 text-[var(--color-text-primary)]">
          repowise <span className="font-medium">v{latest}</span> is available
          {meta?.server_version ? (
            <span className="text-[var(--color-text-tertiary)]"> (you have v{meta.server_version})</span>
          ) : null}
          .{" "}
          {meta?.upgrade_command && (
            <code className="rounded bg-[var(--color-bg-elevated)] px-1.5 py-0.5 text-xs">
              {meta.upgrade_command}
            </code>
          )}{" "}
          <button
            type="button"
            onClick={() => setShowWhatsNew(true)}
            className="text-[var(--color-accent-primary)] underline underline-offset-2 hover:opacity-80"
          >
            What&apos;s new
          </button>
        </p>
        <button
          type="button"
          onClick={dismiss}
          aria-label="Dismiss"
          className="rounded p-1 text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <WhatsNewModal open={showWhatsNew} onOpenChange={setShowWhatsNew} />
    </>
  );
}
