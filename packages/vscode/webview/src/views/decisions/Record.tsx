/**
 * One decision record in the panel: the shared `DecisionDetail` bound to the
 * extension host, the same binding the web's record page makes with its
 * `/api` client. Its back link and prev/next links carry the web hrefs, which
 * the view's link router turns into in-panel switches.
 */

import { useMemo } from "react";
import { ApiError, OrbLoader, ORB_STATE } from "@repowise-dev/ui/shared";
import {
  DecisionDetail,
  type DecisionDetailAdapter,
} from "@repowise-dev/ui/decisions";
import { fileHref } from "../../runtime/fileLinks";
import { DecisionRecordPrompts, decisionHref, type BoardContext } from "./Board";
import { useRead } from "./useRead";
import type { DecisionRecord } from "@repowise-dev/types/decisions";

export function DecisionRecordView({ ctx, decisionId }: { ctx: BoardContext; decisionId: string }) {
  const { host, repoId, epoch, refresh, LinkComponent } = ctx;
  const read = useRead(`decision:${decisionId}`, () => host.api.decision(decisionId), epoch);
  const decision = read.data;
  const review = useRead(
    decision && decision.currency == null ? `prompt:review:${decisionId}` : null,
    () => host.api.decisionAgentPrompt("review", decisionId),
    epoch,
  ).data;

  const adapter = useMemo<DecisionDetailAdapter>(
    () => ({
      cacheKey: `${repoId}:${decisionId}`,
      repoId,
      getLineage: () => host.api.decisionLineage(decisionId),
      getEvidence: () => host.api.decisionEvidence(decisionId),
      listSiblingIds: () =>
        host.api
          .listDecisions({ include_proposed: true, limit: 100 })
          .then((rows) => rows.map((d) => d.id)),
      listModuleSuggestions: () => host.api.moduleSuggestions(),
      // A write moves the record and the board behind it, so both refetch.
      patchDecision: async (patch) => {
        await host.api.patchDecision(decisionId, patch);
        refresh();
      },
      setPinned: async (pinned) => {
        await host.api.setDecisionPinned(decisionId, pinned);
        refresh();
      },
      decisionsHref: () => `/repos/${repoId}/decisions`,
      decisionHref: (id) => decisionHref(repoId, id),
      // No commits view in the editor; the router leaves these links inert.
      commitsHref: (opts) => {
        const qs = opts?.commit ? `?commit=${opts.commit}` : opts?.sort ? `?sort=${opts.sort}` : "";
        return `/repos/${repoId}/commits${qs}`;
      },
      hotspotsHref: () => `/repos/${repoId}/code-health`,
      fileHref,
      LinkComponent,
      ...(review ? { reviewPrompt: () => review.text } : {}),
      renderRecordPrompts: () => (decision ? <DecisionRecordPrompts ctx={ctx} decision={decision} /> : null),
    }),
    [ctx, host, repoId, decisionId, refresh, LinkComponent, review, decision],
  );

  if (read.error !== undefined && !decision) {
    return (
      <div className="mx-auto w-full max-w-3xl p-6">
        <ApiError
          title="Could not load this decision"
          message={read.error instanceof Error ? read.error.message : String(read.error)}
          onRetry={read.reload}
        />
      </div>
    );
  }
  if (!decision) {
    return (
      <div className="flex h-full items-center justify-center py-16">
        <OrbLoader state={ORB_STATE.page} size={64} label="Loading decision" />
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-3xl p-[var(--page-pad)]">
      {/* The view seeds its status, pin and scope from the record once, so it
          remounts when the record the server returns changes (prev/next, a
          write here, or an agent's write picked up on refresh). */}
      <DecisionDetail key={recordVersion(decision)} decision={decision} adapter={adapter} />
    </div>
  );
}

/** Changes whenever a field the detail view seeds its own state from changes. */
function recordVersion(d: DecisionRecord): string {
  return [
    d.id,
    d.updated_at,
    d.status,
    d.pinned ? "pinned" : "",
    d.affected_modules.join(","),
    d.affected_files.join(","),
  ].join("|");
}
