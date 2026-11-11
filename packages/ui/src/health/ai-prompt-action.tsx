"use client";

import { lazy, Suspense, useCallback, useMemo, useState, type ReactNode } from "react";
import type { ChatContext } from "@repowise-dev/types/chat";

import type { AiPromptFlavor } from "./ai-prompt-builder";
import type { PromptSource } from "./ai-prompt-modal";
import { formatNumber } from "../lib/format";

/**
 * The one "prompt for your agent" action: a surface describes the prompt it
 * wants, and this opens the shared modal on it. People fix issues through
 * their agents, so this is the primary verb on every actionable item; the
 * modal is loaded only the first time somebody asks for a prompt.
 */

const AiPromptModal = lazy(() =>
  import("./ai-prompt-modal").then((module) => ({ default: module.AiPromptModal })),
);

/** The label every actionable row's primary button carries. */
export const AGENT_PROMPT_LABEL = "Copy prompt";

/** The same verb for a group: "Copy prompt for these 4", or "this one" for one. */
export function bulkAgentPromptLabel(count: number): string {
  return count === 1 ? "Copy prompt for this one" : `Copy prompt for these ${formatNumber(count)}`;
}

export interface AiPromptRequest {
  title: string;
  description?: string;
  /** One-line identifier beside the title, usually a path. */
  filePath?: string | null;
  /** A pure builder; ignored when `promptSource` is given. */
  getPrompt?: ((flavor: AiPromptFlavor) => string) | null;
  /** A fetched prompt. Held for the life of the request, so it fetches once per flavor. */
  promptSource?: PromptSource;
  chatContext?: ChatContext;
}

export function useAiPromptAction(): {
  openPrompt: (request: AiPromptRequest) => void;
  promptModal: ReactNode;
} {
  const [request, setRequest] = useState<AiPromptRequest | null>(null);
  const openPrompt = useCallback((next: AiPromptRequest) => setRequest(next), []);
  const promptModal = useMemo(
    () =>
      request ? (
        <Suspense fallback={null}>
          <AiPromptModal
            open
            onOpenChange={(open) => {
              if (!open) setRequest(null);
            }}
            title={request.title}
            {...(request.description ? { description: request.description } : {})}
            filePath={request.filePath ?? null}
            getPrompt={request.getPrompt ?? null}
            promptSource={request.promptSource}
            chatContext={request.chatContext}
          />
        </Suspense>
      ) : null,
    [request],
  );
  return { openPrompt, promptModal };
}
