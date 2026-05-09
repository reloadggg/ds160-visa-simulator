"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Spinner } from "@/components/ui/spinner"
import { User, FileText, Zap, ArrowRight, AlertCircle, BrainCircuit } from "lucide-react"
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
  className?: string
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
  className,
}: AnalysisPanelProps) {
  const riskConfig = report ? riskLevelConfig[report.risk_level] : null
  const containerWidth = mode === "coach" ? "w-80 2xl:w-96" : "w-72 2xl:w-80"
  const primaryAction = report?.allowed_next_actions[0] ?? null
  const visibleMissingEvidence =
    mode === "coach" ? report?.missing_evidence ?? [] : (report?.missing_evidence ?? []).slice(0, 2)

  if (isLoading) {
    return (
      <div className={cn(containerWidth, "min-w-0 shrink-0 flex-col items-center justify-center gap-4 border-l border-border bg-background p-4", className)}>
        <Spinner className="w-8 h-8" />
        <span className="text-sm text-muted-foreground">加载分析数据...</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className={cn(containerWidth, "min-w-0 shrink-0 flex-col items-center justify-center gap-4 border-l border-border bg-background p-4", className)}>
        <AlertCircle className="w-12 h-12 text-destructive" />
        <span className="text-sm text-destructive text-center">{error}</span>
      </div>
    )
  }

  if (!report) {
    return (
      <div className={cn(containerWidth, "min-w-0 shrink-0 flex-col items-center justify-center gap-4 border-l border-border bg-background p-4", className)}>
        <span className="text-sm text-muted-foreground">暂无分析数据</span>
      </div>
    )
  }

  return (
    <div className={cn(containerWidth, "min-w-0 shrink-0 flex-col gap-3 overflow-y-auto border-l border-border bg-background p-3 2xl:gap-4 2xl:p-4", className)}>
      {/* Current Status Card */}
      <Card className="min-w-0 py-4 shadow-sm">
        <CardHeader className="pb-3 px-4">
          <CardTitle className="flex min-w-0 items-center gap-3 text-base font-semibold">
            <div className="w-9 h-9 rounded-full bg-purple-100 flex items-center justify-center flex-shrink-0">
              <User className="w-4 h-4 text-purple-600" />
            </div>
            <span className="truncate">当前状态</span>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 px-4">
          <div className="flex min-w-0 items-center justify-between gap-3">
            <span className="text-sm text-muted-foreground">风险等级：</span>
            <div className="flex items-center gap-2">
              <div className={cn("w-2.5 h-2.5 rounded-full", riskConfig?.color)} />
              <span className={cn("text-sm font-medium", riskConfig?.textColor)}>
                {report.risk_level_label || riskConfig?.label}
              </span>
            </div>
          </div>
          <div className="flex min-w-0 items-center justify-between gap-3">
            <span className="text-sm text-muted-foreground">当前阶段：</span>
            <span className="text-sm font-medium text-foreground">
              {report.interview_status_label}
            </span>
          </div>
          <div className="flex min-w-0 items-center justify-between gap-3">
            <span className="text-sm text-muted-foreground">当前结论：</span>
            <span className="min-w-0 max-w-[150px] truncate text-right text-sm font-medium text-foreground">
              {report.outcome_label}
            </span>
          </div>
          <div className="rounded-xl border border-border bg-muted/40 px-3 py-2">
            <div className="text-xs text-muted-foreground mb-1">当前摘要</div>
            <p className="line-clamp-4 break-words text-sm leading-relaxed text-foreground">{report.summary}</p>
          </div>
          {report.current_key_proof_label && (
            <div className="flex min-w-0 items-center justify-between gap-3">
              <span className="text-sm text-muted-foreground">关键证明：</span>
              <span className="min-w-0 max-w-[150px] truncate text-right text-sm font-medium text-foreground">
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
      <Card className="min-w-0 py-4 shadow-sm">
        <CardHeader className="pb-3 px-4">
          <CardTitle className="flex min-w-0 items-center gap-3 text-base font-semibold">
            <div className="w-9 h-9 rounded-full bg-blue-100 flex items-center justify-center flex-shrink-0">
              <FileText className="w-4 h-4 text-primary" />
            </div>
            <span className="truncate">缺失材料</span>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2.5 px-4">
          {visibleMissingEvidence.length > 0 ? (
            <>
              {visibleMissingEvidence.map((material, index) => (
                <div key={material.id || index} className="flex min-w-0 items-start gap-2.5">
                  <div
                    className={cn(
                      "w-2 h-2 rounded-full shrink-0",
                      priorityConfig[material.priority]?.color || "bg-gray-400"
                    )}
                  />
                  <span className="min-w-0 break-words text-sm leading-5 text-foreground">{material.name}</span>
                </div>
              ))}
              {mode === "coach" && report.missing_evidence.length > visibleMissingEvidence.length && (
                <span className="text-xs text-muted-foreground">
                  还有 {report.missing_evidence.length - visibleMissingEvidence.length} 项...
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

      {primaryAction ? (
        <Card className="min-w-0 py-4 shadow-sm">
          <CardHeader className="pb-3 px-4">
            <CardTitle className="flex min-w-0 items-center gap-3 text-base font-semibold">
              <div className="w-9 h-9 rounded-full bg-green-100 flex items-center justify-center flex-shrink-0">
                <Zap className="w-4 h-4 text-green-600" />
              </div>
              <span className="truncate">{mode === "coach" ? "建议动作" : "下一步"}</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 px-4">
            <h4 className="break-words text-sm font-semibold leading-5 text-foreground">
              {primaryAction.title}
            </h4>
            <p className="break-words text-sm leading-relaxed text-muted-foreground">
              {primaryAction.description}
            </p>
            <button
              onClick={() => onActionClick(primaryAction)}
              className="flex items-center gap-1 text-sm font-medium text-primary hover:text-primary/80 transition-colors"
            >
              {primaryAction.cta_text}
              <ArrowRight className="w-4 h-4" />
            </button>
          </CardContent>
        </Card>
      ) : null}

      {mode === "coach" && (
        <Card className="min-w-0 py-4 shadow-sm">
          <CardHeader className="pb-3 px-4">
            <CardTitle className="flex min-w-0 items-center gap-3 text-base font-semibold">
              <div className="w-9 h-9 rounded-full bg-amber-100 flex items-center justify-center flex-shrink-0">
                <BrainCircuit className="w-4 h-4 text-amber-600" />
              </div>
              <span className="truncate">教练提示</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 px-4">
            {report.recommended_improvements.length > 0 ? (
              report.recommended_improvements.slice(0, 3).map((item) => (
                <div key={item} className="rounded-xl border border-border bg-muted/30 px-3 py-2">
                  <p className="break-words text-sm leading-relaxed text-foreground">{item}</p>
                </div>
              ))
            ) : (
              <span className="text-sm text-muted-foreground">暂无额外教练提示</span>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
