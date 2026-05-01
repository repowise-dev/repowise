"use client";

import { SymbolDrawer } from "@repowise-dev/ui/symbols/symbol-drawer";
import { SymbolGraphPanelWrapper } from "./symbol-graph-panel-wrapper";
import type { SymbolResponse } from "@/lib/api/types";

interface Props {
  symbol: SymbolResponse | null;
  repoId: string;
  onClose: () => void;
}

export function SymbolDrawerWrapper({ symbol, repoId, onClose }: Props) {
  return (
    <SymbolDrawer
      symbol={symbol}
      onClose={onClose}
      graphPanel={symbol ? <SymbolGraphPanelWrapper repoId={repoId} symbol={symbol} /> : null}
    />
  );
}
