"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { X } from "lucide-react";
import { cn } from "@/lib/utils/cn";
import type { RepoResponse } from "@/lib/api/types";
import { deriveSetup, ESSENTIALS_TOTAL } from "./setup-rows";
import {
  rememberSetupDone,
  setupRememberedDone,
  useHideOnThisMachine,
  useOnboardingState,
  useSetupData,
} from "./use-setup";

export function setupHref(repoId: string): string {
  return `/repos/${encodeURIComponent(repoId)}/settings/setup`;
}

/**
 * The repo whose Setup pill the footer shows: the active one, once it has an
 * index. A repo still waiting for its first index has its own call to action.
 */
export function setupPillRepoId(
  repos: RepoResponse[],
  activeRepoId: string | undefined,
): string | undefined {
  const repo = repos.find((r) => r.id === activeRepoId);
  if (!repo || repo.id.startsWith("ws:")) return undefined;
  if (repo.workspace_status === "needs_index" || repo.workspace_status === "missing_dir") {
    return undefined;
  }
  return repo.id;
}

/** A small progress ring: green for the done share, the border tone for the rest. */
export function SetupRing({ done, className }: { done: number; className?: string }) {
  const share = Math.round((done / ESSENTIALS_TOTAL) * 100);
  return (
    <span
      aria-hidden
      className={cn("relative inline-block h-3.5 w-3.5 shrink-0 rounded-full", className)}
      style={{
        background: `conic-gradient(var(--color-success) 0 ${share}%, var(--color-border-hover) ${share}% 100%)`,
      }}
    >
      <span className="absolute inset-[3px] rounded-full bg-[var(--color-bg-surface)]" />
    </span>
  );
}

/** How many essentials are done, or null while unknown, hidden or finished. */
function usePillProgress(repoId: string): number | null {
  // Read after mount: storage is not there during SSR.
  const [rememberedDone, setRememberedDone] = useState(true);
  useEffect(() => setRememberedDone(setupRememberedDone(repoId)), [repoId]);
  const onboarding = useOnboardingState(!rememberedDone);
  const hidden = onboarding.data?.setup_pill_hidden ?? true;
  // The full payload runs doctor, so it is asked for only when the pill can show.
  const { data } = useSetupData(!rememberedDone && !hidden ? repoId : null);
  // Only the count is needed here, so the copy-shaping options stay inert.
  const view = data ? deriveSetup(data, { repoName: "", now: Date.now(), relative: () => "" }) : null;
  const allDone = view?.allDone ?? false;
  useEffect(() => {
    if (allDone) rememberSetupDone(repoId, true);
  }, [repoId, allDone]);
  if (!view || allDone || rememberedDone || hidden) return null;
  return view.doneCount;
}

/**
 * "Setup N of 3" with a green ring, in the repo sidebar footer and the mobile
 * nav. Links to the Setup page, hides with ×, and goes away once the three
 * essentials are done.
 */
export function SetupPill({ repoId, compact = false }: { repoId: string; compact?: boolean }) {
  const t = useTranslations("setup.pill");
  const done = usePillProgress(repoId);
  const { hide, hiddenNow } = useHideOnThisMachine();
  if (done === null || hiddenNow.has("setup_pill_hidden")) return null;

  const label = t("aria", { done, total: ESSENTIALS_TOTAL });
  if (compact) {
    return (
      <Link
        href={setupHref(repoId)}
        aria-label={label}
        title={label}
        className="flex items-center justify-center rounded-md p-2 transition-colors hover:bg-[var(--color-bg-elevated)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
      >
        <SetupRing done={done} />
      </Link>
    );
  }
  return (
    <div className="flex items-center gap-1 rounded-full border border-[var(--color-border-hover)] pr-1 text-xs text-[var(--color-text-primary)]">
      <Link
        href={setupHref(repoId)}
        aria-label={label}
        className="flex min-w-0 flex-1 items-center gap-2 rounded-full py-1.5 pl-2.5 transition-colors hover:bg-[var(--color-bg-elevated)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
      >
        <SetupRing done={done} />
        <span className="truncate">{t("label")}</span>
        <span className="ml-auto pr-1 tabular-nums text-[var(--color-text-tertiary)]">
          {t("progress", { done, total: ESSENTIALS_TOTAL })}
        </span>
      </Link>
      <button
        type="button"
        onClick={() => void hide("setup_pill_hidden")}
        aria-label={t("hide")}
        title={t("hide")}
        className="shrink-0 rounded-full p-1 text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

/** The pill for the active repo in the sidebar or mobile nav footer; nothing elsewhere. */
export function FooterSetupPill({
  repos,
  activeRepoId,
  compact = false,
}: {
  repos: RepoResponse[];
  activeRepoId: string | undefined;
  compact?: boolean;
}) {
  const repoId = setupPillRepoId(repos, activeRepoId);
  return repoId ? <SetupPill repoId={repoId} compact={compact} /> : null;
}
