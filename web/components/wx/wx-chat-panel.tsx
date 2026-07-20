"use client"

import type { ChatMessage } from "@/lib/api/types"
import { WxComposer } from "@/components/wx/wx-composer"
import { WxMessageList } from "@/components/wx/wx-message-list"

interface WxChatPanelProps {
  messages: ChatMessage[]
  isSending: boolean
  isSessionTerminal?: boolean
  error?: string | null
  onSend: (content: string) => Promise<void>
  onRetryMessage?: (message: ChatMessage) => void
}

export function WxChatPanel({
  messages,
  isSending,
  isSessionTerminal,
  error,
  onSend,
  onRetryMessage,
}: WxChatPanelProps) {
  return (
    <section className="flex min-h-[520px] flex-col rounded-[2rem] border border-white/10 bg-black/25 text-white shadow-2xl">
      <div className="border-b border-white/10 px-4 py-3">
        <h2 className="text-base font-semibold">模拟面签问答</h2>
        <p className="mt-1 text-xs text-slate-300">第一版微信入口使用非流式回复，稳定优先。</p>
      </div>
      <div className="flex-1 overflow-y-auto px-4 py-4">
        <WxMessageList
          messages={messages}
          isSending={isSending}
          isSessionTerminal={isSessionTerminal}
          onRetryMessage={onRetryMessage}
        />
      </div>
      {error ? (
        <div className="mx-4 mb-3 rounded-2xl border border-red-400/25 bg-red-500/10 px-3 py-2 text-xs leading-5 text-red-100">
          {error}
        </div>
      ) : null}
      <div className="border-t border-white/10 p-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))]">
        <WxComposer
          disabled={isSending || Boolean(isSessionTerminal)}
          isSending={isSending}
          onSend={onSend}
        />
      </div>
    </section>
  )
}
