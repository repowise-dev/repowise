"use client";

import { SymbolGraphPanel } from "@repowise-dev/ui/symbols/symbol-graph-panel";
import { useGraphMetrics, useCallersCallees } from "@/lib/hooks/use-graph";
import type { SymbolResponse } from "@/lib/api/types";

interface Props {
  repoId: string;
  symbol: SymbolResponse;
}

export function SymbolGraphPanelWrapper({ repoId, symbol }: Props) {
  const nodeId = `${symbol.file_path}::${symbol.name}`;
  const { metrics, isLoading: metricsLoading } = useGraphMetrics(repoId, nodeId);
  const { data: callData, isLoading: callsLoading } = useCallersCallees(repoId, nodeId);
  const { data: heritageData } = useCallersCallees(repoId, nodeId, {
    edge_types: "extends,implements",
  });

  return (
    <SymbolGraphPanel
      metrics={metrics}
      metricsLoading={metricsLoading}
      callData={callData}
      callsLoading={callsLoading}
      heritageData={heritageData}
    />
  );
}
