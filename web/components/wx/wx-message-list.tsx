"use client"

import { Bot, UserRound } from "lucide-react"
import type { ChatMessage } from "@/lib/api/types"
import { cn } from "@/lib/utils"

interface WxMessageListProps {
  messages: ChatMessage[]
  isSending: boolean
}

export function WxMessageList({ messages, isSending }: WxMessageListProps) {
  if (!messages.length && !isSending) {
    return (
      <div className="rounded-3xl border border-dashed border-white/10 bg-white/[0.04] p-5 text-center text-sm leading-6 text-slate-300">
        先用中文或英文回答签证官的问题。系统会根据你的回答继续追问。
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {messages.map((message) => {
        const isUser = message.role === "user"
        return (
          <div
            key={message.id}
            className={cn("flex gap-2", isUser ? "justify-end" : "justify-start")}
          >
            {!isUser ? (
              <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-cyan-300/15 text-cyan-100">
                <Bot className="h-4 w-4" />
              </div>
            ) : null}
            <div
              className={cn(
                "max-w-[82%] rounded-3xl px-4 py-3 text-sm leading-6 shadow-lg",
                isUser
                  ? "rounded-br-lg bg-cyan-300 text-slate-950"
                  : "rounded-bl-lg border border-white/10 bg-white/[0.08] text-slate-100",
                message.status === "error" && "border border-red-300/40 bg-red-500/15 text-red-50",
              )}
            >
              <div className="whitespace-pre-wrap break-words">{message.content}</div>
              {message.error_detail ? (
                <div className="mt-2 text-xs opacity-80">{message.error_detail}</div>
              ) : null}
            </div>
            {isUser ? (
              <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-white/10 text-white">
                <UserRound className="h-4 w-4" />
              </div>
            ) : null}
          </div>
        )
      })}
      {isSending ? (
        <div className="flex items-center gap-2 text-sm text-slate-300">
          <span className="h-2 w-2 animate-bounce rounded-full bg-cyan-200" />
          <span className="h-2 w-2 animate-bounce rounded-full bg-cyan-200 [animation-delay:120ms]" />
          <span className="h-2 w-2 animate-bounce rounded-full bg-cyan-200 [animation-delay:240ms]" />
          <span>签证官正在回复...</span>
        </div>
      ) : null}
    </div>
  )
}
