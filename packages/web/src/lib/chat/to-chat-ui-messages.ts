import type { ChatMessageResponse } from "@/lib/api/types";
import type { ChatUIMessage } from "@repowise-dev/types/chat";
import { getLegacyChatArtifactType } from "@repowise-dev/ui/chat";

/** Normalize stored API messages into the same shape produced while streaming. */
export function toChatUiMessages(
  messages: ChatMessageResponse[],
): ChatUIMessage[] {
  return messages.map((message) => ({
    id: message.id,
    serverId: message.id,
    role: message.role,
    text: message.content.text ?? "",
    toolCalls: (message.content.tool_calls ?? []).map((toolCall) => ({
      id: toolCall.id,
      name: toolCall.name,
      arguments: toolCall.arguments ?? {},
      result: toolCall.result,
      summary: toolCall.summary,
      ...(toolCall.artifact
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
  }));
}
