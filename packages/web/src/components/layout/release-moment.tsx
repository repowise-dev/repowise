"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { X } from "lucide-react";
import { useTranslations } from "next-intl";
import { Callout } from "@repowise-dev/ui/shared/callout";
import { useChangelog, useMetaVersion } from "@/lib/hooks/use-meta-version";
import {
  decideMoment,
  rememberRepo,
  startNewSurfaces,
  visitNewSurface,
  type UpgradeMoment,
} from "@/lib/release-moment";
import { WhatsNewModal } from "./whats-new-modal";

/**
 * The once-per-version "you just upgraded" moment: the What's new dialog for a
 * minor release, a quiet note for a patch, nothing on a fresh browser. Also
 * clears a page's "New" mark once it is visited. The decision itself lives in
 * `decideMoment`.
 *
 * `?whats-new=preview` (dialog) and `?whats-new=patch` (note) replay it on
 * demand, using the newest changelog entry, without changing what this
 * browser has seen. Read on a full page load only.
 */
export function ReleaseMoment({ firstRepoId = null }: { firstRepoId?: string | null }) {
  const { version, moment, setMoment, preview } = useReleaseMoment(firstRepoId);
  const [dialogOpen, setDialogOpen] = useState(false);

  useEffect(() => {
    if (moment === "dialog") setDialogOpen(true);
  }, [moment]);

  if (!version) return null;
  return (
    <>
      {moment === "notice" && (
        <UpdatedNote
          version={version}
          onOpen={() => setDialogOpen(true)}
          onDismiss={() => setMoment("none")}
        />
      )}
      <WhatsNewModal
        open={dialogOpen}
        onOpenChange={(open) => {
          setDialogOpen(open);
          if (!open) setMoment("none");
        }}
        version={preview ? null : version}
        fresh
      />
    </>
  );
}

function useReleaseMoment(firstRepoId: string | null) {
  const { meta } = useMetaVersion();
  const pathname = usePathname();
  const [moment, setMoment] = useState<UpgradeMoment>("none");
  const [preview, setPreview] = useState(false);
  const version = meta?.server_version ?? null;

  // The query as the page loaded: the welcome drops `?tour=1` once it opens,
  // which may be before the version arrives.
  const loadSearch = useRef<string | null>(null);
  useEffect(() => {
    loadSearch.current ??= window.location.search;
  }, []);

  // Once per version: StrictMode runs effects twice in development, and the
  // second run would read the version the first one stored.
  const decided = useRef<string | null>(null);
  useEffect(() => {
    if (!version || !meta || decided.current === version) return;
    decided.current = version;
    const search = loadSearch.current ?? window.location.search;
    const next = decideMoment(version, meta.highlights ?? [], search);
    setPreview(next.preview);
    setMoment(next.moment);
    // `meta` refreshes hourly with the same version; the version is the key.
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
    rememberRepo(pathname, firstRepoId);
  }, [pathname, firstRepoId]);

  return { version, moment, setMoment, preview };
}

function UpdatedNote({
  version,
  onOpen,
  onDismiss,
}: {
  version: string;
  onOpen: () => void;
  onDismiss: () => void;
}) {
  const t = useTranslations("shell");
  const tc = useTranslations("common");
  return (
    <div className="px-3 pt-2 sm:px-[var(--page-pad)]">
      <Callout
        tone="info"
        label={t("updatedLabel")}
        className="py-1.5"
        action={
          <button
            type="button"
            onClick={onDismiss}
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
          onClick={onOpen}
          className="text-[var(--color-accent-primary)] underline-offset-2 hover:underline"
        >
          {t("seeWhatChanged")}
        </button>
      </Callout>
    </div>
  );
}
