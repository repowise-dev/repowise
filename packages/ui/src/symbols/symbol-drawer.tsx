"use client";

import { Flame } from "lucide-react";
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
import type { SymbolDetailData } from "@repowise-dev/types/symbols";
import { SymbolDetailBody } from "./symbol-detail-body";

interface SymbolDrawerProps {
  /** Normalized symbol body data — null hides the modal. */
  data: SymbolDetailData | null;
  onClose: () => void;
  /** The optional graph/git feeds are streamed in by the wrapper. */
  metricsLoading?: boolean;
  /** Build an href to a sibling symbol page. */
  symbolHref?: (symbolId: string) => string;
  /** Build an href to the parent file page. */
  fileHref?: (filePath: string) => string;
  onOpenBlastRadius?: () => void;
}

/**
 * The modal symbol surface. Renders the SAME `SymbolDetailBody` as the routed
 * `SymbolPage`, so the drawer and the page expose identical capabilities
 * (graph metrics, call graph, git context, co-changes, dead code, blast radius).
 */
export function SymbolDrawer({
  data,
  onClose,
  metricsLoading,
  symbolHref,
  fileHref,
  onOpenBlastRadius,
}: SymbolDrawerProps) {
  return (
    <Dialog open={data !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-[95vw] w-[1000px] max-h-[88vh] overflow-hidden p-0">
        {data && (
          <>
            <div className="px-6 pt-6 pb-3">
              <DialogHeader>
                <DialogTitle className="font-mono text-base">
                  {data.identity.name}
                </DialogTitle>
                <DialogDescription className="break-all font-mono text-xs text-[var(--color-text-tertiary)]">
                  {data.identity.file_path}:{data.identity.start_line}
                </DialogDescription>
              </DialogHeader>
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <Badge variant="accent">{data.identity.kind}</Badge>
                {data.identity.language && (
                  <Badge variant="outline">{data.identity.language}</Badge>
                )}
                {data.identity.visibility &&
                  data.identity.visibility !== "public" && (
                    <Badge variant="default">{data.identity.visibility}</Badge>
                  )}
                {data.identity.is_async && <Badge variant="default">async</Badge>}
                {data.identity.file_is_hotspot && (
                  <Badge
                    variant="outline"
                    className="text-[var(--color-error)] border-[var(--color-error)]/30"
                  >
                    <Flame className="h-2.5 w-2.5" /> hot file
                  </Badge>
                )}
              </div>
            </div>

            <Separator />

            <ScrollArea className="max-h-[calc(85vh-120px)]">
              <div className="px-6 py-4">
                <SymbolDetailBody
                  data={data}
                  {...(metricsLoading != null ? { metricsLoading } : {})}
                  {...(symbolHref ? { symbolHref } : {})}
                  {...(fileHref ? { fileHref } : {})}
                  {...(onOpenBlastRadius ? { onOpenBlastRadius } : {})}
                />
              </div>
            </ScrollArea>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
