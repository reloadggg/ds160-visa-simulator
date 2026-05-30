import type {
  ChatMessage,
  MessageResponse,
  MessageStreamEvent,
} from "@/lib/api/types"

export type ChatMessageDraft = Omit<ChatMessage, "id" | "timestamp">

export function buildAssistantMessageFromBackendResponse(
  response: Pick<MessageResponse, "assistant_message" | "public_reasoning">,
): ChatMessageDraft | null {
  const content = response.assistant_message.trim()
  if (!content) {
    return null
  }

  return {
    role: "assistant",
    content,
    public_reasoning: response.public_reasoning ?? null,
  }
}

export function transcriptMessagesFromMessageStreamEvent(
  event: MessageStreamEvent,
): ChatMessageDraft[] {
  void event
  return []
}
