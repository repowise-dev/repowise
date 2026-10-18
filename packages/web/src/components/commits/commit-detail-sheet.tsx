"use client";

import { useState } from "react";
import Link from "next/link";
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
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import {
  AiPromptButton,
  AiPromptModal,
  buildChangeAiPrompt,
  buildCommitAiPrompt,
} from "@repowise-dev/ui/health";
import { getCommit } from "@/lib/api/git";
import { getRiskRange } from "@/lib/api/risk";
import { commitFixPromptInput, commitReviewPromptInput } from "@/lib/commit-prompt";
import { useTranslations } from "next-intl";
import { useCommitLedeCopy, useHealthDeltaLabels } from "./history-labels";

/**
 * The commit detail drawer, driven entirely by `?commit=`.
 *
 * Its own island so the list and the scatter can both open it without a
 * shared client parent: they write the query param, this reads it. That is
 * also what makes the deep link work: entity links from Overview and the file
 * pages land here with the sheet already open, and it survives a refresh.
 *
 * Its one filled action is the agent prompt: fix what the commit made worse
 * when it made something worse, otherwise review it.
 */
export function CommitDetailSheet({ repoId }: { repoId: string }) {
  const t = useTranslations("commits");
  const healthLabels = useHealthDeltaLabels();
  const ledeCopy = useCommitLedeCopy();
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

  const fix = detail ? commitFixPromptInput(detail) : null;
  const copy = fix ? "fix" : "review";

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
              <CommitDetailCard
                className="pt-1"
                commit={detail}
                healthLabels={healthLabels}
                ledeCopy={ledeCopy}
                fileHref={(path) => `${fileEntityPath(`/repos/${repoId}`, path)}?tab=health`}
                openFileLabel={t("detail.openFile")}
                LinkComponent={Link}
                action={
                  <AiPromptButton
                    label={t(`detail.${copy}PromptLabel`)}
                    onClick={() => setPromptOpen(true)}
                  />
                }
                fixPercentile={
                  range?.fix_history?.available ? range.fix_history.percentile : null
                }
              />
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
                fix
                  ? buildChangeAiPrompt({ change: fix, flavor })
                  : buildCommitAiPrompt({ commit: commitReviewPromptInput(detail), flavor })
            : null
        }
        filePath={detail ? detail.short_sha : null}
        chatContext={
          detail
            ? { kind: "commit", label: detail.short_sha, target: detail.sha, targetKind: "commit" }
            : undefined
        }
        title={t(`detail.${copy}PromptTitle`)}
        description={t(`detail.${copy}PromptDescription`)}
      />
    </>
  );
}
