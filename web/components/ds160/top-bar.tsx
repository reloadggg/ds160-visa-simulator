"use client"

import { useMemo, useState } from "react"

import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Textarea } from "@/components/ui/textarea"
import { APP_VERSION_LABEL } from "@/lib/app-version"
import {
  getDebugMaterialBundleOption,
  getDebugMaterialBundleOptionsForVisaFamily,
  getDefaultDebugMaterialBundleScenarioForVisaFamily,
} from "@/lib/debug-material-bundles"
import {
  Pause,
  StopCircle,
  Bell,
  ChevronDown,
  RotateCcw,
  FlaskConical,
  Camera,
  MoreHorizontal,
  LogOut,
} from "lucide-react"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import type { DebugMaterialBundleScenario, VisaFamily } from "@/lib/api/types"

interface TopBarProps {
  visaType: VisaFamily
  isPaused: boolean
  userName: string
  userAvatarUrl: string
  mockMode?: boolean
  onPause: () => void
  onEndSession: () => void
  onReset: () => void
  onDebugMaterialBundleScenario?: (
    scenario: DebugMaterialBundleScenario,
    seedText?: string,
  ) => void
  isDebugBundleGenerating?: boolean
  onExportConversationImage?: () => void
  onLogout: () => void
}

export function TopBar({
  visaType,
  isPaused,
  userName,
  userAvatarUrl,
  mockMode = false,
  onPause,
  onEndSession,
  onReset,
  onDebugMaterialBundleScenario,
  isDebugBundleGenerating = false,
  onExportConversationImage,
  onLogout,
}: TopBarProps) {
  const [debugBundleDialogOpen, setDebugBundleDialogOpen] = useState(false)
  const [selectedDebugBundleScenario, setSelectedDebugBundleScenario] =
    useState<DebugMaterialBundleScenario>(
      getDefaultDebugMaterialBundleScenarioForVisaFamily(visaType),
    )
  const [materialSeedOverride, setMaterialSeedOverride] = useState("")
  const materialSeedText = materialSeedOverride
  const debugBundleOptions = useMemo(
    () => getDebugMaterialBundleOptionsForVisaFamily(visaType),
    [visaType],
  )
  const activeDebugBundleScenario = debugBundleOptions.some(
    (option) => option.scenario === selectedDebugBundleScenario,
  )
    ? selectedDebugBundleScenario
    : getDefaultDebugMaterialBundleScenarioForVisaFamily(visaType)
  const selectedDebugBundleOption = getDebugMaterialBundleOption(
    activeDebugBundleScenario,
  )
  const displayName = userName.trim() || "User"
  const fallbackInitials =
    displayName
      .split(/[\s_-]+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase())
      .join("") || "U"

  return (
    <header className="flex h-16 min-w-0 items-center border-b border-border bg-card px-4 lg:px-6">
      {/* Left section - Session info */}
      <div className="min-w-0 flex-1 lg:flex-none">
        <div className="flex min-w-0 items-center gap-2 lg:gap-3">
          <h2 className="truncate text-base font-semibold text-foreground lg:text-lg">
            {visaType} 签证模拟
          </h2>
          <Badge className="bg-emerald-100 text-emerald-700 border-emerald-200 hover:bg-emerald-100">
            进行中
          </Badge>
          {mockMode ? (
            <Badge
              variant="outline"
              className="border-amber-300 bg-amber-50 text-amber-800 hover:bg-amber-50"
            >
              Mock 模式
            </Badge>
          ) : null}
          <Badge
            variant="outline"
            className="hidden font-mono text-[11px] text-muted-foreground sm:inline-flex"
          >
            {APP_VERSION_LABEL}
          </Badge>
        </div>
      </div>

      <div className="hidden min-w-0 flex-1 justify-center text-sm text-muted-foreground lg:flex">
        模拟面签中 · 教练提示已合并在右侧分析面板
      </div>

      {/* Right section - Timer and controls */}
      <div className="flex min-w-0 shrink-0 items-center gap-2 lg:gap-3">
        <div className="hidden items-center gap-2 sm:flex lg:gap-3">
          <Button
            variant="outline"
            size="sm"
            onClick={onPause}
            className="gap-2"
          >
            <Pause className="h-4 w-4" />
            <span className="hidden sm:inline">
              {isPaused ? "继续" : "暂停"}
            </span>
          </Button>

          <Button
            variant="outline"
            size="sm"
            onClick={onEndSession}
            className="gap-2 border-destructive/30 text-destructive hover:bg-destructive/10 hover:text-destructive"
          >
            <StopCircle className="h-4 w-4" />
            <span className="hidden sm:inline">结束本轮</span>
          </Button>
        </div>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="icon-sm" aria-label="更多操作">
              <MoreHorizontal className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-48">
            <div className="lg:hidden">
              <DropdownMenuItem disabled>模拟面签</DropdownMenuItem>
              <DropdownMenuSeparator />
            </div>
            <div className="sm:hidden">
              <DropdownMenuItem onClick={onPause}>
                <Pause className="h-4 w-4" />
                {isPaused ? "继续" : "暂停"}
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={onEndSession}
                className="text-destructive focus:bg-destructive/10 focus:text-destructive"
              >
                <StopCircle className="h-4 w-4" />
                结束本轮
              </DropdownMenuItem>
              <DropdownMenuSeparator />
            </div>
            {onDebugMaterialBundleScenario ? (
              <>
                <DropdownMenuItem
                  onSelect={() => {
                    setDebugBundleDialogOpen(true)
                  }}
                  disabled={isDebugBundleGenerating}
                >
                  <FlaskConical
                    className={
                      isDebugBundleGenerating
                        ? "h-4 w-4 animate-pulse"
                        : "h-4 w-4"
                    }
                  />
                  {isDebugBundleGenerating ? "材料包生成中" : "生成材料包..."}
                </DropdownMenuItem>
                <DropdownMenuSeparator />
              </>
            ) : null}
            {onExportConversationImage ? (
              <DropdownMenuItem onClick={onExportConversationImage}>
                <Camera className="h-4 w-4" />
                导出长截图
              </DropdownMenuItem>
            ) : null}
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={onReset}>
              <RotateCcw className="h-4 w-4" />
              重新选择
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={onLogout}>
              <LogOut className="h-4 w-4" />
              退出当前 Key
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>

        <Button
          variant="ghost"
          size="icon-sm"
          className="relative hidden sm:inline-flex"
        >
          <Bell className="h-4 w-4 text-muted-foreground" />
          <span className="absolute right-1 top-1 h-2 w-2 rounded-full bg-destructive" />
        </Button>

        <div className="hidden min-w-0 items-center gap-2 border-l border-border pl-3 lg:flex">
          <Avatar className="h-8 w-8 shrink-0">
            <AvatarImage src={userAvatarUrl} alt={`${displayName} 的头像`} />
            <AvatarFallback>{fallbackInitials}</AvatarFallback>
          </Avatar>
          <span className="max-w-28 truncate text-sm font-medium text-foreground">
            {displayName}
          </span>
          <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
        </div>
      </div>
      {onDebugMaterialBundleScenario ? (
        <Dialog
          open={debugBundleDialogOpen}
          onOpenChange={setDebugBundleDialogOpen}
        >
          <DialogContent className="max-h-[86vh] overflow-y-auto rounded-2xl sm:max-w-xl">
            <DialogHeader>
              <DialogTitle>生成材料包</DialogTitle>
              <DialogDescription>
                根据你填写的材料生成依据生成材料，写入当前材料库。
              </DialogDescription>
            </DialogHeader>
            <RadioGroup
              value={activeDebugBundleScenario}
              onValueChange={(value) =>
                setSelectedDebugBundleScenario(
                  value as DebugMaterialBundleScenario,
                )
              }
              className="gap-2"
            >
              {debugBundleOptions.map((option) => (
                <label
                  key={option.scenario}
                  className={cn(
                    "flex cursor-pointer items-start gap-3 rounded-xl border border-border px-3 py-3 transition-colors hover:bg-muted/40",
                    activeDebugBundleScenario === option.scenario &&
                      "border-primary/50 bg-primary/5",
                  )}
                >
                  <RadioGroupItem value={option.scenario} className="mt-0.5" />
                  <span className="min-w-0">
                    <span className="block text-sm font-medium text-foreground">
                      {option.label}
                    </span>
                    <span className="mt-1 block break-words text-xs leading-5 text-muted-foreground">
                      {option.description}
                    </span>
                  </span>
                </label>
              ))}
            </RadioGroup>
            <div className="grid gap-2">
              <label className="text-sm font-medium text-foreground" htmlFor="topbar-material-seed">
                材料生成依据
              </label>
              <Textarea
                id="topbar-material-seed"
                value={materialSeedText}
                onChange={(event) => setMaterialSeedOverride(event.target.value)}
                placeholder="例如：我会去 NYU 读 MSCS，父母资助，第一年费用约 9 万美元。"
                className="min-h-24 resize-y"
              />
              <p className="text-xs leading-5 text-muted-foreground">
                这里是唯一生成依据，必填；生成失败不会写入材料。
              </p>
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setDebugBundleDialogOpen(false)}
              >
                取消
              </Button>
              <Button
                onClick={() => {
                  onDebugMaterialBundleScenario(
                    selectedDebugBundleOption.scenario,
                    materialSeedText,
                  )
                  setDebugBundleDialogOpen(false)
                }}
                disabled={isDebugBundleGenerating || !materialSeedText.trim()}
              >
                <FlaskConical
                  className={
                    isDebugBundleGenerating
                      ? "h-4 w-4 animate-pulse"
                      : "h-4 w-4"
                  }
                />
                {isDebugBundleGenerating
                  ? "正在生成"
                  : `生成${selectedDebugBundleOption.shortLabel}`}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      ) : null}
    </header>
  )
}
