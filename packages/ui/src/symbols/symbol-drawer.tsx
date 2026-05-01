"use client";

import type { ReactNode } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "../ui/dialog";
import { Badge } from "../ui/badge";
import { ScrollArea } from "../ui/scroll-area";
import { Separator } from "../ui/separator";
import type { CodeSymbol } from "@repowise-dev/types/symbols";

interface SymbolDrawerProps {
  symbol: CodeSymbol | null;
  onClose: () => void;
  /**
   * Right-column graph intelligence slot. Wrappers pass a data-coupled
   * `<SymbolGraphPanelWrapper>` here; pass `null` to hide the column.
   */
  graphPanel?: ReactNode;
}

export function SymbolDrawer({ symbol, onClose, graphPanel }: SymbolDrawerProps) {
  return (
    <Dialog open={symbol !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-[90vw] w-[900px] max-h-[85vh] overflow-hidden p-0">
        {symbol && (
          <>
            <div className="px-6 pt-6 pb-3">
              <DialogHeader>
                <DialogTitle className="font-mono text-base">{symbol.name}</DialogTitle>
                <DialogDescription className="font-mono text-xs text-[var(--color-text-tertiary)] break-all">
                  {symbol.file_path}:{symbol.start_line}
                </DialogDescription>
              </DialogHeader>
              <div className="flex flex-wrap gap-1.5 mt-2">
                <Badge variant="accent">{symbol.kind}</Badge>
                <Badge variant="outline">{symbol.language}</Badge>
                {symbol.visibility && symbol.visibility !== "public" && (
                  <Badge variant="default">{symbol.visibility}</Badge>
                )}
                {symbol.is_async && <Badge variant="default">async</Badge>}
                {symbol.complexity_estimate > 10 && (
                  <Badge variant="stale">complexity {symbol.complexity_estimate}</Badge>
                )}
              </div>
            </div>

            <Separator />

            <div className="flex min-h-0 flex-1 overflow-hidden" style={{ maxHeight: "calc(85vh - 120px)" }}>
              <ScrollArea className="flex-1 min-w-0">
                <div className="px-6 py-4 space-y-3">
                  <div className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]">
                    <pre className="p-4 text-xs font-mono text-[var(--color-text-primary)] whitespace-pre-wrap break-all">
                      <code>{symbol.signature || symbol.name}</code>
                    </pre>
                  </div>

                  {symbol.docstring && (
                    <div className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3">
                      <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-1.5">
                        Docstring
                      </p>
                      <p className="text-sm text-[var(--color-text-secondary)] whitespace-pre-wrap">
                        {symbol.docstring}
                      </p>
                    </div>
                  )}

                  {symbol.parent_name && (
                    <p className="text-xs text-[var(--color-text-tertiary)]">
                      Parent: <span className="font-mono text-[var(--color-text-secondary)]">{symbol.parent_name}</span>
                    </p>
                  )}
                </div>
              </ScrollArea>

              {graphPanel && (
                <div className="hidden md:flex flex-col border-l border-[var(--color-border-default)] bg-[var(--color-bg-surface)] w-[280px] shrink-0 overflow-hidden">
                  {graphPanel}
                </div>
              )}
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
