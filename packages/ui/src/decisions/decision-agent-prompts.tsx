"use client";

import { AiPromptButton } from "../health/ai-prompt-button";

/** One decision prompt the host fetched, with the label its locale gives it. */
export interface DecisionAgentPrompt {
  /** The prompt as the server rendered it, the text `repowise decision discover` prints. */
  text: string;
  label: string;
}

export interface DecisionAgentPromptsProps {
  /**
   * The discovery pass: check the background records on the busiest files and
   * put the real ones in force. Pass it only when the server served `discover`,
   * so a store with nothing to check shows no button for it.
   */
  discover?: DecisionAgentPrompt | null;
  /** Move stored working agreements into CLAUDE.md. Omit when none is stored. */
  agreements?: DecisionAgentPrompt | null;
  /** Record the decisions nobody wrote down. */
  add?: DecisionAgentPrompt | null;
}

/**
 * The decisions page's agent actions, each a copy button over a prompt the
 * server rendered. The text is never built here: the same words reach the CLI,
 * so an agent run from the terminal and one handed the copied prompt do the
 * same pass. Renders nothing until a prompt is passed.
 */
export function DecisionAgentPrompts({ discover, agreements, add }: DecisionAgentPromptsProps) {
  const shown = [discover, agreements, add].filter(
    (p): p is DecisionAgentPrompt => Boolean(p && p.text),
  );
  if (shown.length === 0) return null;
  return (
    <>
      {shown.map((p) => (
        <AiPromptButton key={p.label} copyText={() => p.text} label={p.label} />
      ))}
    </>
  );
}
