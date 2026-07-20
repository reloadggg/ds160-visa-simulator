"use client"

import { useState } from "react"
import { FileStack, Loader2 } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"

export type PracticeMaterialsDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  visaType?: string | null
  isGenerating?: boolean
  progressLines?: string[]
  error?: string | null
  onGenerate: (seedText: string) => void | Promise<void>
}

/** F-1 style example backgrounds — click fills the seed textarea. */
const EXAMPLE_CHIPS: readonly { label: string; seed: string }[] = [
  {
    label: "UCI 数据科学 · 父母资助",
    seed: "我会去 UC Irvine 读 Data Science 硕士，父母资助学费和生活费，第一年费用约 8 万美元。本科计算机相关专业，毕业后计划回国就业。",
  },
  {
    label: "NYU MSCS · 家庭资金",
    seed: "我被纽约大学（NYU）计算机科学硕士 Fall 2026 录取，本科上海交大软件工程。父母提供学费与生活费，有银行资金证明。毕业后计划回国做软件工程师。",
  },
  {
    label: "USC EE · 奖学金+父母",
    seed: "我申请 F-1 去南加州大学（USC）读电气工程硕士，部分学费有奖学金，其余由父母存款支付。希望在美完成学业后回国发展。",
  },
  {
    label: "UIUC CS 本科",
    seed: "我被伊利诺伊大学香槟分校（UIUC）计算机科学本科录取，父母在国内经营企业，可提供充足资金证明与学费担保。",
  },
]

export function PracticeMaterialsDialog({
  open,
  onOpenChange,
  visaType,
  isGenerating = false,
  progressLines = [],
  error = null,
  onGenerate,
}: PracticeMaterialsDialogProps) {
  const [seedText, setSeedText] = useState("")

  const canSubmit = seedText.trim().length > 0 && !isGenerating

  const handleOpenChange = (nextOpen: boolean) => {
    // Lock dialog closed while generation is in flight (escape / outside / X).
    if (!nextOpen && isGenerating) {
      return
    }
    // Clear draft when the dialog fully closes (not while open during generation).
    if (!nextOpen) {
      setSeedText("")
    }
    onOpenChange(nextOpen)
  }

  const handleGenerate = () => {
    const normalized = seedText.trim()
    if (!normalized || isGenerating) {
      return
    }
    void onGenerate(normalized)
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        className="flex max-h-[86vh] flex-col gap-0 overflow-hidden rounded-2xl p-0 sm:max-w-xl"
        showCloseButton={!isGenerating}
        onEscapeKeyDown={(event) => {
          if (isGenerating) {
            event.preventDefault()
          }
        }}
        onPointerDownOutside={(event) => {
          if (isGenerating) {
            event.preventDefault()
          }
        }}
        onInteractOutside={(event) => {
          if (isGenerating) {
            event.preventDefault()
          }
        }}
      >
        <DialogHeader className="space-y-2 border-b border-border px-6 py-5 text-left">
          <div className="flex flex-wrap items-center gap-2">
            <DialogTitle className="text-lg font-semibold tracking-tight">
              生成练习材料
            </DialogTitle>
            {visaType ? (
              <Badge variant="outline" className="font-mono text-[11px]">
                {visaType}
              </Badge>
            ) : null}
            <Badge
              variant="secondary"
              className="border-amber-200/80 bg-amber-50 text-amber-800 hover:bg-amber-50"
            >
              练习用
            </Badge>
          </div>
          <DialogDescription className="text-sm leading-6 text-muted-foreground">
            用一段话描述你的背景，系统将生成<strong className="font-medium text-foreground">虚构练习材料</strong>
            ，非真实证件。仅供模拟面签练习，请勿当作正式申请材料使用。
          </DialogDescription>
        </DialogHeader>

        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-6 py-5">
          <div className="grid gap-2">
            <Label htmlFor="practice-materials-seed" className="text-sm font-medium">
              你的背景描述
              <span className="ml-1.5 font-normal text-muted-foreground">必填</span>
            </Label>
            <Textarea
              id="practice-materials-seed"
              value={seedText}
              onChange={(event) => setSeedText(event.target.value)}
              disabled={isGenerating}
              placeholder="例如：我会去 UC Irvine 读 Data Science，父母资助，第一年费用约 8 万美元；本科专业、资金来源、毕业后计划等写得越具体，材料越贴近练习场景。"
              className="min-h-36 resize-y text-sm leading-6 md:min-h-40"
              aria-required
            />
            <p className="text-xs leading-5 text-muted-foreground">
              生成依据仅来自上方描述；生成失败不会写入材料库。
            </p>
          </div>

          <div className="grid gap-2">
            <div className="text-xs font-medium text-muted-foreground">快速示例（点击填入）</div>
            <div className="flex flex-wrap gap-2">
              {EXAMPLE_CHIPS.map((chip) => {
                const active = seedText === chip.seed
                return (
                  <button
                    key={chip.label}
                    type="button"
                    disabled={isGenerating}
                    onClick={() => setSeedText(chip.seed)}
                    className={cn(
                      "rounded-full border px-3 py-1.5 text-left text-xs leading-4 transition-colors",
                      "border-border bg-muted/30 text-foreground hover:bg-muted/60",
                      "disabled:pointer-events-none disabled:opacity-50",
                      active && "border-primary/40 bg-primary/5 text-primary",
                    )}
                  >
                    {chip.label}
                  </button>
                )
              })}
            </div>
          </div>

          {progressLines.length > 0 ? (
            <div
              className="max-h-36 overflow-y-auto rounded-xl border border-border bg-muted/20 px-3 py-2.5 font-mono text-[11px] leading-5 text-muted-foreground"
              role="status"
              aria-live="polite"
            >
              {progressLines.map((line, index) => (
                <div key={`${index}-${line}`} className="break-words">
                  {line}
                </div>
              ))}
            </div>
          ) : null}

          {error ? (
            <div
              className="rounded-xl border border-destructive/25 bg-destructive/5 px-3 py-2.5 text-sm leading-5 text-destructive"
              role="alert"
            >
              {error}
            </div>
          ) : null}
        </div>

        <DialogFooter className="border-t border-border px-6 py-4 sm:justify-between">
          <p className="hidden text-xs text-muted-foreground sm:block">
            虚构材料 · 仅练习
          </p>
          <div className="flex w-full flex-col-reverse gap-2 sm:w-auto sm:flex-row">
            <Button
              type="button"
              variant="outline"
              onClick={() => handleOpenChange(false)}
              disabled={isGenerating}
            >
              取消
            </Button>
            <Button type="button" onClick={handleGenerate} disabled={!canSubmit}>
              {isGenerating ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <FileStack className="h-4 w-4" />
              )}
              {isGenerating ? "正在生成…" : "生成"}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
