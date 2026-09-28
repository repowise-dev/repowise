"use client";

import { useState } from "react";
import useSWR from "swr";
import { useQueryState } from "nuqs";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@repowise-dev/ui/ui/sheet";
import { CommitDetailCard } from "@repowise-dev/ui/commits/commit-detail-card";
import { AiPromptButton, AiPromptModal, buildCommitAiPrompt } from "@repowise-dev/ui/health";
import { getCommit } from "@/lib/api/git";
import { getRiskRange } from "@/lib/api/risk";
import { useTranslations } from "next-intl";

/**
 * The commit detail drawer, driven entirely by `?commit=`.
 *
 * Its own island so the queue and the scatter can both open it without a
 * shared client parent: they write the query param, this reads it. That is
 * also what makes the deep link work — entity links from Overview and the file
 * pages land here with the sheet already open, and it survives a refresh.
 */
export function CommitDetailSheet({ repoId }: { repoId: string }) {
  const t = useTranslations("commits");
  const [selectedSha, setSelectedSha] = useQueryState("commit");
  const [promptOpen, setPromptOpen] = useState(false);

  const { data: detail, isLoading } = useSWR(
    selectedSha ? `commit:${repoId}:${selectedSha}` : null,
    () => getCommit(repoId, selectedSha as string),
    { revalidateOnFocus: false },
  );

  // Only for the fix-density percentile, which needs live scoring. The file
  // list itself is stored, so a server with no checkout still renders it.
  const { data: range } = useSWR(
    selectedSha ? `commit-fixes:${repoId}:${selectedSha}` : null,
    () =>
      getRiskRange(repoId, {
        base: `${selectedSha}^`,
        head: selectedSha as string,
      }),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  );

  return (
    <>
      <Sheet
        open={selectedSha !== null}
        onOpenChange={(open) => !open && void setSelectedSha(null)}
      >
        <SheetContent side="right" className="w-[440px] max-w-[92vw] sm:w-[560px]">
          <SheetHeader>
            <SheetTitle>{t("detail.title")}</SheetTitle>
          </SheetHeader>
          <div className="flex-1 overflow-y-auto px-4 pb-6">
            {isLoading || !detail ? (
              <div className="space-y-3 pt-2">
                <Skeleton className="h-24 w-full" />
                <Skeleton className="h-40 w-full" />
              </div>
            ) : (
              <>
                <div className="flex justify-end pt-1 pb-3">
                  <AiPromptButton
                    label={t("detail.promptLabel")}
                    onClick={() => setPromptOpen(true)}
                  />
                </div>
                <CommitDetailCard
                  commit={detail}
                  fixPercentile={
                    range?.fix_history?.available
                      ? range.fix_history.percentile
                      : null
                  }
                />
              </>
            )}
          </div>
        </SheetContent>
      </Sheet>

      <AiPromptModal
        open={promptOpen}
        onOpenChange={setPromptOpen}
        getPrompt={
          detail
            ? (flavor) =>
                buildCommitAiPrompt({
                  commit: {
                    sha: detail.sha,
                    subject: detail.subject,
                    review_priority: detail.review_priority,
                    risk_percentile: detail.risk_percentile,
                    change_risk_score: detail.change_risk_score,
                    is_fix: detail.is_fix,
                    files_changed: detail.files_changed,
                    lines_added: detail.lines_added,
                    lines_deleted: detail.lines_deleted,
                    entropy: detail.entropy,
                    top_drivers: detail.drivers
                      .filter((d) => d.contribution > 0)
                      .map((d) => d.label),
                    author_name: detail.author_name,
                  },
                  flavor,
                })
            : null
        }
        filePath={detail ? detail.short_sha : null}
        title={t("detail.promptTitle")}
        description={t("detail.promptDescription")}
      />
    </>
  );
}
