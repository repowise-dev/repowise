"use client";

/** The body of `/repos/[id]/first-look`: the stored report, routed into this app. */

import Link from "next/link";
import { useCallback, useState } from "react";
import useSWR from "swr";
import { toast } from "sonner";
import { useTranslations } from "next-intl";
import type { AgentPromptFlavor } from "@repowise-dev/types/agent-prompts";
import type { FirstLookRouteKey } from "@repowise-dev/types/first-look";
import { FirstLookReport } from "@repowise-dev/ui/report/first-look";
import { ApiError } from "@repowise-dev/ui/shared/api-error";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { ApiClientError } from "@/lib/api/client";
import { getFirstLook, getFirstLookPrompt, rewriteFirstLook } from "@/lib/api/reports";
import { firstLookHref } from "./first-look-href";

/** The flavor the page asks the report prompt in; the modal fetches any other. */
const FLAVOR: AgentPromptFlavor = "generic";

export function FirstLookView({ repoId }: { repoId: string }) {
  const t = useTranslations("views.firstLook");
  const key = `first-look:${repoId}`;
  const { data, error, isLoading, mutate } = useSWR(key, () => getFirstLook(repoId, { flavor: FLAVOR }));
  const [rewriting, setRewriting] = useState(false);

  const prefix = `/repos/${repoId}`;
  const hrefFor = useCallback(
    (route: FirstLookRouteKey, params: Record<string, string | number>) => firstLookHref(prefix, route, params),
    [prefix],
  );
  const loadPrompt = useCallback(
    async (itemId: string | null, flavor: AgentPromptFlavor) =>
      (await getFirstLookPrompt(repoId, itemId, flavor)).text,
    [repoId],
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
      <div className="mx-auto max-w-[880px] space-y-3">
        <Skeleton className="h-8 w-1/2" />
        <Skeleton className="h-4 w-1/3" />
        <Skeleton className="h-24 w-full" />
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
      staleness={data.staleness}
      hrefFor={hrefFor}
      promptFlavor={data.flavor}
      reportPrompt={data.report_prompt}
      loadPrompt={loadPrompt}
      canRewrite={data.can_rewrite}
      onRewrite={() => void onRewrite()}
      rewriteCost={data.rewrite_cost_usd ?? null}
      rewriting={rewriting}
      LinkComponent={Link}
    />
  );
}
