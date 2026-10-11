"use client";

import useSWR from "swr";
import type { SecurityFinding } from "@repowise-dev/types";
import { SecurityPanel as SecurityPanelShell } from "@repowise-dev/ui/wiki/security-panel";
import { listSecurityFindings } from "@/lib/api/security";

interface Props {
  repoId: string;
  filePath: string;
}

export function SecurityPanelWrapper({ repoId, filePath }: Props) {
  const { data: findings, isLoading } = useSWR<SecurityFinding[]>(
    ["security", repoId, filePath],
    () => listSecurityFindings(repoId, { file_path: filePath, limit: 20 }),
    { revalidateOnFocus: false },
  );

  return <SecurityPanelShell findings={findings} isLoading={isLoading} />;
}
