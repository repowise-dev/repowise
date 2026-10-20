"use client";

import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Segmented } from "@repowise-dev/ui/shared/segmented";
import { commitsViewHref, type CommitsView } from "@/lib/changes";

/** The lens over the same history. Switching drops the other view's filters. */
export function CommitsViewToggle({ repoId, view }: { repoId: string; view: CommitsView }) {
  const t = useTranslations("views.commits");
  const router = useRouter();
  return (
    <Segmented<CommitsView>
      label={t("viewLabel")}
      value={view}
      options={[
        { value: "changes", label: t("viewByChange") },
        { value: "commits", label: t("viewByCommit") },
        { value: "events", label: t("viewEvents") },
        { value: "review", label: t("viewReview") },
      ]}
      onChange={(next) => router.push(commitsViewHref(repoId, next))}
    />
  );
}
