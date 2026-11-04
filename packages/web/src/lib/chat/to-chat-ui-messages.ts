import type { ChatMessageResponse } from "@/lib/api/types";
import type { ChatUIMessage } from "@repowise-dev/types/chat";
import { getLegacyChatArtifactType } from "@repowise-dev/ui/chat";
import { parseEvidenceCards } from "@repowise-dev/api-client/cards";
import { VIEW_TOOLS, parseViewParts } from "@repowise-dev/api-client/views";

/** Stored cards are untrusted wire data; only the ones that validate render. */
function storedCards(message: ChatMessageResponse): Pick<ChatUIMessage, "cards"> {
  const cards = parseEvidenceCards(message.content.cards);
  return cards.length ? { cards } : {};
}

/** Stored views are untrusted wire data too; same rule as the live stream. */
function storedViews(message: ChatMessageResponse): Pick<ChatUIMessage, "views"> {
  const views = parseViewParts(message.content.views);
  return views.length ? { views } : {};
}

/** Figures the numeric check found in no tool result; offsets index `content.text`. */
function storedUnverifiedFigures(
  message: ChatMessageResponse,
): Pick<ChatUIMessage, "unverifiedFigures"> {
  const spans = (message.content.figures ?? [])
    .filter((figure) => !figure.verified)
    .map(({ text, start, end }) => ({ text, start, end }));
  return spans.length ? { unverifiedFigures: spans } : {};
}

/** Normalize stored API messages into the same shape produced while streaming. */
export function toChatUiMessages(
  messages: ChatMessageResponse[],
): ChatUIMessage[] {
  return messages.map((message) => ({
    id: message.id,
    serverId: message.id,
    role: message.role,
    text: message.content.text ?? "",
    toolCalls: (message.content.tool_calls ?? [])
      .map((toolCall) => ({
        id: toolCall.id,
        name: toolCall.name,
        arguments: toolCall.arguments ?? {},
        result: toolCall.result,
        summary: toolCall.summary,
        // A view tool's receipt is not evidence; the view itself renders inline.
        ...(VIEW_TOOLS.has(toolCall.name)
          ? {}
          : toolCall.artifact
          ? { artifact: toolCall.artifact }
          : toolCall.result
          ? {
              artifact: {
                id: `legacy-${message.id}-${toolCall.id}`,
                version: 1 as const,
                type:
                  toolCall.artifact_type ??
                  getLegacyChatArtifactType(toolCall.name),
                tool_name: toolCall.name,
                title: toolCall.summary ?? toolCall.name,
                presentation:
                  toolCall.artifact_type ??
                  getLegacyChatArtifactType(toolCall.name),
                evidence: { basis: "unknown" },
                pinned: false,
                data: toolCall.result,
              },
            }
          : {}),
        // The wire never persists a status, so reload it from the same `error`
        // key the live stream reads. Without this a failed read comes back from
        // storage looking like a good one, and is cited as evidence again.
        status:
          typeof toolCall.result === "object" &&
          toolCall.result !== null &&
          "error" in toolCall.result
            ? ("error" as const)
            : ("done" as const),
        ...(toolCall.origin ? { origin: toolCall.origin } : {}),
      })),
    isStreaming: false,
    ...(message.content.provider ? { provider: message.content.provider } : {}),
    ...(message.content.model ? { model: message.content.model } : {}),
    ...(message.content.truncated ? { truncated: true } : {}),
    ...(message.content.follow_ups?.length
      ? { followUps: message.content.follow_ups }
      : {}),
    ...storedCards(message),
    ...storedViews(message),
    ...storedUnverifiedFigures(message),
  }));
}
