"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  FileText,
  FileImage,
  FolderOpen,
  ImageIcon,
  Info,
  ExternalLink,
} from "lucide-react"
import type { SessionHistoryEntry, UploadedMaterial } from "@/lib/api/types"
import { cn } from "@/lib/utils"

interface MaterialsPanelProps {
  currentMaterials: UploadedMaterial[]
  historyEntries: SessionHistoryEntry[]
  currentSessionId: string | null
}

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

function MaterialPreview({ material }: { material: UploadedMaterial }) {
  if (material.raw_text) {
    return (
      <div className="h-full w-full overflow-hidden rounded-xl bg-slate-950 p-4 text-left">
        <pre className="max-h-full overflow-hidden whitespace-pre-wrap break-words text-xs leading-5 text-slate-100">
          {material.raw_text}
        </pre>
      </div>
    )
  }

  if (material.kind === "image" && material.preview_url) {
    return (
      // Blob URL previews are generated locally and are not suitable for next/image optimization.
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={material.preview_url}
        alt={material.name}
        className="h-full w-full rounded-xl object-cover"
      />
    )
  }

  return (
    <div className="flex h-full w-full items-center justify-center rounded-xl bg-muted/40">
      {material.kind === "pdf" ? (
        <FileText className="h-10 w-10 text-rose-500" />
      ) : material.kind === "image" ? (
        <ImageIcon className="h-10 w-10 text-sky-500" />
      ) : (
        <FileImage className="h-10 w-10 text-muted-foreground" />
      )}
    </div>
  )
}

function materialOpenUrl(material: UploadedMaterial): string | null {
  return material.content_url ?? material.preview_url ?? null
}

function FieldTable({ fields }: { fields?: Record<string, string> }) {
  const entries = Object.entries(fields ?? {})
  if (!entries.length) {
    return null
  }

  return (
    <div className="space-y-2">
      <div className="text-xs font-medium text-muted-foreground">
        结构化字段
      </div>
      <div className="max-h-52 overflow-y-auto rounded-xl border border-border">
        {entries.map(([fieldPath, value]) => (
          <div
            key={fieldPath}
            className="grid gap-1 border-b border-border px-3 py-2 last:border-b-0 md:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]"
          >
            <div className="break-all text-xs font-medium text-muted-foreground">
              {fieldPath}
            </div>
            <div className="break-words text-xs leading-5 text-foreground">
              {value}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function RawTextBlock({ rawText }: { rawText?: string | null }) {
  if (!rawText) {
    return null
  }

  return (
    <div className="space-y-2">
      <div className="text-xs font-medium text-muted-foreground">材料正文</div>
      <pre className="max-h-64 overflow-y-auto whitespace-pre-wrap break-words rounded-xl border border-border bg-muted/20 p-3 text-xs leading-6 text-foreground">
        {rawText}
      </pre>
    </div>
  )
}

function ExpectedFindings({ material }: { material: UploadedMaterial }) {
  if (!material.expected_findings?.length || !material.synthetic_bundle_id) {
    return null
  }

  return (
    <div className="space-y-2">
      <div className="text-xs font-medium text-muted-foreground">核验线索</div>
      <div className="space-y-2">
        {material.expected_findings.map((finding, index) => (
          <div
            key={`${finding.kind}-${finding.field_path ?? index}`}
            className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900"
          >
            <div className="font-medium">{finding.kind}</div>
            <div className="mt-1 break-words">{finding.description}</div>
            {finding.field_path ? (
              <div className="mt-1 break-all text-amber-800">
                {finding.field_path}
              </div>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  )
}

function CaseUnderstandingSummary({ material }: { material: UploadedMaterial }) {
  const claims = material.claims ?? []
  const evidenceCards = material.evidence_cards ?? []
  const proofPoints = material.proof_points ?? []
  const conflicts = material.conflicts ?? []
  const unknowns = material.case_board_delta?.latest_material?.unknowns ?? []
  const nextMove = material.next_move ?? material.case_board_delta?.next_move ?? null

  if (
    !claims.length &&
    !evidenceCards.length &&
    !proofPoints.length &&
    !conflicts.length &&
    !unknowns.length &&
    !nextMove
  ) {
    return null
  }

  return (
    <div className="space-y-4">
      <div className="text-xs font-medium text-muted-foreground">案例理解</div>

      {claims.length ? (
        <div className="space-y-2">
          {claims.map((claim) => (
            <div
              key={claim.claim_id}
              className="rounded-xl border border-border bg-background px-3 py-2"
            >
              <div className="text-xs text-muted-foreground">
                {claim.field_label ?? claim.field_path}
              </div>
              <div className="mt-1 break-words text-sm leading-6 text-foreground">
                {claim.value ?? claim.status}
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {evidenceCards.length ? (
        <div className="space-y-2">
          <div className="text-xs font-medium text-muted-foreground">证据片段</div>
          {evidenceCards.map((card) => (
            <div
              key={card.evidence_id}
              className="rounded-xl border border-border bg-muted/20 px-3 py-2"
            >
              <p className="break-words text-sm leading-6 text-foreground">
                {card.excerpt}
              </p>
              {card.page_number ? (
                <div className="mt-1 text-xs text-muted-foreground">
                  第 {card.page_number} 页
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}

      {proofPoints.length ? (
        <div className="space-y-2">
          <div className="text-xs font-medium text-muted-foreground">证明点</div>
          {proofPoints.map((proof) => (
            <div
              key={proof.proof_point_id}
              className="rounded-xl border border-border bg-muted/20 px-3 py-2"
            >
              <div className="break-words text-sm font-medium leading-6 text-foreground">
                {proof.question}
              </div>
              <div className="mt-1 break-words text-xs leading-5 text-muted-foreground">
                {proof.why_it_matters}
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {conflicts.length ? (
        <div className="space-y-2">
          <div className="text-xs font-medium text-red-700">冲突</div>
          {conflicts.map((conflict) => (
            <div
              key={conflict.conflict_id}
              className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-red-950"
            >
              <p className="break-words text-sm leading-6">{conflict.summary}</p>
              {conflict.suggested_followup ? (
                <div className="mt-1 break-words text-xs leading-5 text-red-800">
                  {conflict.suggested_followup}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}

      {unknowns.length ? (
        <div className="rounded-xl border border-border bg-muted/20 px-3 py-2">
          <div className="text-xs font-medium text-muted-foreground">未知项</div>
          <ul className="mt-1 space-y-1">
            {unknowns.map((unknown) => (
              <li key={unknown} className="break-words text-xs leading-5 text-muted-foreground">
                {unknown}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {nextMove ? (
        <div className="rounded-xl border border-border bg-muted/20 px-3 py-2">
          <div className="text-xs font-medium text-muted-foreground">建议追问</div>
          <div className="mt-1 break-words text-sm font-medium leading-6 text-foreground">
            {nextMove.question}
          </div>
          <div className="mt-1 break-words text-xs leading-5 text-muted-foreground">
            {nextMove.reason}
          </div>
        </div>
      ) : null}
    </div>
  )
}

function MaterialGrid({
  materials,
  emptyText,
}: {
  materials: UploadedMaterial[]
  emptyText: string
}) {
  if (!materials.length) {
    return (
      <div className="break-words text-sm leading-6 text-muted-foreground">
        {emptyText}
      </div>
    )
  }

  return (
    <div className="grid min-w-0 grid-cols-1 gap-4 md:grid-cols-2 2xl:grid-cols-3">
      {materials.map((material) => (
        <MaterialCard key={material.id} material={material} />
      ))}
    </div>
  )
}

function MaterialCard({ material }: { material: UploadedMaterial }) {
  const openUrl = materialOpenUrl(material)

  return (
    <div className="group relative min-w-0 overflow-hidden rounded-2xl border border-border bg-background transition-all hover:shadow-md">
      <div className="aspect-[4/3] border-b border-border p-3">
        <MaterialPreview material={material} />
        <div className="absolute right-3 top-3">
          <Dialog>
            <DialogTrigger asChild>
              <Button
                size="icon"
                variant="secondary"
                className="h-8 w-8 rounded-full shadow-sm"
                aria-label="查看材料详情"
              >
                <Info className="h-4 w-4" />
              </Button>
            </DialogTrigger>
            <DialogContent className="max-h-[86vh] max-w-[min(1180px,calc(100vw-2rem))] overflow-hidden rounded-3xl p-0 sm:max-w-[min(1180px,calc(100vw-2rem))]">
              <div className="grid max-h-[86vh] min-w-0 overflow-y-auto lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
                <div className="min-w-0 bg-muted/30 p-4 md:p-6">
                  <div className="aspect-[3/4] overflow-hidden rounded-2xl border border-border bg-background">
                    <MaterialPreview material={material} />
                  </div>
                </div>
                <div className="min-w-0 p-4 md:p-6">
                  <DialogHeader className="mb-4">
                    <DialogTitle className="break-all text-lg font-semibold leading-snug md:text-xl">
                      {material.name}
                    </DialogTitle>
                    <DialogDescription>
                      查看材料正文、案例理解和证据片段。
                    </DialogDescription>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <Badge variant="outline">
                        {material.kind === "pdf"
                          ? "PDF"
                          : material.kind === "image"
                            ? "图片"
                            : "文件"}
                      </Badge>
                      {material.synthetic_bundle_id ? (
                        <Badge variant="outline">材料包</Badge>
                      ) : null}
                      <span className="break-words text-xs leading-5 text-muted-foreground">
                        {formatDateTime(material.uploaded_at)}
                      </span>
                    </div>
                  </DialogHeader>

                  <div className="flex-1 space-y-4">
                    <div className="rounded-2xl border border-border bg-muted/20 p-4">
                      <div className="mb-1 text-xs font-medium text-muted-foreground">
                        识别结果 (Document Type)
                      </div>
                      <div className="break-words text-sm font-semibold text-foreground">
                        {material.document_type_label ?? material.status_label}
                      </div>
                    </div>

                    {material.feedback_message && (
                      <div className="space-y-1.5">
                        <div className="text-xs font-medium text-muted-foreground">
                          反馈建议
                        </div>
                        <p className="max-h-44 overflow-y-auto break-words pr-2 text-sm leading-7 text-foreground">
                          {material.feedback_message}
                        </p>
                      </div>
                    )}

                    <CaseUnderstandingSummary material={material} />
                    <FieldTable fields={material.fields} />
                    <RawTextBlock rawText={material.raw_text} />
                    <ExpectedFindings material={material} />
                  </div>

                  <div className="mt-6 flex flex-col gap-3 sm:flex-row">
                    {openUrl && (
                      <Button
                        asChild
                        variant="outline"
                        className="min-w-0 flex-1 rounded-xl"
                      >
                        <a href={openUrl} target="_blank" rel="noreferrer">
                          <ExternalLink className="mr-2 h-4 w-4" />
                          打开原始文件
                        </a>
                      </Button>
                    )}
                    <DialogClose asChild>
                      <Button className="min-w-0 flex-1 rounded-xl">
                        确定
                      </Button>
                    </DialogClose>
                  </div>
                </div>
              </div>
            </DialogContent>
          </Dialog>
        </div>
      </div>
      <div className="space-y-2 p-4">
        <div className="flex min-w-0 items-start justify-between gap-3">
          <div className="min-w-0">
            <div
              className="truncate text-sm font-medium text-foreground"
              title={material.name}
            >
              {material.name}
            </div>
            <div className="mt-1 text-xs text-muted-foreground">
              {formatDateTime(material.uploaded_at)}
            </div>
          </div>
          <Badge variant="outline" className="shrink-0">
            {material.kind === "pdf"
              ? "PDF"
              : material.kind === "image"
                ? "图片"
                : "文件"}
          </Badge>
        </div>
        {material.synthetic_bundle_id ? (
          <Badge variant="outline" className="w-fit">
            材料包
          </Badge>
        ) : null}
        <div className="rounded-xl bg-muted/30 px-3 py-2">
          <div className="break-words text-xs leading-5 text-muted-foreground">
            识别结果
          </div>
          <div className="mt-1 line-clamp-2 break-words text-sm font-medium text-foreground">
            {material.document_type_label ?? material.status_label}
          </div>
        </div>
        {material.claims?.length ? (
          <div className="space-y-1 rounded-xl border border-border bg-background px-3 py-2">
            <div className="text-xs text-muted-foreground">已理解事实</div>
            {material.claims.slice(0, 2).map((claim) => (
              <div key={claim.claim_id} className="line-clamp-2 break-words text-xs leading-5 text-foreground">
                {claim.field_label ?? claim.field_path}: {claim.value ?? claim.status}
              </div>
            ))}
          </div>
        ) : material.understanding_status ? (
          <div className="break-words text-xs leading-5 text-muted-foreground">
            理解状态：{material.understanding_status}
          </div>
        ) : null}
        {material.raw_text ? (
          <div className="line-clamp-3 break-words rounded-xl border border-border bg-background px-3 py-2 text-xs leading-5 text-muted-foreground">
            {material.raw_text}
          </div>
        ) : null}
        <div className="pt-1">
          <span className="text-xs font-medium text-primary">
            打开详情可查看正文
          </span>
        </div>
      </div>
    </div>
  )
}

export function MaterialsPanel({
  currentMaterials,
  historyEntries,
  currentSessionId,
}: MaterialsPanelProps) {
  const archivedMaterials = historyEntries
    .filter((entry) => entry.session_id !== currentSessionId)
    .flatMap((entry) =>
      entry.materials.map((material) => ({
        ...material,
        id: `${entry.id}-${material.id}`,
      })),
    )

  return (
    <ScrollArea className="h-full min-w-0">
      <div className="min-w-0 space-y-4 p-3 md:p-4">
        <Card className="py-4">
          <CardHeader className="px-5 pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <FolderOpen className="h-4 w-4 text-primary" />
              当前会话材料
            </CardTitle>
          </CardHeader>
          <CardContent className="px-5">
            <MaterialGrid
              materials={currentMaterials}
              emptyText="当前会话还没有上传材料。图片上传后会展示缩略图，PDF 会显示文件图标。"
            />
          </CardContent>
        </Card>

        <Card className="py-4">
          <CardHeader className="px-5 pb-3">
            <CardTitle className="text-base">历史材料</CardTitle>
          </CardHeader>
          <CardContent className="px-5">
            <div
              className={cn(
                "rounded-xl border border-dashed border-border bg-muted/20 p-4",
                archivedMaterials.length && "border-none bg-transparent p-0",
              )}
            >
              <MaterialGrid
                materials={archivedMaterials}
                emptyText="历史会话中的材料会在这里归档展示。"
              />
            </div>
          </CardContent>
        </Card>
      </div>
    </ScrollArea>
  )
}
