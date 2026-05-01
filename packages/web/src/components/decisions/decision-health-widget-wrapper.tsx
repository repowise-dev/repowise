"use client";

import useSWR from "swr";
import { DecisionHealthWidget } from "@repowise-dev/ui/decisions/decision-health-widget";
import { getDecisionHealth } from "@/lib/api/decisions";

interface DecisionHealthWidgetWrapperProps {
  repoId: string;
}

export function DecisionHealthWidgetWrapper({
  repoId,
}: DecisionHealthWidgetWrapperProps) {
  const { data: health } = useSWR(
    `/api/repos/${repoId}/decisions/health`,
    () => getDecisionHealth(repoId),
  );

  return <DecisionHealthWidget health={health} />;
}
