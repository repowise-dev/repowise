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
      status: "done" as const,
    })),
    isStreaming: false,
    ...(message.content.provider ? { provider: message.content.provider } : {}),
    ...(message.content.model ? { model: message.content.model } : {}),
  }));
}
