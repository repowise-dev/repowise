"use client";

import { AiPromptButton } from "./ai-prompt-button";
import { AGENT_PROMPT_LABEL, useAiPromptAction } from "./ai-prompt-action";

/** One entry of a finding's `details.actions`, as the health engine writes it. */
interface FindingAction {
  kind: string;
  prompt?: string;
  target_test?: string;
  how?: string;
}

const PROMPT_TITLE: Record<string, string> = {
  "add-tests": "Add tests",
  refactor: "Simplify, then test",
};

function readActions(details: Record<string, unknown> | null | undefined): FindingAction[] {
  const raw = details?.actions;
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (a): a is FindingAction =>
      typeof a === "object" && a !== null && typeof (a as FindingAction).kind === "string",
  );
}

/**
 * A finding's own actions: the copy-prompt first (the primary way to act on
 * it), then the test to extend and the rule that would silence it. Renders
 * nothing for a finding that carries no actions.
 */
export function FindingActions({
  details,
  filePath,
}: {
  details: Record<string, unknown> | null | undefined;
  filePath?: string | null;
}) {
  const { openPrompt, promptModal } = useAiPromptAction();
  const actions = readActions(details);
  const lead = actions.find((a) => typeof a.prompt === "string" && a.prompt.length > 0);
  const suppress = actions.find((a) => a.kind === "suppress" && a.how);
  if (!lead && !suppress) return null;
  return (
    <div className="flex flex-col gap-1.5 text-xs text-[var(--color-text-secondary)]">
      {lead ? (
        <div className="flex flex-wrap items-center gap-2">
          <AiPromptButton
            label={AGENT_PROMPT_LABEL}
            onClick={() =>
              openPrompt({
                title: PROMPT_TITLE[lead.kind] ?? "Act on this finding",
                filePath: filePath ?? null,
                getPrompt: () => lead.prompt ?? "",
              })
            }
          />
          {lead.target_test ? (
            <span>
              Extend <code className="font-mono">{lead.target_test}</code>
            </span>
          ) : null}
        </div>
      ) : null}
      {suppress ? (
        <p>
          <span className="font-medium text-[var(--color-text-primary)]">To silence it: </span>
          <code className="font-mono break-all">{suppress.how}</code>
        </p>
      ) : null}
      {promptModal}
    </div>
  );
}
