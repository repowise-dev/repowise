"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { X } from "lucide-react";
import { useTranslations } from "next-intl";
import { Callout } from "@repowise-dev/ui/shared/callout";
import { useChangelog, useMetaVersion } from "@/lib/hooks/use-meta-version";
import {
  previewMoment,
  startNewSurfaces,
  takeUpgradeMoment,
  visitNewSurface,
  type UpgradeMoment,
} from "@/lib/release-moment";
import { WhatsNewModal } from "./whats-new-modal";

/**
 * The once-per-version "you just upgraded" moment: the What's new dialog for a
 * minor release, a quiet note for a patch, nothing on a first visit. Also
 * clears a page's "New" mark once it is visited.
 *
 * `?whats-new=preview` (dialog) and `?whats-new=patch` (note) replay it on
 * demand, using the newest changelog entry, without changing what this
 * browser has seen.
 */
export function ReleaseMoment() {
  const t = useTranslations("shell");
  const tc = useTranslations("common");
  const { meta } = useMetaVersion();
  const pathname = usePathname();
  const [moment, setMoment] = useState<UpgradeMoment>("none");
  const [preview, setPreview] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [dialogFromNote, setDialogFromNote] = useState(false);

  const version = meta?.server_version ?? null;

  useEffect(() => {
    if (!version || !meta) return;
    const highlights = meta.highlights ?? [];
    const replay = previewMoment(window.location.search);
    const next = replay ?? takeUpgradeMoment(version, highlights.length > 0);
    setPreview(replay !== null);
    setMoment(next);
    if (next === "dialog") setDialogOpen(true);
    if (next !== "none" && replay === null) startNewSurfaces(version, highlights);
    // Once per loaded version: `meta` refreshes hourly with the same version.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version]);

  // A preview marks the newest entry's pages, which in a development build is
  // the next release's.
  const { entries } = useChangelog(preview);
  const newest = entries[0];
  useEffect(() => {
    if (preview && newest) startNewSurfaces(newest.version, newest.highlights ?? []);
  }, [preview, newest]);

  useEffect(() => {
    if (pathname) visitNewSurface(pathname);
  }, [pathname]);

  if (!version) return null;
  const dialogVersion = preview ? null : version;

  return (
    <>
      {moment === "notice" && (
        <div className="px-3 pt-2 sm:px-[var(--page-pad)]">
          <Callout
            tone="info"
            label={t("updatedLabel")}
            className="py-1.5"
            action={
              <button
                type="button"
                onClick={() => setMoment("none")}
                aria-label={tc("dismiss")}
                className="rounded p-1.5 text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
              >
                <X className="h-4 w-4" />
              </button>
            }
          >
            {t.rich("updatedNotice", {
              version,
              b: (chunks) => <span className="font-medium">{chunks}</span>,
            })}{" "}
            <button
              type="button"
              onClick={() => {
                setDialogFromNote(true);
                setDialogOpen(true);
              }}
              className="text-[var(--color-accent-primary)] underline-offset-2 hover:underline"
            >
              {t("seeWhatChanged")}
            </button>
          </Callout>
        </div>
      )}
      <WhatsNewModal
        open={dialogOpen}
        onOpenChange={(open) => {
          setDialogOpen(open);
          if (!open && dialogFromNote) setMoment("none");
        }}
        version={dialogVersion}
        fresh
      />
    </>
  );
}
