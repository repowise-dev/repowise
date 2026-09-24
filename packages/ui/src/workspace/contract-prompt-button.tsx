"use client";

import { useState } from "react";
import { AiPromptButton } from "../health/ai-prompt-button";
import { AiPromptModal } from "../health/ai-prompt-modal";
import { buildContractAiPrompt, type BuildContractPromptOptions } from "./contract-ai-prompt";

/**
 * The AI prompt for one contract: the button and the dialog it opens. Used by
 * the contract drawer and the contract detail page, so both hand an agent the
 * same prompt.
 */
export function ContractPromptButton({
  contract,
  links,
  unmatchedReason = null,
  breakingChanges = [],
}: Omit<BuildContractPromptOptions, "flavor">) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <AiPromptButton onClick={() => setOpen(true)} label="AI prompt" />
      <AiPromptModal
        open={open}
        onOpenChange={setOpen}
        filePath={`${contract.repo}/${contract.file_path}`}
        title="AI prompt for this contract"
        description="The contract, where it lives, how it was found and what sits on the other side, written up so an agent can verify it and act."
        getPrompt={(flavor) =>
          buildContractAiPrompt({ contract, links, unmatchedReason, breakingChanges, flavor })
        }
      />
    </>
  );
}
