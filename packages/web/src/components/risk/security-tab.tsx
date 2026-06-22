"use client";

import { useState } from "react";
import useSWR from "swr";
import { RotateCw } from "lucide-react";
import { toast } from "sonner";
import { SecurityFindingsTable } from "@repowise-dev/ui/security/findings-table";
import { SeverityDirectoryMatrix } from "@repowise-dev/ui/security/severity-directory-matrix";
import { AiPromptModal, buildSecurityAiPrompt } from "@repowise-dev/ui/health";
import { Button } from "@repowise-dev/ui/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise-dev/ui/ui/card";
import { CollapsibleSection } from "@repowise-dev/ui/shared/collapsible-section";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { listSecurityFindings, type SecurityFinding } from "@/lib/api/security";
import { syncRepo } from "@/lib/api/repos";
import { useFileCardHost } from "@/components/shared/file-card-host";
import type { FileCardData } from "@repowise-dev/ui/shared/file-card";

export function SecurityTab({ repoId }: { repoId: string }) {
  const { data: findings, isLoading, error } = useSWR<SecurityFinding[]>(
    `security:${repoId}`,
    () => listSecurityFindings(repoId, { limit: 500 }),
    { revalidateOnFocus: false },
  );
  const [rescanning, setRescanning] = useState(false);
  const [promptFinding, setPromptFinding] = useState<SecurityFinding | null>(null);

  const handleRescan = async () => {
    setRescanning(true);
    try {
      await syncRepo(repoId);
      toast.success("Sync started — findings refresh when it completes");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't start a sync");
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

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm text-[var(--color-text-secondary)] mr-auto">
          Secrets, dangerous patterns, and policy hits detected while indexing.
          Findings refresh on every sync.
        </p>
        <Button size="sm" variant="outline" onClick={handleRescan} disabled={rescanning}>
          <RotateCw className={`h-3.5 w-3.5 mr-1.5 ${rescanning ? "animate-spin" : ""}`} />
          Re-scan
        </Button>
      </div>

      {isLoading ? (
        <Skeleton className="h-40 w-full rounded-lg" />
      ) : error ? (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Couldn&apos;t load findings</CardTitle>
          </CardHeader>
          <CardContent className="pt-0 text-xs text-[var(--color-text-secondary)]">
            The security endpoint returned an error. Try re-running ingestion.
          </CardContent>
        </Card>
      ) : (
        <>
          {/* Single visual rollup — per-severity counts live in the table's
              filter pills below, not in a separate distribution card. */}
          <SeverityDirectoryMatrix findings={findings ?? []} />
          <CollapsibleSection
            title="All findings"
            hint={`${(findings ?? []).length} findings`}
            defaultOpen={false}
          >
            <SecurityFindingsTable
              findings={findings ?? []}
              onSelect={handleSelect}
              onGeneratePrompt={setPromptFinding}
            />
          </CollapsibleSection>
        </>
      )}

      <AiPromptModal
        open={promptFinding !== null}
        onOpenChange={(o) => !o && setPromptFinding(null)}
        getPrompt={
          promptFinding
            ? (flavor) => buildSecurityAiPrompt({ finding: promptFinding, flavor })
            : null
        }
        filePath={promptFinding?.file_path}
        title="AI fix prompt"
        description="A ready-to-paste prompt that walks your AI agent through confirming and remediating this security finding."
      />

      {dialog}
    </div>
  );
}
