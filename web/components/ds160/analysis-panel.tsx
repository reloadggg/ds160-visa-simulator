"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Spinner } from "@/components/ui/spinner"
import { User, FileText, Zap, ArrowRight, AlertCircle } from "lucide-react"
import { cn } from "@/lib/utils"
import type { UserReport, AllowedAction } from "@/lib/api/types"

interface AnalysisPanelProps {
  report: UserReport | null
  isLoading?: boolean
  error?: string | null
  mode?: "simulation" | "coach"
  onViewDetails: () => void
  onViewAllMaterials: () => void
  onActionClick: (action: AllowedAction) => void
}

const riskLevelConfig = {
  none: { label: "无明显风险", color: "bg-slate-400", textColor: "text-slate-600" },
  low: { label: "低风险", color: "bg-emerald-500", textColor: "text-emerald-600" },
  medium: { label: "中风险", color: "bg-amber-500", textColor: "text-amber-600" },
  high: { label: "高风险", color: "bg-red-500", textColor: "text-red-600" },
}

const priorityConfig = {
  high: { color: "bg-red-500" },
  medium: { color: "bg-amber-500" },
  low: { color: "bg-gray-400" },
}

export function AnalysisPanel({
  report,
  isLoading = false,
  error,
  mode = "simulation",
  onViewDetails,
  onViewAllMaterials,
  onActionClick,
}: AnalysisPanelProps) {
  const riskConfig = report ? riskLevelConfig[report.risk_level] : null
  const containerWidth = mode === "coach" ? "w-96" : "w-80"

  if (isLoading) {
    return (
      <div className={cn(containerWidth, "flex flex-col items-center justify-center gap-4 p-4 bg-background border-l border-border")}>
        <Spinner className="w-8 h-8" />
        <span className="text-sm text-muted-foreground">加载分析数据...</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className={cn(containerWidth, "flex flex-col items-center justify-center gap-4 p-4 bg-background border-l border-border")}>
        <AlertCircle className="w-12 h-12 text-destructive" />
        <span className="text-sm text-destructive text-center">{error}</span>
      </div>
    )
  }

  if (!report) {
    return (
      <div className={cn(containerWidth, "flex flex-col items-center justify-center gap-4 p-4 bg-background border-l border-border")}>
        <span className="text-sm text-muted-foreground">暂无分析数据</span>
      </div>
    )
  }

  return (
    <div className={cn(containerWidth, "flex flex-col gap-4 p-4 bg-background border-l border-border overflow-y-auto")}>
      {/* Current Status Card */}
      <Card className="py-4 shadow-sm">
        <CardHeader className="pb-3 px-4">
          <CardTitle className="flex items-center gap-3 text-base font-semibold">
            <div className="w-9 h-9 rounded-full bg-purple-100 flex items-center justify-center flex-shrink-0">
              <User className="w-4 h-4 text-purple-600" />
            </div>
            当前状态
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 px-4">
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">风险等级：</span>
            <div className="flex items-center gap-2">
              <div className={cn("w-2.5 h-2.5 rounded-full", riskConfig?.color)} />
              <span className={cn("text-sm font-medium", riskConfig?.textColor)}>
                {report.risk_level_label || riskConfig?.label}
              </span>
            </div>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">当前阶段：</span>
            <span className="text-sm font-medium text-foreground">
              {report.interview_status_label}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">当前结论：</span>
            <span className="text-sm font-medium text-foreground text-right max-w-[140px] truncate">
              {report.outcome_label}
            </span>
          </div>
          <div className="rounded-xl border border-border bg-muted/40 px-3 py-2">
            <div className="text-xs text-muted-foreground mb-1">当前摘要</div>
            <p className="text-sm leading-relaxed text-foreground">{report.summary}</p>
          </div>
          {report.current_key_proof_label && (
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">关键证明：</span>
              <span className="text-sm font-medium text-foreground text-right max-w-[140px] truncate">
                {report.current_key_proof_label}
              </span>
            </div>
          )}
          <button
            onClick={onViewDetails}
            className="flex items-center gap-1 text-sm font-medium text-primary hover:text-primary/80 transition-colors pt-1"
          >
            查看详情
            <ArrowRight className="w-4 h-4" />
          </button>
        </CardContent>
      </Card>

      {/* Missing Materials Card */}
      <Card className="py-4 shadow-sm">
        <CardHeader className="pb-3 px-4">
          <CardTitle className="flex items-center gap-3 text-base font-semibold">
            <div className="w-9 h-9 rounded-full bg-blue-100 flex items-center justify-center flex-shrink-0">
              <FileText className="w-4 h-4 text-primary" />
            </div>
            缺失材料
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2.5 px-4">
          {report.missing_evidence.length > 0 ? (
            <>
              {report.missing_evidence.slice(0, 3).map((material, index) => (
                <div key={material.id || index} className="flex items-center gap-2.5">
                  <div
                    className={cn(
                      "w-2 h-2 rounded-full shrink-0",
                      priorityConfig[material.priority]?.color || "bg-gray-400"
                    )}
                  />
                  <span className="text-sm text-foreground">{material.name}</span>
                </div>
              ))}
              {report.missing_evidence.length > 3 && (
                <span className="text-xs text-muted-foreground">
                  还有 {report.missing_evidence.length - 3} 项...
                </span>
              )}
            </>
          ) : (
            <span className="text-sm text-muted-foreground">暂无缺失材料</span>
          )}
          <button
            onClick={onViewAllMaterials}
            className="flex items-center gap-1 text-sm font-medium text-primary hover:text-primary/80 transition-colors pt-1"
          >
            查看全部缺失材料
            <ArrowRight className="w-4 h-4" />
          </button>
        </CardContent>
      </Card>

      {/* Suggested Action Card */}
      <Card className="py-4 shadow-sm">
        <CardHeader className="pb-3 px-4">
          <CardTitle className="flex items-center gap-3 text-base font-semibold">
            <div className="w-9 h-9 rounded-full bg-green-100 flex items-center justify-center flex-shrink-0">
              <Zap className="w-4 h-4 text-green-600" />
            </div>
            建议动作
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 px-4">
          {report.allowed_next_actions.length > 0 ? (
            <>
              <h4 className="text-sm font-semibold text-foreground">
                {report.allowed_next_actions[0].title}
              </h4>
              <p className="text-sm text-muted-foreground leading-relaxed">
                {report.allowed_next_actions[0].description}
              </p>
              <button
                onClick={() => onActionClick(report.allowed_next_actions[0])}
                className="flex items-center gap-1 text-sm font-medium text-primary hover:text-primary/80 transition-colors"
              >
                {report.allowed_next_actions[0].cta_text}
                <ArrowRight className="w-4 h-4" />
              </button>
            </>
          ) : (
            <span className="text-sm text-muted-foreground">暂无建议动作</span>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
