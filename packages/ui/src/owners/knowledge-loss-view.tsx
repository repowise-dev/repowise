"use client";

import * as React from "react";
import type { KnowledgeMatrix, OwnersSummary } from "@repowise-dev/types/owners";
import { AiPromptButton } from "../health/ai-prompt-button";
import { useAiPromptAction } from "../health/ai-prompt-action";
import { RowPromptAction } from "../health/row-prompt-action";
import { formatRelativeTimeOrNull } from "../lib/format";
import { CleanState } from "../shared/clean-state";
import { quietOwnerExposure, quietOwnersTogether, type QuietOwnerExposure } from "./knowledge-loss";
import { ownershipLabels, type OwnershipLabels } from "./labels";
import { OffboardingAreas } from "./offboarding-result";
import { handoverPromptRequest } from "./prompt-requests";
import { OwnerAvatar } from "./owner-avatar";

export interface KnowledgeLossViewProps {
  matrix: KnowledgeMatrix;
  summary: OwnersSummary;
  /** Person page for an owner key. */
  hrefForPerson?: ((key: string) => string) | undefined;
  /** The off-boarding what-if for one owner key. */
  hrefForScenario?: ((key: string) => string) | undefined;
  base?: string | undefined;
  repoName?: string | undefined;
  LinkComponent?: React.ElementType | undefined;
  labels?: Partial<OwnershipLabels> | undefined;
}

/**
 * Code that still changes while its main author has gone quiet. Each quiet
 * owner reads as an off-boarding that already happened, over the files that
 * changed in the last 90 days: what lost its majority author, and who is left
 * to carry it. The section's one filled prompt plans all of them together.
 */
export function KnowledgeLossView({
  matrix,
  summary,
  hrefForPerson,
  hrefForScenario,
  base,
  repoName,
  LinkComponent,
  labels: overrides,
}: KnowledgeLossViewProps) {
  const labels = React.useMemo(() => ownershipLabels(overrides), [overrides]);
  const rows = React.useMemo(
    () => quietOwnerExposure(matrix, summary.quiet_owners),
    [matrix, summary.quiet_owners],
  );
  const together = React.useMemo(
    () => quietOwnersTogether(matrix, summary.quiet_owners),
    [matrix, summary.quiet_owners],
  );
  const { openPrompt, promptModal } = useAiPromptAction();

  if (summary.quiet_owners_total === 0) {
    return (
      <CleanState
        align="start"
        title={labels.lossCleanTitle(summary.activity_window_days)}
        description={labels.lossCleanDescription(summary.people, summary.owned_files)}
      />
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="max-w-[68ch] text-[15px] leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
        {labels.lossIntro(summary.quiet_owners_total, summary.activity_window_days, rows.length)}
      </p>
      {together.totals.atRiskFiles > 0 ? (
        <AiPromptButton
          className="self-start"
          onClick={() => openPrompt(handoverPromptRequest(together, together.areas, labels, repoName))}
          label={labels.agentPrompt}
        />
      ) : (
        <CleanState
          align="start"
          title={labels.lossCarriedTitle}
          description={labels.lossCarriedDescription(summary.activity_window_days, summary.quiet_owners_total)}
        />
      )}
      <ul aria-label={labels.lossListLabel} className="flex flex-col border-t border-[var(--color-border-default)]">
        {rows.map((row) => (
          <QuietOwnerRow
            key={row.owner.key}
            row={row}
            onPrompt={(areas) => openPrompt(handoverPromptRequest(row.result, areas, labels, repoName))}
            labels={labels}
            hrefForPerson={hrefForPerson}
            hrefForScenario={hrefForScenario}
            base={base}
            LinkComponent={LinkComponent}
          />
        ))}
      </ul>
      {promptModal}
    </div>
  );
}

/** One quiet owner in words: when they last committed, what of theirs changed, what loses its majority. */
function quietOwnerSentence(
  { owner, result: { totals } }: QuietOwnerExposure,
  last: string | null,
  labels: OwnershipLabels,
): string {
  return [
    last ? labels.lossLastCommit(last) : null,
    labels.lossChanging(owner.changing_files_owned, owner.files_owned),
    totals.atRiskFiles > 0 ? labels.lossAtRisk(totals.atRiskFiles, totals.severeFiles) : labels.lossNoneAtRisk,
  ]
    .filter(Boolean)
    .join(" ");
}

function QuietOwnerRow({
  row,
  onPrompt,
  hrefForPerson,
  hrefForScenario,
  base,
  LinkComponent,
  labels,
}: {
  row: QuietOwnerExposure;
  onPrompt: (areas: QuietOwnerExposure["result"]["areas"]) => void;
  hrefForPerson?: ((key: string) => string) | undefined;
  hrefForScenario?: ((key: string) => string) | undefined;
  base?: string | undefined;
  LinkComponent?: React.ElementType | undefined;
  labels: OwnershipLabels;
}) {
  const [open, setOpen] = React.useState(false);
  const A = LinkComponent ?? "a";
  const { owner, result } = row;
  const { totals } = result;
  const panelId = React.useId();
  const personHref = hrefForPerson?.(owner.key);

  return (
    <li className="group/row border-b border-[var(--color-border-default)] py-3">
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <OwnerAvatar name={owner.name} size="md" />
        <div className="min-w-0 flex-1">
          {personHref ? (
            <A href={personHref} className="font-medium text-[var(--color-text-primary)] no-underline hover:text-[var(--color-accent-primary)]">
              {owner.name}
            </A>
          ) : (
            <span className="font-medium text-[var(--color-text-primary)]">{owner.name}</span>
          )}
          <p className="text-xs leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
            {quietOwnerSentence(row, formatRelativeTimeOrNull(owner.last_commit_at), labels)}
          </p>
          <div className="mt-1 flex flex-wrap gap-x-4 text-xs">
            {totals.atRiskFiles > 0 && (
              <button
                type="button"
                aria-expanded={open}
                aria-controls={panelId}
                onClick={() => setOpen((o) => !o)}
                className="text-[var(--color-accent-primary)] hover:underline"
              >
                {labels.lossDirectories(open, result.areas.length)}
              </button>
            )}
            {hrefForScenario && (
              <A href={hrefForScenario(owner.key)} className="text-[var(--color-accent-primary)] hover:underline">
                {labels.openInOffboarding}
              </A>
            )}
          </div>
        </div>
        {totals.atRiskFiles > 0 && (
          <RowPromptAction
            onClick={() => onPrompt(result.areas)}
            label={labels.promptForGroup(totals.atRiskFiles)}
            subject={owner.name}
          />
        )}
      </div>
      {open && (
        <div id={panelId} className="mt-3 pl-11">
          <OffboardingAreas
            areas={result.areas}
            onPrompt={(area) => onPrompt([area])}
            labels={labels}
            base={base}
            LinkComponent={LinkComponent}
            initial={5}
          />
        </div>
      )}
    </li>
  );
}
