"use client";

import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Segmented } from "@repowise-dev/ui/shared/segmented";
import { commitsLens, commitsViewHref, type CommitsLens, type CommitsView } from "@/lib/changes";

/** The page's lens: the history, or a change under review. Switching drops the other lens's filters. */
export function CommitsViewToggle({ repoId, view }: { repoId: string; view: CommitsView }) {
  const t = useTranslations("views.commits");
  const router = useRouter();
  return (
    <Segmented<CommitsLens>
      label={t("viewLabel")}
      value={commitsLens(view)}
      options={[
        { value: "history", label: t("viewHistory") },
        { value: "review", label: t("viewReview") },
      ]}
      onChange={(next) => router.push(commitsViewHref(repoId, next === "review" ? "review" : "commits"))}
    />
  );
}

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
