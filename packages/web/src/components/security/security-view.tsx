"use client";

/**
 * Security — the body of `/repos/[id]/security`. Leads with the posture
 * (counts, where they sit, when the scan ran from `/security/summary`), then
 * every finding with the agent prompt as each row's primary action, then the
 * directory matrix. The page's Re-scan action is exported beside it.
 */

import { useMemo, useState, type ReactNode } from "react";
import useSWR from "swr";
import { RotateCw } from "lucide-react";
import { toast } from "sonner";
import { SecurityFindingsTable } from "@repowise-dev/ui/security/findings-table";
import { SeverityDirectoryMatrix } from "@repowise-dev/ui/security/severity-directory-matrix";
import {
  SecurityPosture,
  SecurityPostureSkeleton,
  postureCountsFromSummary,
  type SecurityPostureCounts,
  type SecurityPostureLabels,
} from "@repowise-dev/ui/security/posture";
import {
  buildSecurityBatchAiPrompt,
  fileChatContext,
  useAiPromptAction,
} from "@repowise-dev/ui/health";
import { Button } from "@repowise-dev/ui/ui/button";
import { OverviewSection } from "@repowise-dev/ui/overview/section";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { CleanState } from "@repowise-dev/ui/shared/clean-state";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import { ApiError } from "@repowise-dev/ui/shared/api-error";
import { formatRelativeTime } from "@repowise-dev/ui/lib/format";
import {
  getSecuritySummary,
  listSecurityFindings,
  type SecurityFinding,
  type SecuritySummary,
} from "@/lib/api/security";
import { syncRepo } from "@/lib/api/repos";
import { useFileCardHost } from "@/components/shared/file-card-host";
import { useRepoFileCount } from "@/lib/hooks/use-repo";
import type { FileCardData } from "@repowise-dev/ui/shared/file-card";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { useTranslations } from "next-intl";

/** Request limit for the findings list; at the limit the page says so. */
const LIMIT = 500;

/** Starts a sync, which re-runs the pattern scan. The page header's action. */
export function SecurityRescanButton({ repoId }: { repoId: string }) {
  const t = useTranslations("risk");
  const [rescanning, setRescanning] = useState(false);

  const handleRescan = async () => {
    setRescanning(true);
    try {
      await syncRepo(repoId);
      toast.success(t("syncStarted"));
    } catch (err) {
      toast.error(toFriendlyMessage(err, t("syncFailed")));
    } finally {
      setRescanning(false);
    }
  };

  return (
    <Button size="sm" variant="outline" onClick={handleRescan} disabled={rescanning}>
      <RotateCw
        className={`h-3.5 w-3.5 mr-1.5 ${rescanning ? "motion-safe:animate-spin" : ""}`}
      />
      {rescanning ? t("rescanning") : t("rescan")}
    </Button>
  );
}

export function SecurityView({ repoId }: { repoId: string }) {
  const t = useTranslations("risk");
  const {
    data: findings,
    isLoading,
    error,
    mutate,
  } = useSWR<SecurityFinding[]>(
    `security:${repoId}`,
    () => listSecurityFindings(repoId, { limit: LIMIT }),
    { revalidateOnFocus: false },
  );
  // An older server has no summary route; that reads as "scan time unknown",
  // never as an all-clear, so a failure here must not fail the page.
  const { data: summary, isLoading: summaryLoading } = useSWR<SecuritySummary | null>(
    `security-summary:${repoId}`,
    () => getSecuritySummary(repoId).catch(() => null),
    { revalidateOnFocus: false },
  );
  // One finding from a row, or every finding the table's filters show,
  // through the shared prompt modal.
  const { openPrompt, promptModal } = useAiPromptAction();
  const promptFor = (findings: SecurityFinding[]) => {
    const one = findings.length === 1 ? findings[0]!.file_path : undefined;
    openPrompt({
      title: t("promptTitle"),
      description: t("promptDescription"),
      filePath: one ?? null,
      ...(one ? { chatContext: fileChatContext(one)! } : {}),
      getPrompt: (flavor) => buildSecurityBatchAiPrompt({ findings, flavor }),
    });
  };
  const fileCount = useRepoFileCount(repoId);

  const { showFile, dialog } = useFileCardHost(repoId);

  const handleSelect = (f: SecurityFinding) => {
    const data: FileCardData = {
      file_path: f.file_path,
      summary: `Security: ${f.kind} (${f.severity})`,
      security: {
        findings_count: (findings ?? []).filter((x) => x.file_path === f.file_path).length,
        critical_count: (findings ?? []).filter(
          (x) => x.file_path === f.file_path && x.severity === "high",
        ).length,
      },
    };
    showFile(data);
  };

  const labels = usePostureLabels();

  const scannedAt = summary?.scanned_at ?? null;
  const scannedAgo = scannedAt ? formatRelativeTime(scannedAt) : null;
  const list = findings ?? [];
  const capped = list.length >= LIMIT;

  let body: ReactNode;
  if (isLoading || summaryLoading) {
    body = <SecurityPostureSkeleton label={t("posture.loading")} />;
  } else if (error) {
    body = (
      <ApiError
        title={t("loadFailed")}
        message={t("loadFailedBody")}
        retryLabel={t("posture.retry")}
        onRetry={() => void mutate()}
      />
    );
  } else if (list.length === 0) {
    // Green only when the scan is known to have run; an unknown scan time
    // with nothing listed is "not scanned yet", not good news.
    body = <SecurityEmpty scannedAgo={scannedAgo} fileCount={fileCount} />;
  } else {
    body = (
      <SecurityFindingsBody
        list={list}
        counts={postureCountsFromSummary(summary)}
        scannedAgo={scannedAgo}
        capped={capped}
        labels={labels}
        onSelect={handleSelect}
        onGeneratePrompt={(f) => promptFor([f])}
        onGenerateBatchPrompt={promptFor}
      />
    );
  }

  return (
    <div className="space-y-6">
      {body}

      {promptModal}

      {dialog}
    </div>
  );
}

function usePostureLabels(): SecurityPostureLabels {
  const t = useTranslations("risk");
  return useMemo<SecurityPostureLabels>(
    () => ({
      figure: t("posture.figure"),
      figureHint: t("posture.figureHint"),
      unit: (total, capped) =>
        capped ? t("posture.unitCapped", { total }) : t("posture.unit", { count: total }),
      sentence: (c) =>
        c.source === 0
          ? t("posture.sentenceNoSource", { count: c.elsewhere })
          : [
              t("posture.sentenceSource", { count: c.source, high: c.sourceHigh }),
              c.elsewhere ? t("posture.sentenceElsewhere", { count: c.elsewhere }) : "",
            ]
              .filter(Boolean)
              .join(" "),
      scanned: (when) => t("posture.scanned", { when }),
      scanTimeUnknown: t("posture.scanTimeUnknown"),
    }),
    [t],
  );
}

function SecurityEmpty({
  scannedAgo,
  fileCount,
}: {
  scannedAgo: string | null;
  fileCount: number | null;
}) {
  const t = useTranslations("risk");
  return scannedAgo ? (
    <CleanState
      title={
        fileCount != null
          ? t("posture.clearTitleScoped", { files: formatNumber(fileCount) })
          : t("posture.clearTitle")
      }
      description={t("posture.clearBody", { when: scannedAgo })}
    />
  ) : (
    <EmptyState title={t("posture.notScannedTitle")} description={t("posture.notScannedBody")} />
  );
}

function SecurityFindingsBody({
  list,
  counts,
  scannedAgo,
  capped,
  labels,
  onSelect,
  onGeneratePrompt,
  onGenerateBatchPrompt,
}: {
  list: SecurityFinding[];
  /** The server's split over every finding; null on an older server. */
  counts: SecurityPostureCounts | null;
  scannedAgo: string | null;
  capped: boolean;
  labels: SecurityPostureLabels;
  onSelect: (f: SecurityFinding) => void;
  onGeneratePrompt: (f: SecurityFinding) => void;
  onGenerateBatchPrompt: (findings: SecurityFinding[]) => void;
}) {
  const t = useTranslations("risk");
  return (
    <>
      <SecurityPosture
        counts={counts}
        findings={list}
        scannedAgo={scannedAgo}
        capped={capped}
        labels={labels}
      />
      <OverviewSection title={t("allFindings")} description={t("description")}>
        <SecurityFindingsTable
          findings={list}
          onSelect={onSelect}
          onGeneratePrompt={onGeneratePrompt}
          promptLabel={t("promptAction")}
          onGenerateBatchPrompt={onGenerateBatchPrompt}
          batchPromptLabel={(count) => t("batchPromptAction", { count })}
        />
        {capped && (
          <p className="text-xs text-[var(--color-text-tertiary)]">
            {t("posture.capped", { count: LIMIT })}
          </p>
        )}
      </OverviewSection>
      <OverviewSection title={t("posture.byDirectory")} description={t("posture.byDirectoryBody")}>
        <SeverityDirectoryMatrix findings={list} />
      </OverviewSection>
    </>
  );
}
