"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Spinner } from "@/components/ui/spinner"
import Image from "next/image"
import { cn } from "@/lib/utils"
import { VISA_FAMILIES, type AccessKeyQuota, type VisaFamily } from "@/lib/api/types"
import { GraduationCap, Users, Briefcase, Building2 } from "lucide-react"

interface VisaSelectorProps {
  onSelect: (visaType: VisaFamily) => void
  isLoading: boolean
  error?: string | null
  mockMode?: boolean
  embedded?: boolean
  accessKeyQuota?: AccessKeyQuota | null
}

const visaIcons: Record<VisaFamily, React.ComponentType<{ className?: string }>> = {
  "F-1": GraduationCap,
  "J-1": Users,
  "B-1/B-2": Briefcase,
  "H-1B": Building2,
}

export function VisaSelector({
  onSelect,
  isLoading,
  error,
  mockMode = false,
  embedded = false,
  accessKeyQuota = null,
}: VisaSelectorProps) {
  const [selectedVisa, setSelectedVisa] = useState<VisaFamily | null>(null)
  const [pendingVisa, setPendingVisa] = useState<VisaFamily | null>(null)

  const quotaBlocksSession =
    Boolean(accessKeyQuota) && !accessKeyQuota?.can_create_session
  const quotaLabel = accessKeyQuota
    ? `剩余 ${accessKeyQuota.remaining_uses}/${accessKeyQuota.usage_limit} 次`
    : mockMode
      ? "Mock 模式不会消耗额度"
      : "当前登录方式不限制创建额度"

  const handleStart = () => {
    if (selectedVisa && !quotaBlocksSession) {
      setPendingVisa(selectedVisa)
    }
  }

  const handleConfirmStart = () => {
    if (pendingVisa) {
      onSelect(pendingVisa)
      setPendingVisa(null)
    }
  }

  return (
    <div
      className={cn(
        "bg-background flex justify-center overflow-y-auto p-4 md:p-6",
        embedded
          ? "h-full min-h-0 items-start pb-[calc(5.5rem+env(safe-area-inset-bottom))] sm:items-center lg:pb-6"
          : "min-h-[100dvh] items-center",
      )}
    >
      <div className="w-full max-w-2xl">
        <div className="text-center mb-8">
          <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center overflow-hidden rounded-2xl bg-primary/10">
            <Image src="/brand-icon.svg" alt="DS-160 模拟面签" width={64} height={64} className="h-16 w-16" />
          </div>
          <h1 className="text-2xl font-semibold text-foreground mb-2">
            DS-160 模拟面签
          </h1>
          <p className="text-muted-foreground">
            选择您要模拟的签证类型，确认后会创建一个新的面签会话
          </p>
          <div className="mt-3 inline-flex rounded-full border border-blue-100 bg-blue-50 px-3 py-1 text-xs font-medium text-blue-700 dark:border-cyan-200/15 dark:bg-cyan-200/[0.06] dark:text-cyan-100/80">
            创建额度：{quotaLabel}
          </div>
          {mockMode && (
            <div className="mt-3 inline-flex items-center rounded-full border border-amber-300 bg-amber-50 px-3 py-1 text-xs font-medium text-amber-800 dark:border-amber-300/25 dark:bg-amber-300/10 dark:text-amber-100">
              开发模式：当前使用 Mock 数据
            </div>
          )}
        </div>

        <Card className="shadow-lg">
          <CardHeader>
            <CardTitle>选择签证类型</CardTitle>
            <CardDescription>
              请选择您计划申请的签证类别
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 p-4 md:p-6">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {VISA_FAMILIES.map((visa) => {
                const Icon = visaIcons[visa.value]
                const isSelected = selectedVisa === visa.value
                return (
                  <button
                    key={visa.value}
                    onClick={() => setSelectedVisa(visa.value)}
                    disabled={isLoading}
                    className={cn(
                      "p-4 rounded-xl border-2 text-left transition-all hover:shadow-md",
                      isSelected
                        ? "border-primary bg-primary/5"
                        : "border-border hover:border-primary/30"
                    )}
                  >
                    <div className="flex items-start gap-3">
                      <div
                        className={cn(
                          "w-10 h-10 rounded-lg flex items-center justify-center shrink-0",
                          isSelected ? "bg-primary/10" : "bg-muted"
                        )}
                      >
                        <Icon
                          className={cn(
                            "w-5 h-5",
                            isSelected ? "text-primary" : "text-muted-foreground"
                          )}
                        />
                      </div>
                      <div>
                        <div
                          className={cn(
                            "font-medium",
                            isSelected ? "text-primary" : "text-foreground"
                          )}
                        >
                          {visa.label}
                        </div>
                        <div className="text-sm text-muted-foreground">
                          {visa.description}
                        </div>
                      </div>
                    </div>
                  </button>
                )
              })}
            </div>

            <div className="pt-4">
              {error && (
                <div className="mb-3 rounded-lg border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                  {error}
                </div>
              )}
              <Button
                onClick={handleStart}
                disabled={!selectedVisa || isLoading || quotaBlocksSession}
                className="w-full"
                size="lg"
              >
                {isLoading ? (
                  <>
                    <Spinner className="mr-2" />
                    正在初始化会话...
                  </>
                ) : (
                  quotaBlocksSession ? "创建额度已用尽" : "开始模拟面签"
                )}
              </Button>
              {quotaBlocksSession ? (
                <div className="mt-3 rounded-lg border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                  这个访问 key 已不能再创建新会话，请联系管理员增加额度或使用已有历史会话。
                </div>
              ) : null}
            </div>
          </CardContent>
        </Card>
      </div>

      <AlertDialog open={pendingVisa !== null} onOpenChange={(open) => !open && setPendingVisa(null)}>
        <AlertDialogContent className="rounded-2xl">
          <AlertDialogHeader>
            <AlertDialogTitle>确认创建新的面签会话？</AlertDialogTitle>
            <AlertDialogDescription>
              {accessKeyQuota
                ? `创建后会消耗 1 次访问 key 创建额度。当前已用 ${accessKeyQuota.usage_count}/${accessKeyQuota.usage_limit} 次，创建后剩余 ${Math.max(0, accessKeyQuota.remaining_uses - 1)} 次。`
                : mockMode
                  ? "这会创建一个 Mock 会话，不会消耗访问 key 额度。"
                  : "这会创建一个新的面签会话。"}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="rounded-xl">取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirmStart}
              className="rounded-xl"
            >
              确认创建
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
