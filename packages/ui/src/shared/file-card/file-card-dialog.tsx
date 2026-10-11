"use client";

import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "../../ui/dialog";
import { FileCard } from "./file-card";
import type { FileCardData, FileCardLinks } from "./types";

export interface FileCardDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  data: FileCardData | null;
  links?: FileCardLinks | undefined;
}

export function FileCardDialog({ open, onOpenChange, data, links }: FileCardDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[95vw] w-[760px] max-h-[85vh] overflow-y-auto">
        {data && (
          <>
            <DialogHeader>
              <DialogTitle className="font-mono text-sm break-all leading-snug">
                {data.file_path}
              </DialogTitle>
              {data.summary ? (
                <DialogDescription className="text-xs">{data.summary}</DialogDescription>
              ) : (
                <DialogDescription className="text-xs">
                  File overview — git, docs, symbols, and risk signals at a glance.
                </DialogDescription>
              )}
            </DialogHeader>
            <FileCard data={data} links={links} hideHeader />
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
