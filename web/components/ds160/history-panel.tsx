"use client"

import { useMemo, useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import { MessageSquare, Clock3, FileText, ImageIcon, RotateCcw } from "lucide-react"
import type { SessionHistoryEntry } from "@/lib/api/types"
import { cn } from "@/lib/utils"

interface HistoryPanelProps {
  entries: SessionHistoryEntry[]
  onRestore?: (entry: SessionHistoryEntry) => void
}

const STATUS_LABELS: Record<SessionHistoryEntry["status"], string> = {
  active: "进行中",
  completed: "已结束",
  abandoned: "已重置",
}

const STATUS_STYLES: Record<SessionHistoryEntry["status"], string> = {
  active: "border-emerald-200 bg-emerald-50 text-emerald-700",
  completed: "border-blue-200 bg-blue-50 text-blue-700",
  abandoned: "border-slate-200 bg-slate-100 text-slate-700",
}

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

export function HistoryPanel({ entries, onRestore }: HistoryPanelProps) {
  const [selectedId, setSelectedId] = useState<string | null>(entries[0]?.id ?? null)
  const resolvedSelectedId =
    selectedId && entries.some((entry) => entry.id === selectedId)
      ? selectedId
      : entries[0]?.id ?? null

  const selectedEntry = useMemo(
    () => entries.find((entry) => entry.id === resolvedSelectedId) ?? null,
    [entries, resolvedSelectedId],
  )

  if (!entries.length) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <Card className="max-w-xl border-dashed bg-muted/20">
          <CardContent className="flex flex-col items-center gap-3 px-8 py-10 text-center">
            <Clock3 className="h-10 w-10 text-muted-foreground" />
            <div>
              <div className="text-base font-semibold text-foreground">还没有历史会话</div>
              <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
                开始一轮新的签证模拟后，这里会保存本浏览器内的会话摘要、消息记录和已上传材料。
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="grid h-full min-h-0 grid-cols-1 gap-4 p-3 xl:grid-cols-[320px_minmax(0,1fr)] xl:p-4">
      <Card className="flex flex-col min-w-0 py-4">
        <CardHeader className="px-4 pb-3">
          <CardTitle className="text-base">历史会话</CardTitle>
        </CardHeader>
        <CardContent className="px-3 flex-1 min-h-0">
          <ScrollArea className="h-[220px] pr-2 xl:h-full">
            <div className="space-y-2">
              {entries.map((entry) => (
                <button
                  key={entry.id}
                  type="button"
                  onClick={() => setSelectedId(entry.id)}
                  className={cn(
                    "w-full rounded-xl border px-3 py-3 text-left transition-colors",
                    resolvedSelectedId === entry.id
                      ? "border-primary bg-primary/5"
                      : "border-border bg-background hover:bg-muted/40",
                  )}
                >
                  <div className="flex min-w-0 items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-medium text-foreground" title={entry.title}>{entry.title}</div>
                      <div className="mt-1 break-words text-xs text-muted-foreground">
                        {formatDateTime(entry.updated_at)}
                      </div>
                    </div>
                    <Badge
                      variant="outline"
                      className={cn("shrink-0", STATUS_STYLES[entry.status])}
                    >
                      {STATUS_LABELS[entry.status]}
                    </Badge>
                  </div>
                  <p className="mt-2 line-clamp-2 text-sm leading-relaxed text-muted-foreground">
                    {entry.summary}
                  </p>
                  <div className="mt-3 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                    <span>{entry.visa_type}</span>
                    <span>{entry.message_count} 条消息</span>
                    <span>{entry.materials.length} 份材料</span>
                  </div>
                </button>
              ))}
            </div>
          </ScrollArea>
        </CardContent>
      </Card>

      <Card className="flex flex-col min-w-0 py-4">
        <CardHeader className="flex min-w-0 flex-row items-center justify-between gap-3 px-5 pb-3">
          <CardTitle className="min-w-0 truncate text-base" title={selectedEntry?.title ?? "会话详情"}>
            {selectedEntry?.title ?? "会话详情"}
          </CardTitle>
          {selectedEntry && onRestore && (
            <Button
              variant="outline"
              size="sm"
              className="h-8 gap-1.5 rounded-lg border-primary/20 text-primary hover:bg-primary/5"
              onClick={() => onRestore(selectedEntry)}
            >
              <RotateCcw className="h-3.5 w-3.5" />
              恢复此会话
            </Button>
          )}
        </CardHeader>
        <ScrollArea className="flex-1">
          <CardContent className="grid min-h-0 gap-4 px-5 pb-8 lg:grid-cols-[1.1fr_0.9fr]">
            {selectedEntry ? (
              <>
                <div className="space-y-4">
                  <div className="rounded-2xl border border-border bg-muted/30 p-4">
                    <div className="text-xs text-muted-foreground">会话摘要</div>
                    <p className="mt-2 text-sm leading-7 text-foreground">
                      {selectedEntry.summary}
                    </p>
                  </div>

                  <div className="grid gap-3 sm:grid-cols-2">
                    <div className="rounded-xl border border-border bg-background p-4">
                      <div className="text-xs text-muted-foreground">风险等级</div>
                      <div className="mt-2 text-sm font-medium text-foreground">
                        {selectedEntry.report?.risk_level_label ?? "暂无"}
                      </div>
                    </div>
                    <div className="rounded-xl border border-border bg-background p-4">
                      <div className="text-xs text-muted-foreground">当前结论</div>
                      <div className="mt-2 text-sm font-medium text-foreground">
                        {selectedEntry.report?.outcome_label ?? "暂无"}
                      </div>
                    </div>
                  </div>

                  <div className="rounded-2xl border border-border bg-background p-4">
                    <div className="mb-3 flex items-center gap-2 text-sm font-medium text-foreground">
                      <MessageSquare className="h-4 w-4 text-primary" />
                      对话摘录
                    </div>
                    <ScrollArea className="h-[360px] pr-3">
                      <div className="space-y-3">
                        {selectedEntry.messages.length ? (
                          selectedEntry.messages.map((message) => (
                            <div key={message.id} className="min-w-0 rounded-xl border border-border bg-muted/20 p-3">
                              <div className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
                                <span>
                                  {message.role === "officer"
                                    ? "签证官"
                                    : message.role === "user"
                                      ? "用户"
                                      : "系统"}
                                </span>
                                <span>{message.timestamp}</span>
                              </div>
                              {message.content ? (
                                <p className="break-words text-sm leading-6 text-foreground">
                                  {message.content}
                                </p>
                              ) : (
                                <p className="text-sm text-muted-foreground">仅包含附件</p>
                              )}
                              {message.attachments?.length ? (
                                <div className="mt-2 flex flex-wrap gap-2">
                                  {message.attachments.map((attachment) => (
                                    <div
                                      key={attachment.id}
                                      className="overflow-hidden rounded-lg border border-border bg-background"
                                    >
                                      {attachment.kind === "image" && attachment.preview_url ? (
                                        // Data URL previews are persisted locally and cannot use next/image optimization.
                                        // eslint-disable-next-line @next/next/no-img-element
                                        <img
                                          src={attachment.preview_url}
                                          alt={attachment.name}
                                          className="h-20 w-24 object-cover"
                                        />
                                      ) : (
                                        <div className="flex h-20 w-24 flex-col items-center justify-center gap-1 bg-muted/40 px-2 text-center">
                                          <ImageIcon className="h-5 w-5 text-muted-foreground" />
                                          <span className="line-clamp-2 text-[10px] text-muted-foreground">
                                            {attachment.name}
                                          </span>
                                        </div>
                                      )}
                                    </div>
                                  ))}
                                </div>
                              ) : null}
                            </div>
                          ))
                        ) : (
                          <div className="text-sm text-muted-foreground">暂无消息记录</div>
                        )}
                      </div>
                    </ScrollArea>
                  </div>
                </div>

                <div className="space-y-4">
                  <div className="rounded-2xl border border-border bg-background p-4">
                    <div className="mb-3 flex items-center gap-2 text-sm font-medium text-foreground">
                      <FileText className="h-4 w-4 text-primary" />
                      材料概览
                    </div>
                    {selectedEntry.materials.length ? (
                      <div className="space-y-3">
                        {selectedEntry.materials.map((material) => (
                          <div key={material.id} className="min-w-0 rounded-xl border border-border bg-muted/20 p-3">
                            <div className="truncate text-sm font-medium text-foreground" title={material.name}>
                              {material.name}
                            </div>
                            <div className="mt-1 break-words text-xs text-muted-foreground">
                              {material.document_type_label ?? material.status_label}
                            </div>
                            {material.feedback_message ? (
                              <p className="mt-2 line-clamp-3 break-words text-sm leading-6 text-muted-foreground">
                                {material.feedback_message}
                              </p>
                            ) : null}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="text-sm text-muted-foreground">该会话没有上传材料。</div>
                    )}
                  </div>

                  <div className="rounded-2xl border border-border bg-background p-4">
                    <div className="text-xs text-muted-foreground">关键问题</div>
                    <p className="mt-2 text-sm leading-7 text-foreground">
                      {selectedEntry.report?.current_key_question ?? "暂无"}
                    </p>
                  </div>
                </div>
              </>
            ) : null}
          </CardContent>
        </ScrollArea>
      </Card>
    </div>
  )
}
