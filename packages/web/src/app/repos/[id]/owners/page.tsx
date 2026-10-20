"use client";

/**
 * Ownership — `/repos/[id]/owners` (the route keeps its original name).
 *
 * Knowledge and handover, as four tabs behind `?tab=`: the overview (truck
 * factor lede, two reads, the full table collapsed), knowledge risks,
 * knowledge loss, and the off-boarding what-if (`?leave=a,b&threshold=50`).
 */

import { useParams } from "next/navigation";
import { useTranslations } from "next-intl";
import useSWR from "swr";
import { Users } from "lucide-react";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { ViewTabs } from "@repowise-dev/ui/shared/view-tabs";
import { ContributorsPanel } from "@/components/owners/contributors-panels";
import { CONTRIBUTOR_TABS, useContributorsUrl } from "@/components/owners/use-contributors-url";
import { getOwnersSummary } from "@/lib/api/owners";

export default function ContributorsPage() {
  const t = useTranslations("views.contributors");
  const { id } = useParams<{ id: string }>();
  const { tab, scenario, setTab, setScenario } = useContributorsUrl();
  const summary = useSWR(`owners-summary:${id}`, () => getOwnersSummary(id), {
    revalidateOnFocus: false,
  });
  const quiet = summary.data?.quiet_owners_total;

  return (
    <PageShell icon={<Users />} title={t("title")} description={t("description")}>
      <ViewTabs
        aria-label={t("tabsLabel")}
        tabs={CONTRIBUTOR_TABS.map((tabId) => ({
          id: tabId,
          label: t(`tabs.${tabId}`),
          // Cheap and meaningful before the click: the summary already counts it.
          ...(tabId === "loss" && quiet !== undefined ? { badge: quiet } : {}),
        }))}
        value={tab}
        onValueChange={setTab}
      >
        <ContributorsPanel
          tab={tab}
          repoId={id}
          summary={summary}
          scenario={scenario}
          onScenarioChange={setScenario}
        />
      </ViewTabs>
    </PageShell>
  );
}
