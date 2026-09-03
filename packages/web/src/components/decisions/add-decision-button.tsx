"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Plus } from "lucide-react";
import { DecisionCreateForm } from "@repowise-dev/ui/decisions/decision-create-form";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@repowise-dev/ui/ui/dialog";
import { Button } from "@repowise-dev/ui/ui/button";
import type { DecisionCreateInput } from "@repowise-dev/types/decisions";
import { createDecision } from "@/lib/api/decisions";

/**
 * The host half of recording a decision: the entry point, the POST, and the
 * refresh. The form itself is shared, so hosted mounts the same fields behind
 * its own client.
 */
export function AddDecisionButton({ repoId }: { repoId: string }) {
  const [open, setOpen] = React.useState(false);
  const router = useRouter();

  const handleSubmit = React.useCallback(
    async (input: DecisionCreateInput) => {
      await createDecision(repoId, input);
    },
    [repoId],
  );

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        <Plus className="h-3.5 w-3.5" />
        Record a decision
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Record a decision</DialogTitle>
            <DialogDescription>
              Something the team has settled on. Name the files it governs and
              it is recorded as confirmed, because you are the person
              confirming it.
            </DialogDescription>
          </DialogHeader>
          <DecisionCreateForm
            onSubmit={handleSubmit}
            onCreated={() => {
              setOpen(false);
              // The page is a server component reading the list, so the new
              // row arrives on a refetch rather than through the table's own
              // SWR cache.
              router.refresh();
            }}
            onCancel={() => setOpen(false)}
          />
        </DialogContent>
      </Dialog>
    </>
  );
}
