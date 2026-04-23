"use client"

import { useState, useRef, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Spinner } from "@/components/ui/spinner"
import { Send, Upload, MessageSquare, Lightbulb, Paperclip, AlertCircle } from "lucide-react"
import { cn } from "@/lib/utils"
import type { ChatMessage } from "@/lib/api/types"

interface ChatPanelProps {
  messages: ChatMessage[]
  onSendMessage: (message: string) => void
  onUploadFile: (file: File) => void
  onRequestHint: () => void
  onContinueAnswer: () => void
  isLoading?: boolean
  isSending?: boolean
  isUploading?: boolean
  error?: string | null
}

export function ChatPanel({
  messages,
  onSendMessage,
  onUploadFile,
  onRequestHint,
  onContinueAnswer,
  isLoading = false,
  isSending = false,
  isUploading = false,
  error,
}: ChatPanelProps) {
  const [inputValue, setInputValue] = useState("")
  const [isComposing, setIsComposing] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const scrollAreaRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    if (scrollAreaRef.current) {
      const scrollContainer = scrollAreaRef.current.querySelector("[data-radix-scroll-area-viewport]")
      if (scrollContainer) {
        scrollContainer.scrollTop = scrollContainer.scrollHeight
      }
    }
  }, [messages])

  const handleSend = () => {
    if (inputValue.trim() && !isSending) {
      onSendMessage(inputValue)
      setInputValue("")
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    const nativeEvent = e.nativeEvent as KeyboardEvent
    if (nativeEvent.isComposing || isComposing || nativeEvent.keyCode === 229) {
      return
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      onUploadFile(file)
      e.target.value = "" // Reset input
    }
  }

  const handleUploadClick = () => {
    fileInputRef.current?.click()
  }

  const isDisabled = isSending || isUploading

  return (
    <div className="relative flex h-full min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border bg-card shadow-sm">
      {/* Loading overlay */}
      {isLoading && (
        <div className="absolute inset-0 bg-background/80 flex items-center justify-center z-10 rounded-xl">
          <div className="flex flex-col items-center gap-3">
            <Spinner className="w-8 h-8" />
            <span className="text-sm text-muted-foreground">正在加载...</span>
          </div>
        </div>
      )}

      {/* Error banner */}
      {error && (
        <div className="px-4 py-3 bg-destructive/10 border-b border-destructive/20 flex items-center gap-2">
          <AlertCircle className="w-4 h-4 text-destructive flex-shrink-0" />
          <span className="text-sm text-destructive">{error}</span>
        </div>
      )}

      {/* Chat messages */}
      <ScrollArea className="min-h-0 flex-1 p-6" ref={scrollAreaRef}>
        <div className="space-y-6 max-w-3xl mx-auto">
          {messages.map((message) => (
            <div
              key={message.id}
              className={cn(
                "flex gap-3",
                message.role === "user" ? "flex-row-reverse" : "flex-row"
              )}
            >
              {/* Avatar */}
              {message.role !== "system" && (
                <Avatar className="w-9 h-9 shrink-0">
                  {message.role === "officer" ? (
                    <>
                      <AvatarImage
                        src="https://images.unsplash.com/photo-1573496359142-b8d87734a5a2?w=36&h=36&fit=crop&crop=face"
                        alt="签证官"
                      />
                      <AvatarFallback className="bg-muted text-muted-foreground text-xs">
                        VO
                      </AvatarFallback>
                    </>
                  ) : (
                    <>
                      <AvatarImage
                        src="https://images.unsplash.com/photo-1472099645785-5658abf4ff4e?w=36&h=36&fit=crop&crop=face"
                        alt="用户"
                      />
                      <AvatarFallback className="bg-primary text-primary-foreground text-xs">
                        AZ
                      </AvatarFallback>
                    </>
                  )}
                </Avatar>
              )}

              {/* Message content */}
              <div
                className={cn(
                  "flex flex-col max-w-[75%]",
                  message.role === "user" ? "items-end" : "items-start",
                  message.role === "system" && "max-w-full items-center mx-auto"
                )}
              >
                {message.role !== "system" && (
                  <div className="flex items-center gap-2 mb-1.5">
                    <span className="text-sm font-medium text-foreground">
                      {message.role === "officer" ? "签证官" : "用户"}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {message.timestamp}
                    </span>
                  </div>
                )}
                <div
                  className={cn(
                    "px-4 py-3 rounded-2xl text-sm leading-relaxed shadow-sm",
                    message.role === "user"
                      ? "bg-primary text-primary-foreground rounded-br-md"
                      : message.role === "system"
                        ? "bg-muted/50 text-muted-foreground border border-border rounded-xl text-center italic"
                        : "bg-muted/80 text-foreground rounded-bl-md border border-border"
                  )}
                >
                  {message.content}
                </div>
              </div>
            </div>
          ))}

          {/* Typing indicator */}
          {isSending && (
            <div className="flex gap-3">
              <Avatar className="w-9 h-9 shrink-0">
                <AvatarImage
                  src="https://images.unsplash.com/photo-1573496359142-b8d87734a5a2?w=36&h=36&fit=crop&crop=face"
                  alt="签证官"
                />
                <AvatarFallback className="bg-muted text-muted-foreground text-xs">
                  VO
                </AvatarFallback>
              </Avatar>
              <div className="flex flex-col items-start">
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="text-sm font-medium text-foreground">签证官</span>
                </div>
                <div className="px-4 py-3 rounded-2xl bg-muted/80 border border-border rounded-bl-md">
                  <div className="flex items-center gap-1">
                    <span className="w-2 h-2 bg-muted-foreground/50 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                    <span className="w-2 h-2 bg-muted-foreground/50 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                    <span className="w-2 h-2 bg-muted-foreground/50 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </ScrollArea>

      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        className="hidden"
        onChange={handleFileSelect}
        accept=".pdf,.png,.jpg,.jpeg"
      />

      {/* Quick actions */}
      <div className="px-6 py-4 border-t border-border bg-card">
        <div className="flex items-center justify-center gap-3 max-w-3xl mx-auto">
          <Button
            variant="outline"
            size="sm"
            onClick={onContinueAnswer}
            disabled={isDisabled}
            className="gap-2 bg-card hover:bg-primary/5 hover:text-primary hover:border-primary/30 transition-colors"
          >
            <MessageSquare className="w-4 h-4" />
            继续回答
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={handleUploadClick}
            disabled={isDisabled}
            className="gap-2 bg-card hover:bg-primary/5 hover:text-primary hover:border-primary/30 transition-colors"
          >
            {isUploading ? (
              <Spinner className="w-4 h-4" />
            ) : (
              <Upload className="w-4 h-4" />
            )}
            上传材料
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={onRequestHint}
            disabled={isDisabled}
            className="gap-2 bg-card hover:bg-primary/5 hover:text-primary hover:border-primary/30 transition-colors"
          >
            <Lightbulb className="w-4 h-4" />
            请求提示
          </Button>
        </div>
      </div>

      {/* Input area */}
      <div className="px-6 py-4 border-t border-border bg-card">
        <div className="flex items-center gap-3 max-w-3xl mx-auto">
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={handleUploadClick}
            disabled={isDisabled}
            className="shrink-0"
          >
            <Paperclip className="w-4 h-4 text-muted-foreground" />
          </Button>
          <div className="flex-1 relative">
            <textarea
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              onCompositionStart={() => setIsComposing(true)}
              onCompositionEnd={() => setIsComposing(false)}
              placeholder="输入你的回答..."
              rows={1}
              disabled={isDisabled}
              className="w-full px-4 py-3 pr-14 bg-muted/30 border border-border rounded-xl resize-none text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/40 transition-all disabled:opacity-50"
            />
            <Button
              size="icon-sm"
              onClick={handleSend}
              disabled={!inputValue.trim() || isDisabled}
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded-lg h-8 w-8"
            >
              <Send className="w-4 h-4" />
            </Button>
          </div>
        </div>
        <p className="text-xs text-muted-foreground text-center mt-3 max-w-3xl mx-auto">
          回答仅用于模拟练习，不会被保存或提交给任何机构。
        </p>
      </div>
    </div>
  )
}
