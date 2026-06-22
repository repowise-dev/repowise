"use client";

import { useState } from "react";
import { useMetaVersion } from "@/lib/hooks/use-meta-version";
import { WhatsNewModal } from "./whats-new-modal";

/**
 * Dynamic version label for the sidebar / mobile-nav footers. Shows the running
 * server version (falling back to a static label until it loads) and an
 * "update available" dot that opens the what's-new view.
 */
export function VersionFooter() {
  const { meta } = useMetaVersion();
  const [showWhatsNew, setShowWhatsNew] = useState(false);

  const version = meta?.server_version;
  const updateAvailable = meta?.update_available === true;

  return (
    <>
      <button
        type="button"
        onClick={() => setShowWhatsNew(true)}
        className="flex items-center gap-1.5 text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]"
      >
        <span>repowise{version ? ` v${version}` : ""}</span>
        {updateAvailable && (
          <span
            className="h-1.5 w-1.5 rounded-full bg-[var(--color-accent-primary)]"
            aria-label="Update available"
          />
        )}
      </button>
      <WhatsNewModal open={showWhatsNew} onOpenChange={setShowWhatsNew} />
    </>
  );
}
