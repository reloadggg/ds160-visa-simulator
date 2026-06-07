"use client"

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import { cn } from "@/lib/utils"
import type { UserReport, InternalReport, InterviewReviewResponse } from "@/lib/api/types"
import { User, FileText, Zap, AlertCircle, ClipboardCheck } from "lucide-react"

interface ReportModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  userReport: UserReport | null
  internalReport: InternalReport | null
  interviewReview?: InterviewReviewResponse | null
  isLoading: boolean
  isGeneratingReview?: boolean
  error?: string | null
  onGenerateReview?: () => void
  onExportReviewImage?: () => void
}

function ReviewListCard({
  title,
  items,
  emptyText = "暂无。",
}: {
  title: string
  items: string[]
  emptyText?: string
}) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {items.length > 0 ? (
          <ul className="space-y-2">
            {items.map((item, index) => (
              <li key={index} className="text-sm leading-relaxed text-foreground">
                {item}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">{emptyText}</p>
        )}
      </CardContent>
    </Card>
  )
}

const riskLevelConfig = {
  none: { label: "无明显风险", color: "bg-slate-400", textColor: "text-slate-600 dark:text-slate-200" },
  low: { label: "低风险", color: "bg-emerald-500", textColor: "text-emerald-600 dark:text-emerald-200" },
  medium: { label: "中风险", color: "bg-amber-500", textColor: "text-amber-600 dark:text-amber-200" },
  high: { label: "高风险", color: "bg-red-500", textColor: "text-red-600 dark:text-red-200" },
}

const interviewResultConfig: Record<string, { color: string; textColor: string; bgColor: string }> = {
  passed: {
    color: "bg-emerald-500",
    textColor: "text-emerald-700 dark:text-emerald-100",
    bgColor: "bg-emerald-50 border-emerald-200 dark:border-emerald-300/25 dark:bg-emerald-300/10",
  },
  refused: {
    color: "bg-red-500",
    textColor: "text-red-700 dark:text-red-100",
    bgColor: "bg-red-50 border-red-200 dark:border-red-300/25 dark:bg-red-300/10",
  },
  not_passed: {
    color: "bg-amber-500",
    textColor: "text-amber-700 dark:text-amber-100",
    bgColor: "bg-amber-50 border-amber-200 dark:border-amber-300/25 dark:bg-amber-300/10",
  },
  in_progress: {
    color: "bg-sky-500",
    textColor: "text-sky-700 dark:text-sky-100",
    bgColor: "bg-sky-50 border-sky-200 dark:border-sky-300/25 dark:bg-sky-300/10",
  },
}

export function ReportModal({
  open,
  onOpenChange,
  userReport,
  internalReport,
  interviewReview,
  isLoading,
  isGeneratingReview = false,
  error,
  onGenerateReview,
  onExportReviewImage,
}: ReportModalProps) {
  const riskConfig = userReport ? riskLevelConfig[userReport.risk_level] : null
  const resultConfig = userReport
    ? interviewResultConfig[userReport.interview_result] ?? interviewResultConfig.in_progress
    : null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[80vh] max-w-3xl flex-col overflow-hidden">
        <DialogHeader>
          <div className="flex items-center justify-between gap-3">
            <DialogTitle>面签报告</DialogTitle>
            <div className="flex shrink-0 items-center gap-2">
              {onExportReviewImage && interviewReview ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={onExportReviewImage}
                  disabled={isLoading || isGeneratingReview}
                  className="gap-2"
                >
                  <FileText className="h-4 w-4" />
                  导出复盘图
                </Button>
              ) : null}
              {onGenerateReview ? (
                <Button
                  size="sm"
                  onClick={onGenerateReview}
                  disabled={isLoading || isGeneratingReview}
                  className="gap-2"
                >
                  {isGeneratingReview ? (
                    <Spinner className="h-4 w-4" />
                  ) : (
                    <ClipboardCheck className="h-4 w-4" />
                  )}
                  {interviewReview ? "重新生成复盘" : "生成复盘"}
                </Button>
              ) : null}
            </div>
          </div>
        </DialogHeader>

        <Tabs defaultValue="user" className="flex min-h-0 w-full flex-1 flex-col">
          <TabsList className="w-full">
            <TabsTrigger value="user" className="flex-1">
              用户报告
            </TabsTrigger>
            <TabsTrigger value="review" className="flex-1">
              复盘
            </TabsTrigger>
            <TabsTrigger value="internal" className="flex-1">
              调试数据
            </TabsTrigger>
          </TabsList>

          <TabsContent value="user" className="mt-4 min-h-0 flex-1">
            {isLoading ? (
              <div className="flex items-center justify-center py-12">
                <Spinner className="w-8 h-8" />
              </div>
            ) : error ? (
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <AlertCircle className="w-12 h-12 text-destructive mb-4" />
                <p className="text-destructive">{error}</p>
              </div>
            ) : userReport ? (
              <ScrollArea className="h-[400px] pr-4">
                <div className="space-y-4">
                  {/* Status Card */}
                  <Card>
                    <CardHeader className="pb-3">
                      <CardTitle className="flex items-center gap-2 text-base">
                        <div className="w-8 h-8 rounded-full bg-purple-100 flex items-center justify-center">
                          <User className="w-4 h-4 text-purple-600" />
                        </div>
                        当前状态
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-2">
                      <div className={cn("rounded-xl border px-3 py-2", resultConfig?.bgColor)}>
                        <div className="mb-1 text-xs text-slate-600 dark:text-slate-300">面签结论</div>
                        <div className="flex items-center gap-2">
                          <div className={cn("h-2.5 w-2.5 shrink-0 rounded-full", resultConfig?.color)} />
                          <span className={cn("text-base font-semibold", resultConfig?.textColor)}>
                            {userReport.interview_result_label}
                          </span>
                        </div>
                        <p className="mt-1 text-xs leading-relaxed text-slate-600 dark:text-slate-300">
                          {userReport.interview_result_reason}
                        </p>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-sm text-muted-foreground">风险等级</span>
                        <div className="flex items-center gap-2">
                          <div className={cn("w-2.5 h-2.5 rounded-full", riskConfig?.color)} />
                          <span className={cn("text-sm font-medium", riskConfig?.textColor)}>
                            {userReport.risk_level_label || riskConfig?.label}
                          </span>
                        </div>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-sm text-muted-foreground">面试状态</span>
                        <span className="text-sm font-medium">{userReport.interview_status_label}</span>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-sm text-muted-foreground">当前结论</span>
                        <span className="text-sm font-medium text-right max-w-[200px]">
                          {userReport.outcome_label}
                        </span>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-sm text-muted-foreground">当前问题</span>
                        <span className="text-sm font-medium text-right max-w-[200px]">
                          {userReport.current_key_question}
                        </span>
                      </div>
                      {userReport.current_key_proof_label && (
                        <div className="flex items-center justify-between">
                          <span className="text-sm text-muted-foreground">待核实点</span>
                          <span className="text-sm font-medium text-right max-w-[200px]">
                            {userReport.current_key_proof_label}
                          </span>
                        </div>
                      )}
                      <div className="rounded-xl border border-border bg-muted/40 px-3 py-2">
                        <div className="text-xs text-muted-foreground mb-1">摘要</div>
                        <p className="text-sm leading-relaxed">{userReport.summary}</p>
                      </div>
                    </CardContent>
                  </Card>

                  {/* Weak Evidence Card */}
                  <Card>
                    <CardHeader className="pb-3">
                      <CardTitle className="flex items-center gap-2 text-base">
                        <div className="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center">
                          <FileText className="w-4 h-4 text-primary" />
                        </div>
                        待核实事实
                      </CardTitle>
                    </CardHeader>
                    <CardContent>
                      {userReport.missing_evidence.length > 0 ? (
                        <ul className="space-y-2">
                          {userReport.missing_evidence.map((item, index) => (
                            <li key={item.id || index} className="flex items-center gap-2">
                              <div
                                className={cn(
                                  "w-2 h-2 rounded-full",
                                  item.priority === "high"
                                    ? "bg-red-500"
                                    : item.priority === "medium"
                                      ? "bg-amber-500"
                                      : "bg-gray-400"
                                )}
                              />
                              <span className="text-sm">{item.name}</span>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="text-sm text-muted-foreground">暂无明确待核实事实</p>
                      )}
                    </CardContent>
                  </Card>

                  {/* Allowed Actions Card */}
                  <Card>
                    <CardHeader className="pb-3">
                      <CardTitle className="flex items-center gap-2 text-base">
                        <div className="w-8 h-8 rounded-full bg-green-100 flex items-center justify-center">
                          <Zap className="w-4 h-4 text-green-600" />
                        </div>
                        建议动作
                      </CardTitle>
                    </CardHeader>
                    <CardContent>
                      {userReport.allowed_next_actions.length > 0 ? (
                        <ul className="space-y-3">
                          {userReport.allowed_next_actions.map((action, index) => (
                            <li key={index} className="border-b border-border pb-3 last:border-0 last:pb-0">
                              <div className="font-medium text-sm">{action.title}</div>
                              <div className="text-sm text-muted-foreground mt-1">
                                {action.description}
                              </div>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="text-sm text-muted-foreground">暂无建议动作</p>
                      )}
                    </CardContent>
                  </Card>

                  <Card>
                    <CardHeader className="pb-3">
                      <CardTitle className="text-base">补强建议</CardTitle>
                    </CardHeader>
                    <CardContent>
                      {userReport.recommended_improvements.length > 0 ? (
                        <ul className="space-y-2">
                          {userReport.recommended_improvements.map((item, index) => (
                            <li key={index} className="text-sm text-foreground leading-relaxed">
                              {item}
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="text-sm text-muted-foreground">暂无额外补强建议</p>
                      )}
                    </CardContent>
                  </Card>
                </div>
              </ScrollArea>
            ) : null}
          </TabsContent>

          <TabsContent value="review" className="mt-4 min-h-0 flex-1">
            {isGeneratingReview ? (
              <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
                <Spinner className="h-8 w-8" />
                <p className="text-sm text-muted-foreground">正在结合面签记录和调试数据生成复盘...</p>
              </div>
            ) : interviewReview ? (
              <ScrollArea className="h-[400px] pr-4">
                <div className="space-y-4">
                  <Card>
                    <CardHeader className="pb-3">
                      <CardTitle className="flex items-center gap-2 text-base">
                        <ClipboardCheck className="h-4 w-4 text-primary" />
                        {interviewReview.report.outcome}
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      <p className="text-sm leading-relaxed text-foreground">
                        {interviewReview.report.executive_summary}
                      </p>
                      <div className="rounded-xl border border-border bg-muted/40 px-3 py-2">
                        <div className="mb-1 text-xs text-muted-foreground">结果原因</div>
                        <p className="text-sm leading-relaxed">{interviewReview.report.outcome_reason}</p>
                      </div>
                    </CardContent>
                  </Card>

                  <ReviewListCard title="做得好的地方" items={interviewReview.report.strengths} />
                  <ReviewListCard title="拒签/风险原因" items={interviewReview.report.refusal_or_risk_reasons} emptyText="暂无明确拒签或高风险原因。" />
                  <ReviewListCard title="缺失或薄弱证据" items={interviewReview.report.missing_or_weak_evidence} />
                  <ReviewListCard title="回答表现问题" items={interviewReview.report.conversation_issues} />
                  <ReviewListCard title="材料复盘" items={interviewReview.report.document_findings} />
                  <ReviewListCard title="下一步补强计划" items={interviewReview.report.improvement_plan} />
                  <ReviewListCard title="下一轮练习重点" items={interviewReview.report.next_practice_focus} />
                </div>
              </ScrollArea>
            ) : (
              <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
                <ClipboardCheck className="h-12 w-12 text-muted-foreground" />
                <div>
                  <p className="font-medium text-foreground">还没有生成复盘</p>
                  <p className="mt-1 text-sm text-muted-foreground">点击右上角“生成复盘”，系统会结合调试数据和材料理解生成总结。</p>
                </div>
              </div>
            )}
          </TabsContent>

          <TabsContent value="internal" className="mt-4 min-h-0 flex-1">
            {isLoading ? (
              <div className="flex items-center justify-center py-12">
                <Spinner className="w-8 h-8" />
              </div>
            ) : error ? (
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <AlertCircle className="w-12 h-12 text-destructive mb-4" />
                <p className="text-destructive">{error}</p>
              </div>
            ) : internalReport ? (
              <ScrollArea className="h-[400px] rounded-lg border border-border bg-muted/40">
                <pre className="max-w-full whitespace-pre-wrap break-words p-4 font-mono text-xs leading-5 text-muted-foreground">
                  {JSON.stringify(internalReport, null, 2)}
                </pre>
              </ScrollArea>
            ) : null}
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}
