"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Spinner } from "@/components/ui/spinner"
import { Skeleton } from "@/components/ui/skeleton"
import {
  FileText,
  ArrowRight,
  AlertCircle,
  BrainCircuit,
  Sparkles,
  RefreshCw,
  ClipboardList,
  Layers,
} from "lucide-react"
import { cn } from "@/lib/utils"
import type { UserReport, AllowedAction, UploadedMaterial } from "@/lib/api/types"
import {
  isMaterialUnderstandingFailed,
  materialUnderstandingErrorMessage,
  materialUnderstandingStatus,
} from "@/lib/upload-feedback-policy"
import { selectCaseUnderstandingPresentation } from "@/lib/case-board-presentation-policy"

export type PracticeMaterialsBrief = {
  user_summary_zh?: string | null
  document_briefs_zh?: Array<{
    document_type_label?: string | null
    filename?: string | null
    highlights?: Array<{ label: string; value: string }>
  }>
  scenario_label?: string | null
}

interface AnalysisPanelProps {
  report: UserReport | null
  isLoading?: boolean
  error?: string | null
  mode?: "simulation" | "coach"
  materials?: UploadedMaterial[]
  onViewDetails: () => void
  onViewAllMaterials: () => void
  onActionClick: (action: AllowedAction) => void
  className?: string
  /** Product practice materials (default off for backward compat). */
  practiceMaterialsEnabled?: boolean
  /** Chinese practice pack brief shown near the top of the rail. */
  practiceBrief?: PracticeMaterialsBrief | null
  /** Whether a session is currently active. */
  hasSession?: boolean
  /** One-click open of the practice materials generate dialog. */
  onOpenPracticeMaterials?: () => void
  /** True while a practice pack is being generated. */
  isPracticeGenerating?: boolean
}

const riskLevelConfig = {
  none: {
    label: "无明显风险",
    chip: "border-slate-200 bg-slate-100 text-slate-700 dark:border-slate-400/25 dark:bg-slate-400/10 dark:text-slate-200",
    dot: "bg-slate-400",
  },
  low: {
    label: "低风险",
    chip: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-400/25 dark:bg-emerald-400/10 dark:text-emerald-100",
    dot: "bg-emerald-500",
  },
  medium: {
    label: "中风险",
    chip: "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-400/25 dark:bg-amber-400/10 dark:text-amber-100",
    dot: "bg-amber-500",
  },
  high: {
    label: "高风险",
    chip: "border-red-200 bg-red-50 text-red-700 dark:border-red-400/25 dark:bg-red-400/10 dark:text-red-100",
    dot: "bg-red-500",
  },
}

const interviewResultConfig: Record<
  string,
  { chip: string; textColor: string; bgColor: string; dot: string }
> = {
  passed: {
    chip: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-400/25 dark:bg-emerald-400/10 dark:text-emerald-100",
    textColor: "text-emerald-700 dark:text-emerald-100",
    bgColor: "bg-emerald-50/80 border-emerald-200/80 dark:border-emerald-300/25 dark:bg-emerald-300/10",
    dot: "bg-emerald-500",
  },
  refused: {
    chip: "border-red-200 bg-red-50 text-red-700 dark:border-red-400/25 dark:bg-red-400/10 dark:text-red-100",
    textColor: "text-red-700 dark:text-red-100",
    bgColor: "bg-red-50/80 border-red-200/80 dark:border-red-300/25 dark:bg-red-300/10",
    dot: "bg-red-500",
  },
  not_passed: {
    chip: "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-400/25 dark:bg-amber-400/10 dark:text-amber-100",
    textColor: "text-amber-800 dark:text-amber-100",
    bgColor: "bg-amber-50/80 border-amber-200/80 dark:border-amber-300/25 dark:bg-amber-300/10",
    dot: "bg-amber-500",
  },
  in_progress: {
    chip: "border-sky-200 bg-sky-50 text-sky-700 dark:border-sky-400/25 dark:bg-sky-400/10 dark:text-sky-100",
    textColor: "text-sky-700 dark:text-sky-100",
    bgColor: "bg-sky-50/80 border-sky-200/80 dark:border-sky-300/25 dark:bg-sky-300/10",
    dot: "bg-sky-500",
  },
}

function StatusChip({
  label,
  className,
  dotClassName,
}: {
  label: string
  className?: string
  dotClassName?: string
}) {
  return (
    <span
      className={cn(
        "inline-flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold leading-none",
        className,
      )}
    >
      {dotClassName ? (
        <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", dotClassName)} />
      ) : null}
      <span className="truncate">{label}</span>
    </span>
  )
}

function PanelEmptyState({
  icon: Icon,
  title,
  description,
}: {
  icon: typeof ClipboardList
  title: string
  description: string
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-4 py-10 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-muted text-muted-foreground">
        <Icon className="h-6 w-6" />
      </div>
      <div className="space-y-1.5">
        <div className="text-sm font-semibold text-foreground">{title}</div>
        <p className="max-w-[14rem] text-xs leading-5 text-muted-foreground">
          {description}
        </p>
      </div>
    </div>
  )
}

function CaseBoardSkeleton({
  containerWidth,
  className,
}: {
  containerWidth: string
  className?: string
}) {
  return (
    <div
      className={cn(
        containerWidth,
        "min-w-0 shrink-0 flex-col gap-3 overflow-y-auto border-l border-border bg-background p-3 2xl:gap-4 2xl:p-4",
        className,
      )}
    >
      <div className="rounded-2xl border border-border bg-card p-3.5 shadow-sm">
        <div className="mb-3 flex items-center justify-between gap-2">
          <Skeleton className="h-6 w-16 rounded-full" />
          <Skeleton className="h-6 w-20 rounded-full" />
        </div>
        <div className="grid grid-cols-3 gap-2">
          <Skeleton className="h-14 rounded-xl" />
          <Skeleton className="h-14 rounded-xl" />
          <Skeleton className="h-14 rounded-xl" />
        </div>
        <Skeleton className="mt-3 h-12 w-full rounded-xl" />
      </div>
      <Skeleton className="h-24 w-full rounded-2xl" />
      <div className="space-y-2 rounded-2xl border border-border bg-card p-3.5">
        <Skeleton className="h-4 w-20" />
        <Skeleton className="h-12 w-full rounded-xl" />
        <Skeleton className="h-12 w-full rounded-xl" />
        <Skeleton className="h-12 w-full rounded-xl" />
      </div>
    </div>
  )
}

function PracticeGuideCard({
  compact = false,
  onOpen,
  isGenerating = false,
}: {
  compact?: boolean
  onOpen?: () => void
  isGenerating?: boolean
}) {
  return (
    <button
      type="button"
      onClick={() => onOpen?.()}
      disabled={isGenerating || !onOpen}
      className={cn(
        "group w-full min-w-0 rounded-xl border border-sky-200/80 bg-gradient-to-br from-sky-50 via-white to-violet-50 text-left shadow-sm transition-all",
        "hover:border-sky-300 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400/50",
        "disabled:cursor-not-allowed disabled:opacity-70",
        "dark:border-sky-400/25 dark:from-sky-500/10 dark:via-background dark:to-violet-500/10 dark:hover:border-sky-400/40",
        compact ? "px-3 py-3" : "px-4 py-4",
      )}
    >
      <div className="flex items-start gap-3">
        <div
          className={cn(
            "flex shrink-0 items-center justify-center rounded-full bg-sky-100 dark:bg-sky-400/15",
            compact ? "h-8 w-8" : "h-10 w-10",
          )}
        >
          {isGenerating ? (
            <Spinner className={cn(compact ? "h-3.5 w-3.5" : "h-4 w-4")} />
          ) : (
            <Sparkles
              className={cn(
                "text-sky-600 dark:text-sky-300",
                compact ? "h-3.5 w-3.5" : "h-4 w-4",
              )}
            />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div
            className={cn(
              "font-semibold text-foreground",
              compact ? "text-sm" : "text-sm 2xl:text-base",
            )}
          >
            {isGenerating ? "正在生成练习材料…" : "用一段话生成练习材料"}
          </div>
          <div className="mt-1 text-xs leading-5 text-muted-foreground">
            不必上传真实证件 · 点击直接填写
          </div>
          {!compact && !isGenerating ? (
            <div className="mt-2.5 flex items-center gap-1 text-xs font-medium text-sky-700 transition-colors group-hover:text-sky-800 dark:text-sky-300 dark:group-hover:text-sky-200">
              立即填写
              <ArrowRight className="h-3.5 w-3.5" />
            </div>
          ) : null}
        </div>
      </div>
    </button>
  )
}

function PracticeBriefCard({
  brief,
  onRegenerate,
  isGenerating = false,
}: {
  brief: PracticeMaterialsBrief
  onRegenerate?: () => void
  isGenerating?: boolean
}) {
  const summaryParagraphs = (brief.user_summary_zh ?? "")
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)
  const documentBriefs = brief.document_briefs_zh ?? []

  return (
    <Card className="min-w-0 border-sky-200/70 py-4 shadow-sm dark:border-sky-400/20">
      <CardHeader className="px-4 pb-3">
        <div className="flex min-w-0 items-start justify-between gap-2">
          <CardTitle className="flex min-w-0 items-center gap-3 text-base font-semibold">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-sky-100 dark:bg-sky-400/15">
              <Sparkles className="h-4 w-4 text-sky-600 dark:text-sky-300" />
            </div>
            <span className="truncate">练习材料说明</span>
          </CardTitle>
          <Badge
            variant="outline"
            className="shrink-0 border-amber-300/70 bg-amber-50 text-amber-800 dark:border-amber-400/30 dark:bg-amber-400/10 dark:text-amber-100"
          >
            练习材料 · 虚构
          </Badge>
        </div>
        {brief.scenario_label ? (
          <div className="mt-2 pl-12 text-xs text-muted-foreground">
            场景：{brief.scenario_label}
          </div>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-3 px-4">
        {summaryParagraphs.length > 0 ? (
          <div className="space-y-2 rounded-xl border border-border bg-muted/30 px-3 py-2.5">
            {summaryParagraphs.map((paragraph, index) => (
              <p
                key={`practice-summary-${index}`}
                className="break-words text-sm leading-relaxed text-foreground"
              >
                {paragraph}
              </p>
            ))}
          </div>
        ) : null}

        {documentBriefs.length > 0 ? (
          <div className="space-y-2">
            {documentBriefs.map((doc, index) => {
              const title =
                doc.document_type_label?.trim() ||
                doc.filename?.trim() ||
                `材料 ${index + 1}`
              const highlights = doc.highlights ?? []
              return (
                <div
                  key={`practice-doc-${index}-${title}`}
                  className="min-w-0 rounded-lg border border-border px-3 py-2"
                >
                  <div className="text-sm font-medium text-foreground">{title}</div>
                  {doc.filename &&
                  doc.document_type_label &&
                  doc.filename !== doc.document_type_label ? (
                    <div className="mt-0.5 truncate text-xs text-muted-foreground">
                      {doc.filename}
                    </div>
                  ) : null}
                  {highlights.length > 0 ? (
                    <ul className="mt-2 space-y-1">
                      {highlights.map((item, hIndex) => (
                        <li
                          key={`practice-hl-${index}-${hIndex}-${item.label}`}
                          className="flex min-w-0 gap-2 text-xs leading-5"
                        >
                          <span className="shrink-0 text-muted-foreground">
                            {item.label}
                          </span>
                          <span className="min-w-0 break-words text-foreground">
                            {item.value}
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              )
            })}
          </div>
        ) : null}

        {onRegenerate ? (
          <button
            type="button"
            onClick={onRegenerate}
            disabled={isGenerating}
            className="flex items-center gap-1.5 text-sm font-medium text-primary transition-colors hover:text-primary/80 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isGenerating ? (
              <Spinner className="h-3.5 w-3.5" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            {isGenerating ? "正在生成…" : "重新生成"}
          </button>
        ) : null}
      </CardContent>
    </Card>
  )
}

function SecondarySection({
  title,
  count,
  children,
  action,
}: {
  title: string
  count?: number
  children: React.ReactNode
  action?: React.ReactNode
}) {
  return (
    <section className="min-w-0 rounded-2xl border border-border/80 bg-card/80 px-3.5 py-3 shadow-none">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <h3 className="text-xs font-semibold tracking-wide text-muted-foreground">
            {title}
          </h3>
          {typeof count === "number" ? (
            <span className="text-[11px] font-medium tabular-nums text-muted-foreground/80">
              {count}
            </span>
          ) : null}
        </div>
        {action}
      </div>
      <div className="space-y-1.5">{children}</div>
    </section>
  )
}

export function AnalysisPanel({
  report,
  isLoading = false,
  error,
  mode = "simulation",
  materials = [],
  onViewDetails,
  onViewAllMaterials,
  onActionClick,
  className,
  practiceMaterialsEnabled = false,
  practiceBrief = null,
  hasSession = false,
  onOpenPracticeMaterials,
  isPracticeGenerating = false,
}: AnalysisPanelProps) {
  const riskConfig = report ? riskLevelConfig[report.risk_level] : null
  const resultConfig = report
    ? interviewResultConfig[report.interview_result] ?? interviewResultConfig.in_progress
    : null
  const containerWidth = mode === "coach" ? "w-80 2xl:w-96" : "w-72 2xl:w-80"
  const primaryAction = report?.allowed_next_actions[0] ?? null
  const caseUnderstanding = selectCaseUnderstandingPresentation(
    report?.case_board,
    materials,
  )
  const understoodClaims = caseUnderstanding.claims
  const evidenceCards = caseUnderstanding.evidenceCards
  const proofPoints = caseUnderstanding.proofPoints
  const conflicts = caseUnderstanding.conflicts
  const latestMaterial = caseUnderstanding.latestMaterialStatusSource
  const failedMaterials = materials.filter(isMaterialUnderstandingFailed)
  const latestNextMove = caseUnderstanding.latestNextMove
  const visibleClaims = understoodClaims.slice(0, mode === "coach" ? 5 : 3)
  const visibleProofPoints = proofPoints.slice(0, 2)
  const visibleConflicts = conflicts.slice(0, 2)
  const hasNoMaterials = materials.length === 0
  const showPracticeGuide =
    Boolean(practiceMaterialsEnabled) && Boolean(hasSession) && hasNoMaterials

  // Skeleton instead of blank flash when first loading report / case board.
  if (isLoading && !report) {
    return <CaseBoardSkeleton containerWidth={containerWidth} className={className} />
  }

  // Full-panel error only when there is no last-good report to keep on screen.
  if (error && !report) {
    return (
      <div
        className={cn(
          containerWidth,
          "min-w-0 shrink-0 flex-col items-center justify-center gap-3 border-l border-border bg-background p-4",
          className,
        )}
      >
        <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-destructive/10 text-destructive">
          <AlertCircle className="h-6 w-6" />
        </div>
        <div className="space-y-1 text-center">
          <div className="text-sm font-semibold text-foreground">分析加载失败</div>
          <p className="max-w-[14rem] text-xs leading-5 text-destructive">{error}</p>
        </div>
      </div>
    )
  }

  if (!report) {
    // Product UX: one-click practice materials guide replaces empty state.
    if (showPracticeGuide) {
      return (
        <div
          className={cn(
            containerWidth,
            "min-w-0 shrink-0 flex-col gap-3 overflow-y-auto border-l border-border bg-background p-3 2xl:gap-4 2xl:p-4",
            className,
          )}
        >
          {practiceBrief ? (
            <PracticeBriefCard
              brief={practiceBrief}
              onRegenerate={onOpenPracticeMaterials}
              isGenerating={isPracticeGenerating}
            />
          ) : null}
          <PracticeGuideCard
            onOpen={onOpenPracticeMaterials}
            isGenerating={isPracticeGenerating}
          />
        </div>
      )
    }

    return (
      <div
        className={cn(
          containerWidth,
          "min-w-0 shrink-0 flex-col border-l border-border bg-background",
          className,
        )}
      >
        {!hasSession ? (
          <PanelEmptyState
            icon={Layers}
            title="尚无进行中的会话"
            description="选择签证类型并开始模拟后，案例状态与建议会显示在这里。"
          />
        ) : (
          <PanelEmptyState
            icon={ClipboardList}
            title="暂无案例分析"
            description="继续对话或上传材料后，风险、事实与下一步建议会出现在此。"
          />
        )}
      </div>
    )
  }

  return (
    <div
      className={cn(
        containerWidth,
        "min-w-0 shrink-0 flex-col gap-3 overflow-y-auto border-l border-border bg-background p-3 2xl:gap-3.5 2xl:p-4",
        className,
      )}
    >
      {error ? (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {error}
        </div>
      ) : null}
      {isLoading ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Spinner className="h-3.5 w-3.5" />
          <span>正在更新分析…</span>
        </div>
      ) : null}

      {/* Practice materials brief — near top, above status */}
      {practiceBrief ? (
        <PracticeBriefCard
          brief={practiceBrief}
          onRegenerate={onOpenPracticeMaterials}
          isGenerating={isPracticeGenerating}
        />
      ) : null}

      {/* Compact one-click guide when session has report but no materials yet */}
      {showPracticeGuide ? (
        <PracticeGuideCard
          compact
          onOpen={onOpenPracticeMaterials}
          isGenerating={isPracticeGenerating}
        />
      ) : null}

      {/* Hero: status chips + metrics (elevated grouping, no icon wells) */}
      <section className="min-w-0 rounded-2xl border border-border bg-card p-3.5 shadow-sm">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <StatusChip
            label={report.risk_level_label || riskConfig?.label || "风险未知"}
            className={riskConfig?.chip}
            dotClassName={riskConfig?.dot}
          />
          <StatusChip
            label={report.interview_result_label}
            className={resultConfig?.chip}
          />
        </div>

        <div className="grid grid-cols-3 gap-2">
          <div className="rounded-xl border border-border/70 bg-muted/30 px-2 py-2 text-center">
            <div
              className={cn(
                "text-base font-semibold tabular-nums",
                conflicts.length ? "text-red-600 dark:text-red-400" : "text-foreground",
              )}
            >
              {conflicts.length}
            </div>
            <div className="text-[11px] text-muted-foreground">冲突</div>
          </div>
          <div className="rounded-xl border border-border/70 bg-muted/30 px-2 py-2 text-center">
            <div className="text-base font-semibold tabular-nums text-foreground">
              {understoodClaims.length}
            </div>
            <div className="text-[11px] text-muted-foreground">事实</div>
          </div>
          <div className="rounded-xl border border-border/70 bg-muted/30 px-2 py-2 text-center">
            <div className="text-base font-semibold tabular-nums text-foreground">
              {proofPoints.length || evidenceCards.length}
            </div>
            <div className="text-[11px] text-muted-foreground">
              {proofPoints.length ? "待证明" : "证据"}
            </div>
          </div>
        </div>

        <div className={cn("mt-3 rounded-xl border px-3 py-2", resultConfig?.bgColor)}>
          <div className="mb-1 flex items-center gap-2">
            <span className={cn("h-2 w-2 shrink-0 rounded-full", resultConfig?.dot)} />
            <span className={cn("text-sm font-semibold", resultConfig?.textColor)}>
              {report.interview_result_label}
            </span>
          </div>
          <p className="line-clamp-2 break-words text-xs leading-relaxed text-muted-foreground">
            {report.interview_result_reason}
          </p>
        </div>

        <div className="mt-2.5 space-y-1.5 text-xs">
          <div className="flex min-w-0 items-center justify-between gap-3">
            <span className="text-muted-foreground">阶段</span>
            <span className="truncate font-medium text-foreground">
              {report.interview_status_label}
            </span>
          </div>
          <div className="flex min-w-0 items-center justify-between gap-3">
            <span className="text-muted-foreground">结论</span>
            <span className="max-w-[150px] truncate text-right font-medium text-foreground">
              {report.outcome_label}
            </span>
          </div>
          {report.current_key_proof_label ? (
            <div className="flex min-w-0 items-center justify-between gap-3">
              <span className="text-muted-foreground">待核实</span>
              <span className="max-w-[150px] truncate text-right font-medium text-foreground">
                {report.current_key_proof_label}
              </span>
            </div>
          ) : null}
        </div>

        {report.summary ? (
          <p className="mt-2.5 line-clamp-3 break-words text-xs leading-relaxed text-muted-foreground">
            {report.summary}
          </p>
        ) : null}

        <button
          type="button"
          onClick={onViewDetails}
          className="mt-2.5 flex items-center gap-1 text-xs font-medium text-primary transition-colors hover:text-primary/80"
        >
          查看详情
          <ArrowRight className="h-3.5 w-3.5" />
        </button>
      </section>

      {/* Primary next-action — elevated callout */}
      {primaryAction ? (
        <section className="min-w-0 rounded-2xl border border-primary/30 bg-primary/[0.06] p-3.5 shadow-sm dark:bg-primary/10">
          <div className="mb-1.5 text-[11px] font-bold uppercase tracking-wide text-primary">
            {mode === "coach" ? "建议动作" : "下一步"}
          </div>
          <h4 className="break-words text-sm font-semibold leading-5 text-foreground">
            {primaryAction.title}
          </h4>
          <p className="mt-1.5 break-words text-xs leading-relaxed text-muted-foreground">
            {primaryAction.description}
          </p>
          <button
            type="button"
            onClick={() => onActionClick(primaryAction)}
            className="mt-2.5 flex items-center gap-1 text-sm font-medium text-primary transition-colors hover:text-primary/80"
          >
            {primaryAction.cta_text}
            <ArrowRight className="h-4 w-4" />
          </button>
        </section>
      ) : latestNextMove ? (
        <section className="min-w-0 rounded-2xl border border-primary/30 bg-primary/[0.06] p-3.5 shadow-sm dark:bg-primary/10">
          <div className="mb-1.5 text-[11px] font-bold uppercase tracking-wide text-primary">
            下一步
          </div>
          <h4 className="break-words text-sm font-semibold leading-5 text-foreground">
            {latestNextMove.question}
          </h4>
          <p className="mt-1.5 break-words text-xs leading-relaxed text-muted-foreground">
            {latestNextMove.reason}
          </p>
        </section>
      ) : null}

      {/* Secondary: materials pulse */}
      {(latestMaterial || failedMaterials.length > 0) && (
        <SecondarySection title="材料">
          {latestMaterial ? (
            <div
              className={cn(
                "rounded-xl border px-2.5 py-2",
                isMaterialUnderstandingFailed(latestMaterial)
                  ? "border-destructive/25 bg-destructive/10"
                  : "border-border/70 bg-muted/20",
              )}
            >
              <div className="min-w-0 truncate text-xs font-medium text-foreground">
                {caseUnderstanding.latestMaterialName ?? "材料"}
              </div>
              {latestMaterial.understanding_status ? (
                <div className="mt-0.5 text-[11px] text-muted-foreground">
                  {materialUnderstandingStatus(latestMaterial)}
                </div>
              ) : null}
              {isMaterialUnderstandingFailed(latestMaterial) ? (
                <div className="mt-1 line-clamp-2 break-words text-[11px] leading-4 text-destructive">
                  {materialUnderstandingErrorMessage(latestMaterial) ??
                    "材料理解失败，请重新上传或稍后重试。"}
                </div>
              ) : null}
            </div>
          ) : null}
          {failedMaterials.length ? (
            <div className="rounded-xl border border-destructive/25 bg-destructive/10 px-2.5 py-2 text-[11px] leading-4 text-destructive">
              {failedMaterials.length} 份材料理解失败，打开材料库查看原因。
            </div>
          ) : null}
        </SecondarySection>
      )}

      {/* Secondary: proof points (quieter) */}
      {visibleProofPoints.length > 0 ? (
        <SecondarySection title="待证明" count={proofPoints.length}>
          {visibleProofPoints.map((proof) => (
            <div
              key={proof.proof_point_id}
              className="rounded-xl border border-border/70 bg-muted/15 px-2.5 py-2"
            >
              <div className="break-words text-xs font-medium leading-5 text-foreground/90">
                {proof.question}
              </div>
              {proof.why_it_matters ? (
                <div className="mt-0.5 break-words text-[11px] leading-4 text-muted-foreground">
                  {proof.why_it_matters}
                </div>
              ) : null}
            </div>
          ))}
        </SecondarySection>
      ) : null}

      {/* Secondary: conflicts (quieter, semantic red) */}
      {visibleConflicts.length > 0 ? (
        <SecondarySection title="冲突" count={conflicts.length}>
          {visibleConflicts.map((conflict) => (
            <div
              key={conflict.conflict_id}
              className="rounded-xl border border-red-200/80 bg-red-50/70 px-2.5 py-2 dark:border-red-400/20 dark:bg-red-400/10"
            >
              <p className="break-words text-xs leading-5 text-red-950 dark:text-red-100">
                {conflict.summary}
              </p>
              {conflict.suggested_followup ? (
                <div className="mt-0.5 break-words text-[11px] leading-4 text-red-800/90 dark:text-red-200/80">
                  {conflict.suggested_followup}
                </div>
              ) : null}
            </div>
          ))}
        </SecondarySection>
      ) : null}

      {/* Secondary: claims (quietest list) */}
      <SecondarySection
        title="已知事实"
        count={understoodClaims.length}
        action={
          <button
            type="button"
            onClick={onViewAllMaterials}
            className="flex items-center gap-0.5 text-[11px] font-medium text-primary transition-colors hover:text-primary/80"
          >
            材料证据
            <ArrowRight className="h-3 w-3" />
          </button>
        }
      >
        {visibleClaims.length > 0 ? (
          <>
            {visibleClaims.map((claim) => (
              <div
                key={claim.claim_id}
                className="min-w-0 rounded-xl border border-border/60 bg-muted/10 px-2.5 py-1.5"
              >
                <div className="text-[11px] text-muted-foreground">
                  {claim.field_label ?? claim.field_path}
                </div>
                <div className="mt-0.5 break-words text-xs leading-5 text-foreground/90">
                  {claim.value ?? claim.status}
                </div>
              </div>
            ))}
            {understoodClaims.length > visibleClaims.length ? (
              <span className="block px-0.5 text-[11px] text-muted-foreground">
                还有 {understoodClaims.length - visibleClaims.length} 个事实…
              </span>
            ) : null}
          </>
        ) : (
          <div className="flex items-start gap-2 rounded-xl border border-dashed border-border/80 bg-muted/10 px-2.5 py-2.5">
            <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <span className="text-[11px] leading-4 text-muted-foreground">
              上传材料后会在这里显示已理解的事实与证据。
            </span>
          </div>
        )}
      </SecondarySection>

      {/* Coach tips — secondary weight */}
      {mode === "coach" ? (
        <SecondarySection title="教练提示">
          {report.recommended_improvements.length > 0 ? (
            report.recommended_improvements.slice(0, 3).map((item) => (
              <div
                key={item}
                className="flex gap-2 rounded-xl border border-border/70 bg-muted/15 px-2.5 py-2"
              >
                <BrainCircuit className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <p className="break-words text-xs leading-5 text-foreground/90">{item}</p>
              </div>
            ))
          ) : (
            <span className="block px-0.5 text-[11px] text-muted-foreground">
              暂无额外教练提示
            </span>
          )}
        </SecondarySection>
      ) : null}
    </div>
  )
}
