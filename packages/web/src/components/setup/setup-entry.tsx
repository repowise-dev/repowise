"use client";

import { Check } from "lucide-react";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils/cn";
import type { RepoResponse } from "@/lib/api/types";
import { setupHref } from "@/components/settings/settings-groups";
import { deriveSetup, ESSENTIALS_TOTAL } from "./setup-rows";
import { useSetupData } from "./use-setup";

/** The workspace Setup page, where the entry goes with no indexed repo active. */
export const WORKSPACE_SETUP_HREF = "/settings/setup";

/**
 * The repo whose progress the Setup entry shows: the active one, once it has
 * an index. A repo still waiting for its first index has its own call to action.
 */
export function setupEntryRepoId(
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

/**
 * Where setup stands for one repo. The ring counts the three essentials, as
 * the Setup page does. Once they are done, `more` is the finish-setup pass's
 * step count while the server offers it: the same steps, and the same offer,
 * behind `repowise next --prompt setup` and the SessionStart line. Those are
 * optional improvements, so they never fill the ring.
 */
export type SetupProgress =
  | { state: "essentials"; done: number; total: number }
  | { state: "more"; count: number }
  | { state: "done" };

/** Null with no repo, and until the repo's setup is read. */
export function useSetupProgress(repoId: string | undefined): SetupProgress | null {
  // The server caches this payload (a minute fresh, five stale), so a page
  // load asking for it does not rerun doctor.
  const { data } = useSetupData(repoId ?? null);
  if (!data) return null;
  // Only the count is needed here, so the copy-shaping options stay inert.
  const view = deriveSetup(data, { repoName: "", now: Date.now(), relative: () => "" });
  if (!view.allDone) return { state: "essentials", done: view.doneCount, total: ESSENTIALS_TOTAL };
  const steps = data.setup;
  if (steps?.offer === "finish_setup" && steps.open > 0) return { state: "more", count: steps.open };
  return { state: "done" };
}

/** The Setup entry for the sidebar and mobile nav: where it links, and its progress. */
export function useSetupEntry(repos: RepoResponse[], activeRepoId: string | undefined) {
  const repoId = setupEntryRepoId(repos, activeRepoId);
  const progress = useSetupProgress(repoId);
  return { href: repoId ? setupHref(repoId) : WORKSPACE_SETUP_HREF, progress };
}

/** What a screen reader hears after "Setup", and the tooltip; empty while unknown. */
export function useSetupProgressText(progress: SetupProgress | null): string {
  const t = useTranslations("setup.pill");
  if (!progress) return "";
  if (progress.state === "essentials") return t("essentials", { done: progress.done, total: progress.total });
  if (progress.state === "more") return t("moreLong", { count: progress.count });
  return t("done");
}

/** The ring: an ink arc over a hairline track, `done / total` of the way round. */
export function SetupRing({
  done,
  total,
  className = "h-3.5 w-3.5",
}: {
  done: number;
  total: number;
  className?: string;
}) {
  const r = 5.5;
  const c = 2 * Math.PI * r;
  const share = total > 0 ? Math.min(done / total, 1) : 0;
  return (
    <svg viewBox="0 0 14 14" aria-hidden className={cn("shrink-0 -rotate-90", className)}>
      <circle cx="7" cy="7" r={r} fill="none" strokeWidth="2" className="stroke-[var(--color-border-hover)]" />
      {share > 0 && (
        <circle
          cx="7"
          cy="7"
          r={r}
          fill="none"
          strokeWidth="2"
          strokeLinecap="round"
          strokeDasharray={`${share * c} ${c}`}
          className="stroke-[var(--color-text-secondary)]"
        />
      )}
    </svg>
  );
}

/**
 * What trails the expanded Setup row: "2/3" and the ring while an essential
 * is open, "5 more" once only the optional pass is left, a quiet check after.
 * Progress, not an alert: no colour, no motion.
 */
export function SetupProgressMark({ progress }: { progress: SetupProgress | null }) {
  const t = useTranslations("setup.pill");
  const text = useSetupProgressText(progress);
  if (!progress) return null;
  return (
    <span className="ml-auto inline-flex shrink-0 items-center gap-1.5" title={text}>
      {progress.state === "essentials" && (
        <>
          <span className="font-mono text-[10px] tabular-nums text-[var(--color-text-tertiary)]" aria-hidden>
            {progress.done}/{progress.total}
          </span>
          <SetupRing done={progress.done} total={progress.total} />
        </>
      )}
      {progress.state === "more" && (
        <span className="font-mono text-[10px] tabular-nums text-[var(--color-text-tertiary)]" aria-hidden>
          {t("more", { count: progress.count })}
        </span>
      )}
      {progress.state === "done" && (
        <Check className="h-3.5 w-3.5 text-[var(--color-text-tertiary)]" aria-hidden />
      )}
      <span className="sr-only">, {text}</span>
    </span>
  );
}

/** Collapsed: a small ring on the icon while an essential is open; nothing otherwise. */
export function SetupRingBadge({ progress }: { progress: SetupProgress | null }) {
  if (progress?.state !== "essentials") return null;
  return (
    <SetupRing
      done={progress.done}
      total={progress.total}
      className="absolute bottom-1 right-1 h-2.5 w-2.5 rounded-full bg-[var(--color-bg-surface)]"
    />
  );
}
