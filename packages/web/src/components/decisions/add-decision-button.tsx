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
import { AiPromptButton } from "@repowise-dev/ui/health/ai-prompt-button";
import { ADD_WITH_AGENT_LABEL } from "@repowise-dev/ui/decisions/decision-review-lanes";
import { buildAddDecisionsPrompt } from "@/lib/decision-prompts";
import type { DecisionCreateInput } from "@repowise-dev/types/decisions";
import { createDecision } from "@/lib/api/decisions";
import { useTranslations } from "next-intl";

/**
 * The host half of recording a decision: the entry point, the POST, and the
 * refresh. The form itself is shared, so hosted mounts the same fields behind
 * its own client. Beside it, the page's agent action: hand the search for
 * unrecorded rules to the reader's agent, which a page with few decisions
 * needs most.
 */
export function AddDecisionButton({ repoId }: { repoId: string }) {
  const t = useTranslations("decisions");
  const [open, setOpen] = React.useState(false);
  const router = useRouter();

  const handleSubmit = React.useCallback(
    async (input: DecisionCreateInput) => {
      await createDecision(repoId, input);
    },
    [repoId],
  );

  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      <AiPromptButton copyText={buildAddDecisionsPrompt} label={ADD_WITH_AGENT_LABEL} />
      {/* Quieter than the agent action beside it: agents record most decisions. */}
      <Button variant="ghost" size="sm" onClick={() => setOpen(true)}>
        <Plus className="h-3.5 w-3.5" />
        {t("add.trigger")}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{t("add.title")}</DialogTitle>
            <DialogDescription>{t("add.description")}</DialogDescription>
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
    </div>
  );
}
