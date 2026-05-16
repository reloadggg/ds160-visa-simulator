"use client"

import { useState, useRef, useEffect } from "react"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Spinner } from "@/components/ui/spinner"
import { Send, Paperclip, AlertCircle, X, FileIcon, ImageIcon, FileText } from "lucide-react"
import { cn } from "@/lib/utils"
import type { ChatAttachment, ChatMessage, ComposerCommand } from "@/lib/api/types"

interface ChatPanelProps {
  messages: ChatMessage[]
  onSendMessage: (message: string, files?: File[]) => void
  userName: string
  userAvatarUrl: string
  isLoading?: boolean
  isSending?: boolean
  isUploading?: boolean
  error?: string | null
  composerCommand?: ComposerCommand | null
  onComposerCommandHandled?: () => void
}

export function ChatPanel({
  messages,
  onSendMessage,
  userName,
  userAvatarUrl,
  isLoading = false,
  isSending = false,
  isUploading = false,
  error,
  composerCommand,
  onComposerCommandHandled,
}: ChatPanelProps) {
  const [inputValue, setInputValue] = useState("")
  const [attachments, setAttachments] = useState<File[]>([])
  const [isComposing, setIsComposing] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const scrollAreaRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    if (!scrollAreaRef.current) {
      return
    }

    const scrollContainer = scrollAreaRef.current.querySelector(
      "[data-radix-scroll-area-viewport]",
    )
    if (!(scrollContainer instanceof HTMLDivElement)) {
      return
    }

    const distanceToBottom =
      scrollContainer.scrollHeight -
      (scrollContainer.scrollTop + scrollContainer.clientHeight)

    if (distanceToBottom < 120) {
      scrollContainer.scrollTop = scrollContainer.scrollHeight
    }
  }, [messages])

  useEffect(() => {
    if (!composerCommand) {
      return
    }

    if (composerCommand.type === "upload") {
      fileInputRef.current?.click()
    }

    if (composerCommand.type === "focus") {
      textareaRef.current?.focus()
    }

    onComposerCommandHandled?.()
  }, [composerCommand, onComposerCommandHandled])

  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items
    if (!items) {
      return
    }

    const newFiles: File[] = []
    for (const item of Array.from(items)) {
      if (item.kind !== "file") {
        continue
      }
      const file = item.getAsFile()
      if (file) {
        newFiles.push(file)
      }
    }

    if (!newFiles.length) {
      return
    }

    e.preventDefault()
    setAttachments((prev) => [...prev, ...newFiles])
  }

  const handleSend = () => {
    if ((inputValue.trim() || attachments.length > 0) && !isSending && !isUploading) {
      onSendMessage(inputValue, attachments)
      setInputValue("")
      setAttachments([])
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
    const selectedFiles = Array.from(e.target.files || [])
    if (selectedFiles.length > 0) {
      setAttachments(prev => [...prev, ...selectedFiles])
      e.target.value = "" // Reset input
    }
  }

  const removeAttachment = (index: number) => {
    setAttachments(prev => prev.filter((_, i) => i !== index))
  }

  const handleUploadClick = () => {
    fileInputRef.current?.click()
  }

  const isDisabled = isSending || isUploading
  const isSendDisabled = (!inputValue.trim() && attachments.length === 0) || isDisabled
  const displayName = userName.trim() || "User"
  const fallbackInitials = displayName
    .split(/[\s_-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("") || "U"

  const renderAttachment = (attachment: ChatAttachment) => {
    const preview = attachment.kind === "image" && attachment.preview_url

    return (
      <div
        key={attachment.id}
        className="w-full max-w-[220px] min-w-0 overflow-hidden rounded-2xl border border-border bg-background/80 shadow-sm"
      >
        <div className="aspect-[4/3] border-b border-border bg-muted/30 p-2">
          {preview ? (
            // Blob URL previews are generated locally and are not suitable for next/image optimization.
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={attachment.preview_url ?? undefined}
              alt={attachment.name}
              className="h-full w-full rounded-xl object-cover"
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center rounded-xl bg-muted/40">
              {attachment.kind === "pdf" ? (
                <FileText className="h-9 w-9 text-rose-500" />
              ) : attachment.kind === "image" ? (
                <ImageIcon className="h-9 w-9 text-sky-500" />
              ) : (
                <FileIcon className="h-9 w-9 text-muted-foreground" />
              )}
            </div>
          )}
        </div>
        <div className="space-y-1 px-3 py-2">
          <div className="truncate text-xs font-medium text-foreground">{attachment.name}</div>
          <div className="text-[11px] text-muted-foreground">
            {attachment.kind === "pdf" ? "PDF" : attachment.kind === "image" ? "图片" : "文件"}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="relative flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-card shadow-sm md:rounded-xl md:border md:border-border">
      {/* Loading overlay */}
      {isLoading && (
        <div className="absolute inset-0 z-10 flex items-center justify-center rounded-xl bg-background/80">
          <div className="flex flex-col items-center gap-3">
            <Spinner className="h-8 w-8" />
            <span className="text-sm text-muted-foreground">正在加载...</span>
          </div>
        </div>
      )}

      {/* Error banner */}
      {error && (
        <div className="flex items-center gap-2 border-b border-destructive/20 bg-destructive/10 px-4 py-3">
          <AlertCircle className="h-4 w-4 flex-shrink-0 text-destructive" />
          <span className="text-sm text-destructive">{error}</span>
        </div>
      )}

      {/* Chat messages */}
      <ScrollArea className="min-h-0 flex-1 px-3 py-4 md:p-6" ref={scrollAreaRef}>
        <div className="mx-auto max-w-3xl space-y-6">
          {messages.map((message) => (
            <div
              key={message.id}
              className={cn(
                "flex min-w-0 gap-2 md:gap-3",
                message.role === "user" ? "flex-row-reverse" : "flex-row"
              )}
            >
              {/* Avatar */}
              {message.role !== "system" && (
                <Avatar className="h-8 w-8 shrink-0 md:h-9 md:w-9">
                  {message.role === "officer" ? (
                    <>
                      <AvatarImage
                        src="https://images.unsplash.com/photo-1573496359142-b8d87734a5a2?w=36&h=36&fit=crop&crop=face"
                        alt="签证官"
                      />
                      <AvatarFallback className="bg-muted text-xs text-muted-foreground">
                        VO
                      </AvatarFallback>
                    </>
                  ) : (
                    <>
                      <AvatarImage
                        src={userAvatarUrl}
                        alt={`${displayName} 的头像`}
                      />
                      <AvatarFallback className="bg-primary text-xs text-primary-foreground">
                        {fallbackInitials}
                      </AvatarFallback>
                    </>
                  )}
                </Avatar>
              )}

              {/* Message content */}
              <div
                className={cn(
                  "flex min-w-0 max-w-[90%] flex-col md:max-w-[75%]",
                  message.role === "user" ? "items-end" : "items-start",
                  message.role === "system" && "mx-auto max-w-full items-center"
                )}
              >
                {message.role !== "system" && (
                  <div className="mb-1 flex items-center gap-2">
                    <span className="text-xs font-medium text-foreground md:text-sm">
                      {message.role === "officer" ? "签证官" : displayName}
                    </span>
                    <span className="text-[10px] text-muted-foreground md:text-xs">
                      {message.timestamp}
                    </span>
                  </div>
                )}
                {message.content ? (
                  <div
                    className={cn(
                      "whitespace-pre-wrap break-words rounded-2xl px-4 py-2.5 text-sm leading-relaxed shadow-sm md:py-3",
                      message.role === "user"
                        ? message.status === "error"
                          ? "rounded-br-md border border-destructive/20 bg-destructive/10 text-destructive"
                          : "rounded-br-md bg-primary text-primary-foreground"
                        : message.role === "system"
                          ? "rounded-xl border border-border bg-muted/50 text-center italic text-muted-foreground"
                          : "rounded-bl-md border border-border bg-muted/80 text-foreground"
                    )}
                  >
                    {message.content}
                  </div>
                ) : null}
                {message.attachments?.length ? (
                  <div
                    className={cn(
                      "mt-2 flex flex-wrap gap-2",
                      message.role === "user" ? "justify-end" : "justify-start",
                    )}
                  >
                    {message.attachments.map(renderAttachment)}
                  </div>
                ) : null}
                {message.role === "user" && message.status === "error" && (
                  <div className="mt-1 flex items-center gap-1 text-xs text-destructive">
                    <AlertCircle className="h-3 w-3" />
                    <span>发送失败</span>
                  </div>
                )}
              </div>
            </div>
          ))}

          {/* Typing indicator */}
          {(isSending || isUploading) && (
            <div className="flex gap-2 md:gap-3">
              <Avatar className="h-8 w-8 shrink-0 md:h-9 md:w-9">
                <AvatarImage
                  src="https://images.unsplash.com/photo-1573496359142-b8d87734a5a2?w=36&h=36&fit=crop&crop=face"
                  alt="签证官"
                />
                <AvatarFallback className="bg-muted text-xs text-muted-foreground">
                  VO
                </AvatarFallback>
              </Avatar>
              <div className="flex flex-col items-start">
                <div className="mb-1 flex items-center gap-2">
                  <span className="text-xs font-medium text-foreground md:text-sm">签证官</span>
                </div>
                <div className="rounded-2xl rounded-bl-md border border-border bg-muted/80 px-4 py-3">
                  <div className="flex items-center gap-1">
                    {isUploading ? (
                      <span className="flex items-center gap-2 text-xs text-muted-foreground">
                        <Spinner className="h-3 w-3" />
                        正在处理上传材料...
                      </span>
                    ) : (
                      <>
                        <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/50" style={{ animationDelay: "0ms" }} />
                        <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/50" style={{ animationDelay: "150ms" }} />
                        <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/50" style={{ animationDelay: "300ms" }} />
                      </>
                    )}
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
        multiple
        className="hidden"
        onChange={handleFileSelect}
        accept="image/*,.pdf"
      />

      {/* Input area */}
      <div className="shrink-0 border-t border-border bg-card px-3 py-3 md:px-6 md:py-4">
        <div className="mx-auto max-w-3xl space-y-3">
          {/* Attachments list */}
          {attachments.length > 0 && (
            <div className="max-h-24 overflow-y-auto">
              <div className="flex flex-wrap gap-2">
              {attachments.map((file, i) => (
                <div
                  key={`${file.name}-${i}`}
                  className="group flex animate-in fade-in zoom-in items-center gap-2 rounded-lg border border-border bg-muted px-2 py-1.5 duration-200"
                >
                  {file.type.startsWith('image/') ? (
                    <ImageIcon className="h-3.5 w-3.5 text-blue-500" />
                  ) : (
                    <FileIcon className="h-3.5 w-3.5 text-muted-foreground" />
                  )}
                  <span className="max-w-[120px] truncate text-xs font-medium" title={file.name}>
                    {file.name}
                  </span>
                  <button
                    onClick={() => removeAttachment(i)}
                    className="rounded-full p-0.5 transition-colors hover:bg-muted-foreground/20"
                  >
                    <X className="h-3 w-3 text-muted-foreground" />
                  </button>
                </div>
              ))}
              </div>
            </div>
          )}

          <div className="flex items-end gap-2 md:gap-3">
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={handleUploadClick}
              disabled={isDisabled}
              className="mb-1 shrink-0"
            >
              <Paperclip className="h-4 w-4 text-muted-foreground" />
            </Button>
            <div className="relative min-w-0 flex-1">
              <textarea
                ref={textareaRef}
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={handleKeyDown}
                onCompositionStart={() => setIsComposing(true)}
                onCompositionEnd={() => setIsComposing(false)}
                onPaste={handlePaste}
                placeholder={attachments.length > 0 ? "添加关于附件的说明..." : "输入你的回答..."}
                rows={1}
                disabled={isDisabled}
                className="max-h-32 min-h-[44px] w-full resize-none overflow-y-auto rounded-xl border border-border bg-muted/30 px-3 py-3 pr-12 text-sm transition-all placeholder:text-muted-foreground focus:border-primary/40 focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:opacity-50 md:px-4 md:pr-14"
                onInput={(e) => {
                  const target = e.target as HTMLTextAreaElement
                  target.style.height = 'auto'
                  target.style.height = `${Math.min(target.scrollHeight, 200)}px`
                }}
              />
              <Button
                size="icon-sm"
                onClick={handleSend}
                disabled={isSendDisabled}
                className="absolute bottom-1.5 right-1.5 h-8 w-8 rounded-lg md:right-2"
              >
                <Send className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </div>
        <p className="mx-auto mt-3 max-w-3xl text-center text-[10px] text-muted-foreground md:text-xs">
          回答仅用于模拟练习，不会被保存或提交给任何机构。
        </p>
      </div>
    </div>
  )
}
