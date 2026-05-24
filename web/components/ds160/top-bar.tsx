"use client"

import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { DEBUG_MATERIAL_BUNDLE_OPTIONS } from "@/lib/debug-material-bundles"
import { Pause, StopCircle, Bell, ChevronDown, RotateCcw, FlaskConical, Camera, MoreHorizontal } from "lucide-react"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import type { DebugMaterialBundleScenario } from "@/lib/api/types"

interface TopBarProps {
  visaType: string
  sessionTime: string
  isPaused: boolean
  activeTab: string
  userName: string
  userAvatarUrl: string
  mockMode?: boolean
  onTabChange: (tab: string) => void
  onPause: () => void
  onEndSession: () => void
  onReset: () => void
  onDebugMaterialBundleScenario?: (scenario: DebugMaterialBundleScenario) => void
  isDebugBundleGenerating?: boolean
  onExportConversationImage?: () => void
}

export function TopBar({
  visaType,
  sessionTime,
  isPaused,
  activeTab,
  userName,
  userAvatarUrl,
  mockMode = false,
  onTabChange,
  onPause,
  onEndSession,
  onReset,
  onDebugMaterialBundleScenario,
  isDebugBundleGenerating = false,
  onExportConversationImage,
}: TopBarProps) {
  const displayName = userName.trim() || "User"
  const fallbackInitials = displayName
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
        </div>
      </div>

      {/* Center section - Tabs */}
      <div className="hidden min-w-0 flex-1 justify-center lg:flex">
        <Tabs value={activeTab} onValueChange={onTabChange}>
          <TabsList className="bg-muted/50">
            <TabsTrigger value="simulation" className="px-6">
              模拟面签
            </TabsTrigger>
            <TabsTrigger value="coach" className="px-6">
              教练视图
            </TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      {/* Right section - Timer and controls */}
      <div className="flex min-w-0 shrink-0 items-center gap-2 lg:gap-3">
        {/* Timer */}
        <span className="min-w-[70px] text-right font-mono text-base font-semibold tracking-wider text-foreground sm:min-w-[82px] lg:text-xl">
          {sessionTime}
        </span>

        <div className="hidden items-center gap-2 sm:flex lg:gap-3">
          <Button
            variant="outline"
            size="sm"
            onClick={onPause}
            className="gap-2"
          >
            <Pause className="h-4 w-4" />
            <span className="hidden sm:inline">{isPaused ? "继续" : "暂停"}</span>
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
              <DropdownMenuItem onClick={() => onTabChange("simulation")} className={cn(activeTab === "simulation" && "bg-primary/10 text-primary")}>
                模拟面签
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => onTabChange("coach")} className={cn(activeTab === "coach" && "bg-primary/10 text-primary")}>
                教练视图
              </DropdownMenuItem>
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
                <DropdownMenuSub>
                  <DropdownMenuSubTrigger>
                    <FlaskConical className={isDebugBundleGenerating ? "h-4 w-4 animate-pulse" : "h-4 w-4"} />
                    材料包
                  </DropdownMenuSubTrigger>
                  <DropdownMenuSubContent className="w-56">
                    <DropdownMenuLabel className="text-xs text-muted-foreground">
                      {isDebugBundleGenerating ? "正在生成" : "选择场景"}
                    </DropdownMenuLabel>
                    {DEBUG_MATERIAL_BUNDLE_OPTIONS.map((option) => (
                      <DropdownMenuItem
                        key={option.scenario}
                        onClick={() => onDebugMaterialBundleScenario(option.scenario)}
                        disabled={isDebugBundleGenerating}
                      >
                        <span className="min-w-0 truncate">{option.label}</span>
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuSubContent>
                </DropdownMenuSub>
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
          </DropdownMenuContent>
        </DropdownMenu>

        <Button variant="ghost" size="icon-sm" className="relative hidden sm:inline-flex">
          <Bell className="h-4 w-4 text-muted-foreground" />
          <span className="absolute right-1 top-1 h-2 w-2 rounded-full bg-destructive" />
        </Button>

        <div className="hidden min-w-0 items-center gap-2 border-l border-border pl-3 lg:flex">
          <Avatar className="h-8 w-8 shrink-0">
            <AvatarImage src={userAvatarUrl} alt={`${displayName} 的头像`} />
            <AvatarFallback>{fallbackInitials}</AvatarFallback>
          </Avatar>
          <span className="max-w-28 truncate text-sm font-medium text-foreground">{displayName}</span>
          <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
        </div>
      </div>
    </header>
  )
}
