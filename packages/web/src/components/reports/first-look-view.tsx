"use client";

/** The body of `/repos/[id]/report`: the repo report, routed into this app. */

import Link from "next/link";
import { useCallback, useState } from "react";
import useSWR from "swr";
import { toast } from "sonner";
import { useTranslations } from "next-intl";
import type { AgentPromptFlavor } from "@repowise-dev/types/agent-prompts";
import type { FirstLookLivingItem, FirstLookMark, FirstLookRouteKey } from "@repowise-dev/types/first-look";
import { FirstLookReport, type FirstLookPromptTarget } from "@repowise-dev/ui/report/first-look";
import { ApiError } from "@repowise-dev/ui/shared/api-error";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { ApiClientError } from "@/lib/api/client";
import {
  firstLookMarkdownPath,
  getFirstLook,
  getFirstLookPrompt,
  markFirstLookItem,
  rewriteFirstLook,
} from "@/lib/api/reports";
import { firstLookHref } from "./first-look-href";
import { useFirstLookCopy } from "./first-look-copy";

/** The flavor the page's prompts are worded for. */
const FLAVOR: AgentPromptFlavor = "generic";

export const firstLookKey = (repoId: string) => `first-look:${repoId}`;

export function FirstLookView({ repoId }: { repoId: string }) {
  const t = useTranslations("views.firstLook");
  const copy = useFirstLookCopy();
  const { data, error, isLoading, mutate } = useSWR(firstLookKey(repoId), () =>
    getFirstLook(repoId, { flavor: FLAVOR }),
  );
  const [rewriting, setRewriting] = useState(false);

  const prefix = `/repos/${repoId}`;
  const hrefFor = useCallback(
    (route: FirstLookRouteKey, params: Record<string, string | number>) => firstLookHref(prefix, route, params),
    [prefix],
  );
  const loadPrompt = useCallback(
    async (target: FirstLookPromptTarget, flavor: AgentPromptFlavor) =>
      (await getFirstLookPrompt(repoId, target, flavor)).text,
    [repoId],
  );
  const onMark = useCallback(
    async (item: FirstLookLivingItem, mark: FirstLookMark, reason: string | null) => {
      try {
        await markFirstLookItem(repoId, item.id, { status: mark, ...(reason ? { reason } : {}) });
      } catch (err) {
        // The server's words say what to do (a secret is ignored in code, not here).
        throw new Error(
          err instanceof ApiClientError && err.detail ? err.detail : toFriendlyMessage(err, t("markFailed")),
        );
      }
      await mutate();
    },
    [mutate, repoId, t],
  );
  const onRewrite = useCallback(async () => {
    setRewriting(true);
    try {
      await mutate(await rewriteFirstLook(repoId, { flavor: FLAVOR }), { revalidate: false });
    } catch (err) {
      toast.error(toFriendlyMessage(err, t("rewriteFailed")));
    } finally {
      setRewriting(false);
    }
  }, [mutate, repoId, t]);

  if (isLoading) {
    return (
      <div className="mx-auto max-w-[900px] space-y-3 px-4 pt-9">
        <Skeleton className="h-9 w-1/2" />
        <Skeleton className="h-4 w-1/3" />
        <Skeleton className="h-20 w-full" />
      </div>
    );
  }
  if (error instanceof ApiClientError && error.status === 404) {
    return <EmptyState title={t("empty")} description={t("emptyHint")} />;
  }
  if (error || !data) return <ApiError message={toFriendlyMessage(error)} onRetry={() => void mutate()} />;

  return (
    <FirstLookReport
      report={data.report}
      hrefFor={hrefFor}
      overviewHref={`${prefix}/overview`}
      loadPrompt={loadPrompt}
      promptFlavor={FLAVOR}
      onMark={onMark}
      markdownHref={firstLookMarkdownPath(repoId)}
      sinceCommand="repowise report repo --since-last"
      canRewrite={data.can_rewrite}
      onRewrite={() => void onRewrite()}
      rewriteCost={data.rewrite_cost_usd ?? null}
      rewriting={rewriting}
      copy={copy}
      LinkComponent={Link}
    />
  );
}
