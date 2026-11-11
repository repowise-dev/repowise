"use client";

import * as React from "react";
import useSWR from "swr";
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
import { DecisionAgentPrompts } from "@repowise-dev/ui/decisions/decision-agent-prompts";
import { ADD_WITH_AGENT_LABEL } from "@repowise-dev/ui/decisions/decision-review-lanes";
import type {
  DecisionAgentPromptKind,
  DecisionCreateInput,
  DecisionRecord,
} from "@repowise-dev/types/decisions";
import { createDecision, getDecisionAgentPrompt } from "@/lib/api/decisions";
import { useTranslations } from "next-intl";

/**
 * The host half of recording a decision: the entry point, the POST, and the
 * refresh. The form itself is shared, so hosted mounts the same fields behind
 * its own client. Beside it, the page's agent actions: check the background
 * records on the busiest files, move working agreements into CLAUDE.md, and
 * record what nobody wrote down. The server renders each prompt, the same
 * text `repowise decision discover` prints.
 */
export function AddDecisionButton({ repoId }: { repoId: string }) {
  const t = useTranslations("decisions");
  const [open, setOpen] = React.useState(false);
  const router = useRouter();
  const discover = useDecisionAgentPrompt(repoId, "discover");
  const agreements = useDecisionAgentPrompt(repoId, "agreements");
  const add = useDecisionAgentPrompt(repoId, "add");

  const handleSubmit = React.useCallback(
    async (input: DecisionCreateInput) => {
      await createDecision(repoId, input);
    },
    [repoId],
  );

  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      <DecisionAgentPrompts
        discover={
          discover?.kind === "discover"
            ? { text: discover.text, label: t("agent.discover") }
            : null
        }
        agreements={agreements ? { text: agreements.text, label: t("agent.agreements") } : null}
        add={add ? { text: add.text, label: ADD_WITH_AGENT_LABEL } : null}
      />
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

/**
 * One decision prompt from the server. Keyed the way the review lanes key it,
 * so the Background lane and this row share one fetch. Revalidated on focus,
 * so after the agent's pass the copied list names what is still left. A failed
 * read shows no button rather than an error: the prompt is an extra, not the page.
 */
export function useDecisionAgentPrompt(
  repoId: string,
  kind: DecisionAgentPromptKind | null,
  decisionId?: string,
) {
  const { data } = useSWR(
    kind ? [`/api/repos/${repoId}/decisions/agent-prompt`, kind, decisionId ?? ""] : null,
    () => getDecisionAgentPrompt(repoId, kind!, decisionId),
  );
  return data;
}

/**
 * One record's agent prompts, rendered by the server for its id: a background
 * record gets the check; a decision in force gets pin or unpin, veto, and
 * change. A retired one gets none.
 */
export function DecisionRecordPrompts({
  repoId,
  decision: d,
}: {
  repoId: string;
  decision: Pick<DecisionRecord, "id" | "currency" | "pinned" | "status">;
}) {
  const t = useTranslations("decisions");
  const retired = d.currency === "superseded" || d.currency === "dismissed" || d.currency === "stale";
  const inForce = d.currency != null && !retired;
  // A dismissed or retired record was settled; checking it again would undo that.
  const background = d.currency == null && d.status === "proposed";
  const pinKind = d.pinned ? "unpin" : "pin";
  const review = useDecisionAgentPrompt(repoId, background ? "review" : null, d.id);
  const pin = useDecisionAgentPrompt(repoId, inForce ? pinKind : null, d.id);
  const veto = useDecisionAgentPrompt(repoId, inForce ? "veto" : null, d.id);
  const edit = useDecisionAgentPrompt(repoId, inForce ? "edit" : null, d.id);
  return (
    <DecisionAgentPrompts
      prompts={[
        review ? { text: review.text, label: t("agent.review") } : null,
        edit ? { text: edit.text, label: t("agent.edit") } : null,
        veto ? { text: veto.text, label: t("agent.veto") } : null,
        pin ? { text: pin.text, label: t(d.pinned ? "agent.unpin" : "agent.pin") } : null,
      ]}
    />
  );
}
