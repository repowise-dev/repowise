"use client";

/**
 * Decision detail host — binds the shared {@link SharedDecisionDetail} to web's
 * `/api` client, `/repos/:id` routing, and `next/link`. The composition
 * (markdown body, lineage, module-link editor, evidence drawer, AI prompt,
 * confirm/undo status actions, pin) lives in `@repowise-dev/ui/decisions`; this
 * file only injects the app-specific pieces so web and hosted render the same
 * view. The agent prompts are the server's, the same text the CLI prints.
 */

import Link from "next/link";
import {
  DecisionDetail as SharedDecisionDetail,
  type DecisionDetailAdapter,
} from "@repowise-dev/ui/decisions";
import {
  getDecisionEvidence,
  getDecisionLineage,
  listDecisions,
  patchDecision,
  setDecisionPinned,
} from "@/lib/api/decisions";
import { listModuleHealth } from "@/lib/api/modules";
import type { DecisionRecordResponse } from "@/lib/api/types";
import { DecisionRecordPrompts, useDecisionAgentPrompt } from "./add-decision-button";

interface DecisionDetailProps {
  decision: DecisionRecordResponse;
  repoId: string;
}

export function DecisionDetail({ decision, repoId }: DecisionDetailProps) {
  const prefix = `/repos/${repoId}`;
  const review = useDecisionAgentPrompt(
    repoId,
    decision.currency == null ? "review" : null,
    decision.id,
  );

  const adapter: DecisionDetailAdapter = {
    cacheKey: `${repoId}:${decision.id}`,
    repoId,
    getLineage: () => getDecisionLineage(repoId, decision.id),
    getEvidence: () => getDecisionEvidence(repoId, decision.id),
    listSiblingIds: () =>
      listDecisions(repoId, { include_proposed: true, limit: 100 }).then((rows) =>
        rows.map((d) => d.id),
      ),
    listModuleSuggestions: () =>
      listModuleHealth(repoId, { sort: "file_count", limit: 500 }).then((m) =>
        m.items.map((x) => x.module_path),
      ),
    patchDecision: async (patch) => {
      await patchDecision(repoId, decision.id, patch);
    },
    decisionsHref: () => `${prefix}/decisions`,
    decisionHref: (id) => `${prefix}/decisions/${id}`,
    commitsHref: (opts) => {
      const qs = opts?.commit
        ? `?commit=${opts.commit}`
        : opts?.sort
          ? `?sort=${opts.sort}`
          : "";
      return `${prefix}/commits${qs}`;
    },
    hotspotsHref: () => `${prefix}/code-health`,
    LinkComponent: Link,
    ...(review ? { reviewPrompt: () => review.text } : {}),
    setPinned: async (pinned) => {
      await setDecisionPinned(repoId, decision.id, pinned);
    },
    renderRecordPrompts: () => <DecisionRecordPrompts repoId={repoId} decision={decision} />,
  };

  return <SharedDecisionDetail decision={decision} adapter={adapter} />;
}
