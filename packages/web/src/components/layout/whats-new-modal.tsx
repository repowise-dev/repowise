"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@repowise-dev/ui/ui/dialog";
import { useChangelog } from "@/lib/hooks/use-meta-version";
import { highlightRepoId } from "@/lib/release-moment";
import { useTranslations } from "next-intl";
import { ReleaseHeader, ReleaseNotes, releaseName } from "./release-notes";

/** How many earlier releases the switcher lists. */
const EARLIER_SHOWN = 8;

export interface WhatsNewModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Release to open on. Defaults to the newest entry in the changelog. */
  version?: string | null;
  /** The reader just upgraded to `version`: its highlights read "New". */
  fresh?: boolean;
  /** Also list releases newer than the running version (the upgrade banner). */
  includeNewer?: boolean;
}

/** repowise's release notes from the bundled changelog (plus newer releases
 *  when `includeNewer`): the chosen release on top, earlier ones a click away. */
export function WhatsNewModal({
  open,
  onOpenChange,
  version,
  fresh = false,
  includeNewer = false,
}: WhatsNewModalProps) {
  const t = useTranslations("shell");
  const pathname = usePathname();
  // Only fetch once the modal is opened.
  const { entries, error, isLoading } = useChangelog(open, undefined, includeNewer);
  const [selected, setSelected] = useState<string | null>(null);

  // Each opening starts on the release it was opened for.
  useEffect(() => {
    if (open) setSelected(null);
  }, [open, version]);

  // With no version to open on (a preview), the next release leads when it
  // has highlights; in a released build there is no such entry.
  const fallback =
    entries.find((e) => e.version === version) ??
    entries.find((e) => e.version === "unreleased" && (e.highlights?.length ?? 0) > 0) ??
    entries[0];
  const entry = entries.find((e) => e.version === selected) ?? fallback;
  const earlier = entries.filter((e) => e !== entry).slice(0, EARLIER_SHOWN);

  const title = (
    <DialogHeader>
      <DialogTitle className="font-mono text-[10px] font-normal uppercase tracking-[0.12em] text-[var(--color-text-secondary)]">
        {t("whatsNewModal.title")}
      </DialogTitle>
      <DialogDescription className="sr-only">{t("whatsNewModal.description")}</DialogDescription>
    </DialogHeader>
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* Its own deeper ground and a glow band: the release moment is the one
          dialog that presents rather than asks. */}
      <DialogContent className="max-h-[85vh] gap-0 overflow-y-auto bg-[var(--color-release-ground)] p-0 sm:max-w-3xl">
        {entry ? (
          <ReleaseHeader key={`h-${entry.version}`} entry={entry} title={title} />
        ) : (
          <div className="px-6 pt-6 sm:px-8">{title}</div>
        )}

        <div className="space-y-8 px-6 py-6 sm:px-8">
        {isLoading && (
          <p className="text-sm text-[var(--color-text-tertiary)]">
            {t("whatsNewModal.loading")}
          </p>
        )}
        {error && (
          <p className="text-sm text-[var(--color-text-tertiary)]">
            {t.rich("whatsNewModal.unavailable", {
              link: (chunks) => (
                <a
                  href="https://github.com/repowise-dev/repowise/releases"
                  target="_blank"
                  rel="noreferrer"
                  className="text-[var(--color-accent-primary)] underline"
                >
                  {chunks}
                </a>
              ),
            })}
          </p>
        )}

        {entry && (
          <ReleaseNotes
            key={entry.version}
            entry={entry}
            repoId={highlightRepoId(pathname)}
            fresh={fresh && entry === fallback}
            onNavigate={() => onOpenChange(false)}
          />
        )}

        {earlier.length > 0 && (
          <nav
            aria-label={t("whatsNewModal.earlier")}
            className="space-y-1.5 border-t border-[var(--color-border-default)] pt-4"
          >
            <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
              {t("whatsNewModal.earlier")}
            </p>
            <ul className="flex flex-wrap gap-x-4 gap-y-1">
              {earlier.map((e) => (
                <li key={e.version}>
                  <button
                    type="button"
                    onClick={() => setSelected(e.version)}
                    className="rounded text-xs text-[var(--color-accent-primary)] underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
                  >
                    {releaseName(e.version, t("whatsNewModal.nextRelease"))}
                    {e.label && (
                      <span className="ml-1 text-[var(--color-text-tertiary)]">{e.label}</span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          </nav>
        )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
