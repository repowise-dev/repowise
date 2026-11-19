"use client";

import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Segmented } from "@repowise-dev/ui/shared/segmented";
import { commitsViewHref } from "@/lib/changes";

/** How the history list is grouped: one row per commit, or one per merged change. */
export function HistoryGroupToggle({ repoId, view }: { repoId: string; view: "commits" | "changes" }) {
  const t = useTranslations("views.commits");
  const router = useRouter();
  return (
    <Segmented<"commits" | "changes">
      label={t("groupLabel")}
      value={view}
      options={[
        { value: "commits", label: t("viewByCommit") },
        { value: "changes", label: t("viewByChange") },
      ]}
      onChange={(next) => router.push(commitsViewHref(repoId, next))}
    />
  );
}
