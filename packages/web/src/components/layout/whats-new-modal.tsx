"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@repowise-dev/ui/ui/dialog";
import type { ChangelogEntry } from "@/lib/api/meta";
import { useChangelog } from "@/lib/hooks/use-meta-version";
import { highlightRepoId } from "@/lib/release-moment";
import { useTranslations } from "next-intl";
import { MOMENT_GROUND, MOMENT_GUTTER, MOMENT_MICRO, MOMENT_SHEET } from "./moment-shell";
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
}

/** repowise's release notes from the bundled changelog: the chosen release on
 *  top, earlier ones a click away. */
export function WhatsNewModal({ open, onOpenChange, version, fresh = false }: WhatsNewModalProps) {
  const t = useTranslations("shell");
  const pathname = usePathname();
  // Only fetch once the modal is opened.
  const { entries, error, isLoading } = useChangelog(open);
  const [selected, setSelected] = useState<string | null>(null);
  const scroller = useRef<HTMLDivElement>(null);

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
      <DialogTitle className={`${MOMENT_MICRO} pr-10`}>
        {t("whatsNewModal.title")}
      </DialogTitle>
      <DialogDescription className="sr-only">{t("whatsNewModal.description")}</DialogDescription>
    </DialogHeader>
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* Its own deeper ground and a glow band: a moment presents instead of
          asking. */}
      <DialogContent
        data-moment
        className={`${MOMENT_GROUND} ${MOMENT_SHEET} min-[561px]:h-auto min-[561px]:max-w-3xl`}
      >
        {/* One scroll for band and notes; the close button stays put. */}
        <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
          {entry ? (
            <ReleaseHeader key={`h-${entry.version}`} entry={entry} title={title} />
          ) : (
            <div className={`${MOMENT_GUTTER} pt-6 min-[561px]:pt-8`}>{title}</div>
          )}

          <div className={`${MOMENT_GUTTER} space-y-10 pb-[max(2rem,env(safe-area-inset-bottom))] pt-6`}>
            {isLoading && (
              <p className="text-xs text-[var(--color-text-tertiary)]">{t("whatsNewModal.loading")}</p>
            )}
            {error && (
              <p className="text-xs text-[var(--color-text-tertiary)]">
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
              <EarlierReleases
                entries={earlier}
                onSelect={(v) => {
                  setSelected(v);
                  scroller.current?.scrollTo?.({ top: 0 });
                }}
              />
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** The switcher to earlier releases, as quiet version chips. */
function EarlierReleases({
  entries,
  onSelect,
}: {
  entries: ChangelogEntry[];
  onSelect: (version: string) => void;
}) {
  const t = useTranslations("shell.whatsNewModal");
  return (
    <nav aria-label={t("earlier")} className="space-y-3">
      <p className={MOMENT_MICRO}>{t("earlier")}</p>
      <ul className="flex flex-wrap gap-2">
        {entries.map((e) => (
          <li key={e.version}>
            <button
              type="button"
              onClick={() => onSelect(e.version)}
              className="inline-flex h-11 items-center gap-2 rounded-lg border border-[var(--color-border-default)] px-3 font-mono text-xs tabular-nums text-[var(--color-text-primary)] transition-colors hover:border-[var(--color-border-hover)] hover:bg-[var(--color-bg-elevated)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)] min-[561px]:h-8"
            >
              {releaseName(e.version, t("nextRelease"))}
              {e.label && <span className="text-[var(--color-text-tertiary)]">{e.label}</span>}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
