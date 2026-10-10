"use client";

import { useMemo, useState, type ReactNode } from "react";
import useSWR from "swr";
import { RotateCw } from "lucide-react";
import { toast } from "sonner";
import { SecurityFindingsTable } from "@repowise-dev/ui/security/findings-table";
import { SeverityDirectoryMatrix } from "@repowise-dev/ui/security/severity-directory-matrix";
import {
  SecurityPosture,
  SecurityPostureSkeleton,
  type SecurityPostureLabels,
} from "@repowise-dev/ui/security/posture";
import { AiPromptModal, buildSecurityAiPrompt, fileChatContext } from "@repowise-dev/ui/health";
import { Button } from "@repowise-dev/ui/ui/button";
import { OverviewSection } from "@repowise-dev/ui/overview/section";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
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
import type { FileCardData } from "@repowise-dev/ui/shared/file-card";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { useTranslations } from "next-intl";

/** Request limit for the findings list; at the limit the page says so. */
const LIMIT = 500;

export function SecurityTab({ repoId }: { repoId: string }) {
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
  const [rescanning, setRescanning] = useState(false);
  const [promptFinding, setPromptFinding] = useState<SecurityFinding | null>(null);

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

  const labels = useMemo<SecurityPostureLabels>(
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
      all: t("allFindings"),
      elsewhere: t("posture.elsewhere"),
      elsewhereHint: t("posture.elsewhereHint"),
      high: t("posture.high"),
      highSub: (count) => t("posture.highSub", { count }),
    }),
    [t],
  );

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
    body = scannedAgo ? (
      <EmptyState
        tone="positive"
        title={t("posture.clearTitle")}
        description={t("posture.clearBody", { when: scannedAgo })}
      />
    ) : (
      <EmptyState title={t("posture.notScannedTitle")} description={t("posture.notScannedBody")} />
    );
  } else {
    body = (
      <>
        <SecurityPosture findings={list} scannedAgo={scannedAgo} capped={capped} labels={labels} />
        <OverviewSection title={t("allFindings")} description={t("description")}>
          <SecurityFindingsTable
            findings={list}
            onSelect={handleSelect}
            onGeneratePrompt={setPromptFinding}
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

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-end gap-2">
        <Button size="sm" variant="outline" onClick={handleRescan} disabled={rescanning}>
          <RotateCw
            className={`h-3.5 w-3.5 mr-1.5 ${rescanning ? "motion-safe:animate-spin" : ""}`}
          />
          {rescanning ? "Re-scanning…" : "Re-scan"}
        </Button>
      </div>

      {body}

      <AiPromptModal
        open={promptFinding !== null}
        onOpenChange={(o) => !o && setPromptFinding(null)}
        getPrompt={
          promptFinding
            ? (flavor) => buildSecurityAiPrompt({ finding: promptFinding, flavor })
            : null
        }
        filePath={promptFinding?.file_path}
        chatContext={fileChatContext(promptFinding?.file_path)}
        title={t("promptTitle")}
        description={t("promptDescription")}
      />

      {dialog}
    </div>
  );
}
