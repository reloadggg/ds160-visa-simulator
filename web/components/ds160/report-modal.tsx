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
import { Spinner } from "@/components/ui/spinner"
import { cn } from "@/lib/utils"
import type { UserReport, InternalReport } from "@/lib/api/types"
import { User, FileText, Zap, AlertCircle } from "lucide-react"

interface ReportModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  userReport: UserReport | null
  internalReport: InternalReport | null
  isLoading: boolean
  error?: string | null
}

const riskLevelConfig = {
  none: { label: "无明显风险", color: "bg-slate-400", textColor: "text-slate-600" },
  low: { label: "低风险", color: "bg-emerald-500", textColor: "text-emerald-600" },
  medium: { label: "中风险", color: "bg-amber-500", textColor: "text-amber-600" },
  high: { label: "高风险", color: "bg-red-500", textColor: "text-red-600" },
}

export function ReportModal({
  open,
  onOpenChange,
  userReport,
  internalReport,
  isLoading,
  error,
}: ReportModalProps) {
  const riskConfig = userReport ? riskLevelConfig[userReport.risk_level] : null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh]">
        <DialogHeader>
          <DialogTitle>面签报告</DialogTitle>
        </DialogHeader>

        <Tabs defaultValue="user" className="w-full">
          <TabsList className="w-full">
            <TabsTrigger value="user" className="flex-1">
              用户报告
            </TabsTrigger>
            <TabsTrigger value="internal" className="flex-1">
              调试数据
            </TabsTrigger>
          </TabsList>

          <TabsContent value="user" className="mt-4">
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
                        <span className="text-sm text-muted-foreground">关键问题</span>
                        <span className="text-sm font-medium text-right max-w-[200px]">
                          {userReport.current_key_question}
                        </span>
                      </div>
                      {userReport.current_key_proof_label && (
                        <div className="flex items-center justify-between">
                          <span className="text-sm text-muted-foreground">关键证明</span>
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

                  {/* Missing Evidence Card */}
                  <Card>
                    <CardHeader className="pb-3">
                      <CardTitle className="flex items-center gap-2 text-base">
                        <div className="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center">
                          <FileText className="w-4 h-4 text-primary" />
                        </div>
                        缺失材料
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
                        <p className="text-sm text-muted-foreground">暂无缺失材料</p>
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

          <TabsContent value="internal" className="mt-4">
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
              <ScrollArea className="h-[400px]">
                <pre className="text-xs bg-muted p-4 rounded-lg overflow-auto">
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
