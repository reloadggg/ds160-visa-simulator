"use client"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Pause, StopCircle, Bell, ChevronDown, RotateCcw } from "lucide-react"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"

interface TopBarProps {
  visaType: string
  sessionTime: string
  isPaused: boolean
  activeTab: string
  onTabChange: (tab: string) => void
  onPause: () => void
  onEndSession: () => void
  onReset: () => void
}

export function TopBar({
  visaType,
  sessionTime,
  isPaused,
  activeTab,
  onTabChange,
  onPause,
  onEndSession,
  onReset,
}: TopBarProps) {
  return (
    <header className="h-16 bg-card border-b border-border px-6 flex items-center relative">
      {/* Left section - Session info */}
      <div className="flex items-center gap-4 flex-shrink-0">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-semibold text-foreground">
            {visaType} 签证模拟
          </h2>
          <Badge className="bg-emerald-100 text-emerald-700 border-emerald-200 hover:bg-emerald-100">
            进行中
          </Badge>
        </div>
      </div>

      {/* Center section - Tabs */}
      <div className="flex-1 flex justify-center">
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
      <div className="flex items-center gap-3 flex-shrink-0">
        {/* Timer */}
        <span className="text-xl font-mono font-semibold text-foreground tracking-wider min-w-[90px]">
          {sessionTime}
        </span>

        {/* Pause button */}
        <Button
          variant="outline"
          size="sm"
          onClick={onPause}
          className="gap-2"
        >
          <Pause className="w-4 h-4" />
          {isPaused ? "继续" : "暂停"}
        </Button>

        {/* End session button */}
        <Button
          variant="outline"
          size="sm"
          onClick={onEndSession}
          className="gap-2 text-destructive border-destructive/30 hover:bg-destructive/10 hover:text-destructive"
        >
          <StopCircle className="w-4 h-4" />
          结束本轮
        </Button>

        {/* Reset/Re-select button */}
        <Button
          variant="outline"
          size="sm"
          onClick={onReset}
          className="gap-2 text-muted-foreground hover:text-foreground"
        >
          <RotateCcw className="w-4 h-4" />
          重新选择
        </Button>

        {/* Notification */}
        <Button variant="ghost" size="icon-sm" className="relative">
          <Bell className="w-4 h-4 text-muted-foreground" />
          <span className="absolute top-1 right-1 w-2 h-2 bg-destructive rounded-full" />
        </Button>

        {/* User avatar */}
        <div className="flex items-center gap-2 pl-3 ml-1 border-l border-border">
          <Avatar className="w-8 h-8">
            <AvatarImage src="https://images.unsplash.com/photo-1472099645785-5658abf4ff4e?w=32&h=32&fit=crop&crop=face" alt="用户头像" />
            <AvatarFallback>AZ</AvatarFallback>
          </Avatar>
          <span className="text-sm font-medium text-foreground">Alex Zhang</span>
          <ChevronDown className="w-4 h-4 text-muted-foreground" />
        </div>
      </div>
    </header>
  )
}
