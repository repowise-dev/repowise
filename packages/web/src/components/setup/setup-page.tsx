"use client";

import { useEffect, useId, useState } from "react";
import Link from "next/link";
import { useFormatter, useTranslations } from "next-intl";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { Callout } from "@repowise-dev/ui/shared/callout";
import { Button } from "@repowise-dev/ui/ui/button";
import { Skeleton, SkeletonRegion } from "@repowise-dev/ui/ui/skeleton";
import { tourHref } from "@/lib/tour";
import type { SetupResponse } from "@/lib/api/setup";
import { HostedSetupCard } from "./hosted-setup-card";
import { SetupRowView, useSetupText } from "./setup-row";
import { firstCallerName, type SetupRow, type SetupView } from "./setup-rows";
import { useSetupControls } from "./setup-controls";
import { rememberSetupDone, useSetupData, useSetupView } from "./use-setup";

const MICRO = "font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--color-text-tertiary)]";

/** The Setup page: what is set up for one repo, with one action per row. */
export function SetupPage({ repoId, repoName }: { repoId: string; repoName: string }) {
  const t = useTranslations("setup");
  const { data, error, isLoading, mutate } = useSetupData(repoId);
  const view = useSetupView(repoId, repoName, data);

  useEffect(() => {
    if (view) rememberSetupDone(repoId, view.allDone);
  }, [repoId, view]);

  if (!data || !view) {
    return (
      <PageShell title={t("title")} description={t("loading")} maxWidth="narrow">
        {error && !isLoading ? (
          <Callout tone="error" label={t("loadFailedLabel")} action={<Button size="sm" variant="outline" onClick={() => void mutate()}>{t("retry")}</Button>}>
            {t("loadFailed")}
          </Callout>
        ) : (
          <SetupSkeleton label={t("loading")} />
        )}
      </PageShell>
    );
  }
  return <SetupLoaded repoId={repoId} setup={data} view={view} />;
}

function SetupSkeleton({ label }: { label: string }) {
  return (
    <SkeletonRegion label={label} className="flex flex-col gap-3">
      {Array.from({ length: 6 }, (_, i) => (
        <div key={i} className="grid grid-cols-1 gap-3 border-t border-[var(--color-border-default)] pt-3 sm:grid-cols-[124px_minmax(0,1fr)_auto]">
          <Skeleton className="h-4 w-20" />
          <div className="space-y-1.5">
            <Skeleton className="h-4 w-48 max-w-full" />
            <Skeleton className="h-3 w-full" />
          </div>
          <Skeleton className="h-7 w-28" />
        </div>
      ))}
    </SkeletonRegion>
  );
}

function SetupLoaded({ repoId, setup, view }: { repoId: string; setup: SetupResponse; view: SetupView }) {
  const t = useTranslations("setup");
  const format = useFormatter();
  const controls = useSetupControls(repoId);
  const text = useSetupText();

  const description = view.allDone
    ? t("doneLede", {
        agent: firstCallerName(setup) ?? t("yourAgent"),
        when: format.relativeTime(new Date(setup.calls.first_call_at ?? Date.now())),
      })
    : t("progress", { done: view.doneCount });

  const rows = (list: SetupRow[]) =>
    list.map((row) => (
      <SetupRowView
        key={row.id}
        row={row}
        action={controls.actionFor(row)}
        onOpenPrompt={() => controls.openRowPrompt(row)}
      >
        {row.files?.length ? <SkippedFiles files={row.files} /> : null}
      </SetupRowView>
    ));

  return (
    <PageShell title={view.allDone ? t("doneTitle") : t("title")} description={description} maxWidth="narrow">
      {!setup.writes_allowed && (
        <Callout tone="info" label={t("writesOffLabel")}>
          {t("writesOff")}
        </Callout>
      )}
      <div>
        {(view.attention.length > 0 || view.checksUnreadable) && (
          <RowGroup label={t("attention")}>
            {view.checksUnreadable && (
              <p className="border-t border-[var(--color-border-default)] py-3 text-xs text-[var(--color-text-tertiary)]">
                {text({ key: "unreadable.detail", values: { command: "repowise doctor" } })}
              </p>
            )}
            {rows(view.attention)}
          </RowGroup>
        )}
        <RowGroup
          label={t("essentials")}
          aside={
            <Link
              href={tourHref(repoId)}
              className="text-xs font-medium text-[var(--color-text-primary)] underline decoration-[var(--color-border-hover)] underline-offset-[3px] hover:decoration-[var(--color-text-primary)]"
            >
              {t("tour")}
            </Link>
          }
        >
          {rows(view.essentials)}
        </RowGroup>
        {view.allDone && <HostedSetupCard onboarding={setup.onboarding} />}
        <RowGroup label={t("optional")}>{rows(view.optional)}</RowGroup>
      </div>
      {controls.dialogs}
    </PageShell>
  );
}

function RowGroup({
  label,
  aside,
  children,
}: {
  label: string;
  aside?: React.ReactNode;
  children: React.ReactNode;
}) {
  const headingId = useId();
  return (
    <section className="mt-6 first:mt-0" aria-labelledby={headingId}>
      <div className="mb-1 flex items-baseline justify-between gap-3">
        <h2 id={headingId} className={MICRO}>
          {label}
        </h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

function SkippedFiles({ files }: { files: string[] }) {
  const t = useTranslations("setup");
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-1">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="text-xs font-medium text-[var(--color-text-primary)] underline decoration-[var(--color-border-hover)] underline-offset-[3px]"
      >
        {open ? t("hideFiles") : t("showFiles")}
      </button>
      {open && (
        <ul className="mt-1.5 space-y-0.5 font-mono text-[11px] text-[var(--color-text-secondary)]">
          {files.map((f) => (
            <li key={f} className="[overflow-wrap:anywhere]">
              {f}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
