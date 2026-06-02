"use client"

import { AlertCircle, Bug, ClipboardCopy, RefreshCw } from "lucide-react"

import { APP_VERSION_LABEL, appVersionDetailLabel } from "@/lib/app-version"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import type {
  DebugMaterialBundleResponse,
  RuntimeDebugEvent,
  RuntimeDebugSnapshot,
} from "@/lib/api/types"

interface RuntimeDebugPanelProps {
  sessionId: string | null
  snapshot: RuntimeDebugSnapshot | null
  liveEvents: RuntimeDebugEvent[]
  latestDebugBundle: DebugMaterialBundleResponse | null
  isLoading: boolean
  error?: string | null
  onRefresh: () => void
  onCopyDebugPackage: () => void
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

function asList(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.filter(
        (item): item is Record<string, unknown> =>
          Boolean(item) && typeof item === "object" && !Array.isArray(item),
      )
    : []
}

function displayValue(value: unknown, fallback = "未获取"): string {
  if (typeof value === "string" && value.trim()) {
    return value
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value)
  }
  return fallback
}

function statusTone(status: unknown): "default" | "outline" | "secondary" | "destructive" {
  if (status === "failed" || status === "error") {
    return "destructive"
  }
  if (status === "completed") {
    return "default"
  }
  if (status === "started" || status === "still_running") {
    return "secondary"
  }
  return "outline"
}

function jsonPreview(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2)
}

export function RuntimeDebugPanel({
  sessionId,
  snapshot,
  liveEvents,
  latestDebugBundle,
  isLoading,
  error,
  onRefresh,
  onCopyDebugPackage,
}: RuntimeDebugPanelProps) {
  const backend = asRecord(snapshot?.backend)
  const currentRuntime = asRecord(snapshot?.current_runtime)
  const runtimeViewState = asRecord(snapshot?.runtime_view_state)
  const caseBoard = asRecord(snapshot?.case_board)
  const evidenceGraph = asRecord(snapshot?.evidence_graph)
  const caseClaims = asList(caseBoard.claims)
  const evidenceCards = asList(caseBoard.evidence_cards)
  const proofPoints = asList(caseBoard.proof_points)
  const caseConflicts = asList(caseBoard.conflicts)
  const graphEdges = asList(evidenceGraph.edges)
  const nextMove = asRecord(caseBoard.next_move ?? evidenceGraph.next_move)
  const lastMaterialRefresh = asRecord(snapshot?.last_material_refresh)
  const snapshotMaterial = asRecord(snapshot?.material_generation)
  const materialUnderstanding = asList(snapshot?.material_understanding)
  const snapshotGeneration = asRecord(snapshotMaterial.generation)
  const latestGeneration = latestDebugBundle?.generation ?? null
  const generation = latestGeneration ?? snapshotGeneration
  const errors = asList(snapshot?.errors)
  const snapshotTimeline = asList(snapshot?.timeline)
  const trace = asList(snapshot?.runtime_trace)
  const latestEvents: Array<Record<string, unknown>> = [
    ...liveEvents.slice(-40).reverse().map((event) => ({ ...event })),
    ...snapshotTimeline.slice(-40).reverse(),
  ].slice(0, 40)

  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <div className="flex shrink-0 items-center justify-between gap-3 border-b border-border px-4 py-3 md:px-6">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2">
            <Bug className="h-4 w-4 shrink-0 text-primary" />
            <h2 className="truncate text-base font-semibold text-foreground">
              运行时调试台
            </h2>
            <Badge variant="outline">{APP_VERSION_LABEL}</Badge>
          </div>
          <p className="mt-1 truncate text-xs text-muted-foreground">
            {sessionId ?? "当前没有进行中的会话"}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={onRefresh}
            disabled={!sessionId || isLoading}
            className="gap-2"
          >
            <RefreshCw className={isLoading ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
            刷新
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={onCopyDebugPackage}
            disabled={!sessionId}
            className="gap-2"
          >
            <ClipboardCopy className="h-4 w-4" />
            复制
          </Button>
        </div>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="grid gap-4 p-4 md:grid-cols-2 md:p-6 xl:grid-cols-3">
          {error ? (
            <div className="md:col-span-2 xl:col-span-3 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
              {error}
            </div>
          ) : null}

          <Card className="py-4">
            <CardHeader className="px-5 pb-3">
              <CardTitle className="text-sm">版本</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 px-5 text-sm">
              <div className="flex justify-between gap-3">
                <span className="text-muted-foreground">前端</span>
                <span className="truncate font-mono">{appVersionDetailLabel()}</span>
              </div>
              <div className="flex justify-between gap-3">
                <span className="text-muted-foreground">后端</span>
                <span className="truncate font-mono">
                  {displayValue(backend.version)}
                </span>
              </div>
              <div className="flex justify-between gap-3">
                <span className="text-muted-foreground">Git</span>
                <span className="truncate font-mono">
                  {displayValue(backend.git_sha)}
                </span>
              </div>
            </CardContent>
          </Card>

          <Card className="py-4">
            <CardHeader className="px-5 pb-3">
              <CardTitle className="text-sm">运行时</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 px-5 text-sm">
              <div className="flex justify-between gap-3">
                <span className="text-muted-foreground">配置</span>
                <span className="truncate font-mono">
                  {displayValue(currentRuntime.configured_runtime)}
                </span>
              </div>
              <div className="flex justify-between gap-3">
                <span className="text-muted-foreground">本轮</span>
                <span className="truncate font-mono">
                  {displayValue(currentRuntime.turn_selected_public_runtime)}
                </span>
              </div>
              <div className="flex justify-between gap-3">
                <span className="text-muted-foreground">材料刷新</span>
                <span className="truncate font-mono">
                  {displayValue(currentRuntime.material_selected_public_runtime)}
                </span>
              </div>
            </CardContent>
          </Card>

          <Card className="py-4">
            <CardHeader className="px-5 pb-3">
              <CardTitle className="text-sm">当前状态</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 px-5 text-sm">
              <div className="flex justify-between gap-3">
                <span className="text-muted-foreground">decision</span>
                <span className="truncate font-mono">
                  {displayValue(runtimeViewState.decision)}
                </span>
              </div>
              <div className="flex justify-between gap-3">
                <span className="text-muted-foreground">public_status</span>
                <span className="truncate font-mono">
                  {displayValue(runtimeViewState.public_status)}
                </span>
              </div>
              <div className="flex justify-between gap-3">
                <span className="text-muted-foreground">key_proof</span>
                <span className="truncate font-mono">
                  {displayValue(runtimeViewState.current_key_proof)}
                </span>
              </div>
            </CardContent>
          </Card>

          <Card className="py-4 md:col-span-2 xl:col-span-3">
            <CardHeader className="px-5 pb-3">
              <CardTitle className="text-sm">案件事实图</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 px-5 text-sm">
              <div className="grid gap-2 md:grid-cols-5">
                <div className="rounded-lg border border-border bg-muted/20 px-3 py-2">
                  <div className="text-xs text-muted-foreground">claims</div>
                  <div className="mt-1 font-mono text-base">{caseClaims.length}</div>
                </div>
                <div className="rounded-lg border border-border bg-muted/20 px-3 py-2">
                  <div className="text-xs text-muted-foreground">evidence</div>
                  <div className="mt-1 font-mono text-base">{evidenceCards.length}</div>
                </div>
                <div className="rounded-lg border border-border bg-muted/20 px-3 py-2">
                  <div className="text-xs text-muted-foreground">proof</div>
                  <div className="mt-1 font-mono text-base">{proofPoints.length}</div>
                </div>
                <div className="rounded-lg border border-border bg-muted/20 px-3 py-2">
                  <div className="text-xs text-muted-foreground">conflicts</div>
                  <div className="mt-1 font-mono text-base">{caseConflicts.length}</div>
                </div>
                <div className="rounded-lg border border-border bg-muted/20 px-3 py-2">
                  <div className="text-xs text-muted-foreground">edges</div>
                  <div className="mt-1 font-mono text-base">{graphEdges.length}</div>
                </div>
              </div>
              {nextMove.question ? (
                <div className="rounded-lg border border-border bg-muted/20 px-3 py-2 text-xs">
                  <div className="font-medium text-foreground">
                    {displayValue(nextMove.question)}
                  </div>
                  <div className="mt-1 text-muted-foreground">
                    {displayValue(nextMove.reason)}
                  </div>
                </div>
              ) : (
                <div className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-sm text-muted-foreground">
                  暂无 Case Memory 事实图。
                </div>
              )}
            </CardContent>
          </Card>

          <Card className="py-4 md:col-span-2 xl:col-span-3">
            <CardHeader className="px-5 pb-3">
              <CardTitle className="text-sm">材料理解</CardTitle>
            </CardHeader>
            <CardContent className="px-5">
              {materialUnderstanding.length ? (
                <div className="space-y-2">
                  {materialUnderstanding.map((item, index) => {
                    const understandingError = asRecord(item.understanding_error)
                    return (
                      <div
                        key={`${displayValue(item.document_id, "doc")}-${index}`}
                        className="grid gap-2 rounded-lg border border-border bg-muted/20 px-3 py-2 text-xs md:grid-cols-[minmax(0,1fr)_120px_minmax(0,1.5fr)]"
                      >
                        <div className="min-w-0">
                          <div className="truncate font-medium text-foreground">
                            {displayValue(item.filename)}
                          </div>
                          <div className="mt-0.5 truncate font-mono text-muted-foreground">
                            {displayValue(item.document_id)}
                          </div>
                        </div>
                        <Badge
                          variant={statusTone(item.understanding_status)}
                          className="w-fit"
                        >
                          {displayValue(item.understanding_status)}
                        </Badge>
                        <div className="min-w-0">
                          {understandingError.code || understandingError.message ? (
                            <div className="flex min-w-0 items-start gap-2 text-destructive">
                              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                              <span className="min-w-0 break-words">
                                {displayValue(understandingError.code, "error")}
                                {understandingError.message
                                  ? `：${displayValue(understandingError.message)}`
                                  : ""}
                              </span>
                            </div>
                          ) : (
                            <span className="text-muted-foreground">
                              {displayValue(item.document_type, "无错误")}
                            </span>
                          )}
                        </div>
                      </div>
                    )
                  })}
                </div>
              ) : (
                <div className="rounded-lg border border-dashed border-border px-3 py-8 text-center text-sm text-muted-foreground">
                  上传或解析材料后，这里会显示案例理解状态。
                </div>
              )}
            </CardContent>
          </Card>

          <Card className="py-4 md:col-span-2 xl:col-span-3">
            <CardHeader className="px-5 pb-3">
              <CardTitle className="text-sm">时间线</CardTitle>
            </CardHeader>
            <CardContent className="px-5">
              {latestEvents.length ? (
                <div className="space-y-2">
                  {latestEvents.map((event, index) => (
                    <div
                      key={`${displayValue(event.received_at, "event")}-${index}`}
                      className="grid gap-2 rounded-lg border border-border bg-muted/20 px-3 py-2 text-xs md:grid-cols-[160px_120px_1fr]"
                    >
                      <div className="truncate font-mono text-muted-foreground">
                        {displayValue(event.phase)}.{displayValue(event.step)}
                      </div>
                      <Badge variant={statusTone(event.status)} className="w-fit">
                        {displayValue(event.status)}
                      </Badge>
                      <div className="min-w-0 truncate text-foreground">
                        {displayValue(event.summary, "无摘要")}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="rounded-lg border border-dashed border-border px-3 py-8 text-center text-sm text-muted-foreground">
                  发送消息、上传材料或生成材料包后，这里会显示后端步骤。
                </div>
              )}
            </CardContent>
          </Card>

          <Card className="py-4 md:col-span-2">
            <CardHeader className="px-5 pb-3">
              <CardTitle className="text-sm">材料生成</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-3 px-5 text-sm md:grid-cols-2">
              <div>
                <div className="text-xs text-muted-foreground">bundle</div>
                <div className="mt-1 break-all font-mono">
                  {latestDebugBundle?.bundle_id ??
                    displayValue(snapshotMaterial.bundle_id)}
                </div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">source / mode</div>
                <div className="mt-1 font-mono">
                  {displayValue(generation.source)} / {displayValue(generation.mode)}
                </div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">seed_source</div>
                <div className="mt-1 font-mono">
                  {displayValue(generation.seed_source)}
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="py-4">
            <CardHeader className="px-5 pb-3">
              <CardTitle className="text-sm">错误</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 px-5 text-xs">
              {errors.length ? (
                errors.map((item, index) => (
                  <pre
                    key={index}
                    className="max-h-32 overflow-auto rounded-lg bg-muted/40 p-3"
                  >
                    {jsonPreview(item)}
                  </pre>
                ))
              ) : (
                <div className="text-sm text-muted-foreground">未看到运行时错误。</div>
              )}
            </CardContent>
          </Card>

          <Card className="py-4 md:col-span-2 xl:col-span-3">
            <CardHeader className="px-5 pb-3">
              <CardTitle className="text-sm">快照 JSON</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 px-5">
              <div className="grid gap-4 xl:grid-cols-2">
                <pre className="max-h-[420px] overflow-auto rounded-lg bg-muted/40 p-4 text-xs">
                  {jsonPreview(snapshot)}
                </pre>
                <pre className="max-h-[420px] overflow-auto rounded-lg bg-muted/40 p-4 text-xs">
                  {jsonPreview({
                    live_events: liveEvents,
                    latest_debug_bundle: latestDebugBundle,
                    last_material_refresh: lastMaterialRefresh,
                    runtime_trace_tail: trace.slice(-20),
                  })}
                </pre>
              </div>
            </CardContent>
          </Card>
        </div>
      </ScrollArea>
    </div>
  )
}
