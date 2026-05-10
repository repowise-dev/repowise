"use client";

import { useMemo } from "react";
import useSWR from "swr";
import { useParams } from "next/navigation";
import { ShieldAlert } from "lucide-react";
import { SeverityDistribution } from "@repowise-dev/ui/security/severity-distribution";
import { SecurityFindingsTable } from "@repowise-dev/ui/security/findings-table";
import { FindingsByDirectory } from "@repowise-dev/ui/security/findings-by-directory";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise-dev/ui/ui/card";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { listSecurityFindings, type SecurityFinding } from "@/lib/api/security";
import { useFileCardHost } from "@/components/shared/file-card-host";
import type { FileCardData } from "@repowise-dev/ui/shared/file-card";

export default function SecurityPage() {
  const params = useParams<{ id: string }>();
  const repoId = params.id;

  const { data: findings, isLoading, error } = useSWR<SecurityFinding[]>(
    `security:${repoId}`,
    () => listSecurityFindings(repoId, { limit: 500 }),
    { revalidateOnFocus: false },
  );

  const { showFile, dialog } = useFileCardHost(repoId);

  const counts = useMemo(() => {
    const c: Record<string, number> = { high: 0, med: 0, low: 0 };
    for (const f of findings ?? []) {
      c[f.severity] = (c[f.severity] ?? 0) + 1;
    }
    return c;
  }, [findings]);

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
    <div className="p-4 sm:p-6 space-y-6 max-w-[1600px]">
      <div>
        <h1 className="text-xl font-semibold text-[var(--color-text-primary)] mb-1 flex items-center gap-2">
          <ShieldAlert className="h-5 w-5 text-red-400" />
          Security
        </h1>
        <p className="text-sm text-[var(--color-text-secondary)]">
          Findings detected during ingestion — secrets, dangerous patterns, and policy hits.
        </p>
      </div>

      {isLoading ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Skeleton className="h-40 w-full rounded-lg" />
          <Skeleton className="h-40 w-full rounded-lg" />
        </div>
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
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <SeverityDistribution counts={counts} />
            <FindingsByDirectory findings={findings ?? []} />
          </div>
          <SecurityFindingsTable findings={findings ?? []} onSelect={handleSelect} />
        </>
      )}

      {dialog}
    </div>
  );
}
