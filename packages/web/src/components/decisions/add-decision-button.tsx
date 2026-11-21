"use client";

import * as React from "react";
import useSWR from "swr";
import { useRouter } from "next/navigation";
import { DecisionCreateForm } from "@repowise-dev/ui/decisions/decision-create-form";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@repowise-dev/ui/ui/dialog";
import {
  DecisionAgentHero,
  DecisionAgentPrompts,
} from "@repowise-dev/ui/decisions/decision-agent-prompts";
import { recordPromptKinds } from "@repowise-dev/ui/decisions/decision-review-lanes";
import { AiPromptButton } from "@repowise-dev/ui/health/ai-prompt-button";
import type {
  DecisionAgentPromptKind,
  DecisionCreateInput,
  DecisionRecord,
} from "@repowise-dev/types/decisions";
import { createDecision, getDecisionAgentPrompt } from "@/lib/api/decisions";
import { useTranslations } from "next-intl";

const FOOT_LINK =
  "rounded text-xs text-[var(--color-accent-primary)] underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]";

/**
 * The page's handoff: the discovery prompt the server rendered, the same text
 * `repowise decision discover` prints, behind one copy button. Beside it the
 * other two prompts (record what nobody wrote down, move working agreements
 * into CLAUDE.md) and the manual path, a person recording one by hand. The
 * form is shared, so hosted mounts the same fields behind its own client.
 */
export function DecisionAgentPanel({ repoId }: { repoId: string }) {
  const t = useTranslations("decisions");
  const [open, setOpen] = React.useState(false);
  const router = useRouter();
  const { data: discover, error } = useDecisionAgentPromptState(repoId, "discover");
  const agreements = useDecisionAgentPrompt(repoId, "agreements");
  const add = useDecisionAgentPrompt(repoId, "add");
  // A server that predates the kind refuses it; the read then fails and no button shows.
  const reviewAll = useDecisionAgentPrompt(repoId, "review_all");

  const handleSubmit = React.useCallback(
    async (input: DecisionCreateInput) => {
      await createDecision(repoId, input);
    },
    [repoId],
  );

  // The server turns `discover` into `add` on a store with nothing to check,
  // so the hero is then the add pass and the add link would repeat it.
  const checking = discover?.kind === "discover";
  const heading = discover
    ? checking
      ? t("hero.discoverHeading", { count: discover.count })
      : t("hero.addHeading")
    : undefined;
  const description = discover
    ? checking
      ? t("hero.discoverDescription")
      : t("hero.addDescription")
    : undefined;

  return (
    <>
      <DecisionAgentHero
        prompt={error ? null : discover}
        {...(heading ? { heading } : {})}
        {...(description ? { description } : {})}
        copyLabel={t("hero.copy")}
        copiedLabel={t("hero.copied")}
        failedLabel={t("hero.copyFailed")}
        reviewAll={reviewAll && { text: reviewAll.text, label: t("hero.reviewAll") }}
        footnote={
          <span>
            {t.rich("hero.footnote", { code: (chunks) => <code>{chunks}</code> })}
          </span>
        }
        links={
          <>
            {checking && add?.text && (
              <AiPromptButton variant="ghost" copyText={() => add.text} label={t("hero.recordMissed")} />
            )}
            {agreements?.text && (
              <AiPromptButton
                variant="ghost"
                copyText={() => agreements.text}
                label={t("agent.agreements")}
              />
            )}
            <button type="button" className={FOOT_LINK} onClick={() => setOpen(true)}>
              {t("hero.addByHand")}
            </button>
          </>
        }
      />
      {/* No discover prompt (a failed read): the manual path stays reachable. */}
      {error && (
        <div className="flex justify-end">
          <button type="button" className={FOOT_LINK} onClick={() => setOpen(true)}>
            {t("add.trigger")}
          </button>
        </div>
      )}
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
    </>
  );
}

/** The SWR key of one decision prompt, shared by every reader of it. */
export function decisionPromptKey(
  repoId: string,
  kind: DecisionAgentPromptKind,
  decisionId?: string,
) {
  return [`/api/repos/${repoId}/decisions/agent-prompt`, kind, decisionId ?? ""] as const;
}

function useDecisionAgentPromptState(
  repoId: string,
  kind: DecisionAgentPromptKind | null,
  decisionId?: string,
) {
  // No retry loop on a failed read: a server that refuses the kind keeps
  // refusing it. Focus revalidation still recovers from a passing failure.
  return useSWR(
    kind ? decisionPromptKey(repoId, kind, decisionId) : null,
    () => getDecisionAgentPrompt(repoId, kind!, decisionId),
    { shouldRetryOnError: false },
  );
}

/**
 * One decision prompt from the server. Revalidated on focus, so after the
 * agent's pass the copied list names what is still left. A failed read shows
 * no button rather than an error: the prompt is an extra, not the page.
 */
export function useDecisionAgentPrompt(
  repoId: string,
  kind: DecisionAgentPromptKind | null,
  decisionId?: string,
) {
  return useDecisionAgentPromptState(repoId, kind, decisionId).data;
}

/**
 * One record's agent prompts, rendered by the server for its id, for the
 * record's drawer. Each kind is its own fetch, so the buttons appear as their
 * prompts arrive.
 */
export function DecisionRecordPrompts({
  repoId,
  decision: d,
}: {
  repoId: string;
  decision: Pick<DecisionRecord, "id" | "currency" | "pinned" | "status">;
}) {
  return (
    <>
      {recordPromptKinds(d).map((kind) => (
        <RecordPrompt key={kind} repoId={repoId} decisionId={d.id} kind={kind} />
      ))}
    </>
  );
}

function RecordPrompt({
  repoId,
  decisionId,
  kind,
}: {
  repoId: string;
  decisionId: string;
  kind: ReturnType<typeof recordPromptKinds>[number];
}) {
  const t = useTranslations("decisions");
  const prompt = useDecisionAgentPrompt(repoId, kind, decisionId);
  return (
    <DecisionAgentPrompts
      prompts={[prompt ? { text: prompt.text, label: t(`agent.${kind}`) } : null]}
    />
  );
}
