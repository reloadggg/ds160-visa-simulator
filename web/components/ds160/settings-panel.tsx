"use client"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import { RotateCcw, Copy, Trash2, Settings2, Download, FlaskConical, Camera } from "lucide-react"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"

interface SettingsPanelProps {
  mockMode: boolean
  apiBaseUrl: string
  sessionId: string | null
  historyCount: number
  feedback?: string | null
  onCopySessionId: () => void
  onExportSession: () => void
  onExportConversationImage: () => void
  onDebugFillCurrentGap: () => void
  onResetCurrentSession: () => void
  onClearHistory: () => void
}

export function SettingsPanel({
  mockMode,
  apiBaseUrl,
  sessionId,
  historyCount,
  feedback,
  onCopySessionId,
  onExportSession,
  onExportConversationImage,
  onDebugFillCurrentGap,
  onResetCurrentSession,
  onClearHistory,
}: SettingsPanelProps) {
  return (
    <ScrollArea className="h-full">
      <div className="space-y-4 p-4">
        <Card className="py-4">
          <CardHeader className="px-5 pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Settings2 className="h-4 w-4 text-primary" />
              运行配置
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 px-5">
            <div className="flex items-center justify-between rounded-xl border border-border bg-muted/20 px-4 py-3">
              <div>
                <div className="text-sm font-medium text-foreground">Mock 模式</div>
                <div className="mt-1 text-xs text-muted-foreground">
                  通过 `NEXT_PUBLIC_MOCK` 控制，运行时不可直接切换。
                </div>
              </div>
              <Badge variant="outline">{mockMode ? "已开启" : "已关闭"}</Badge>
            </div>

            <div className="rounded-xl border border-border bg-muted/20 px-4 py-3">
              <div className="text-sm font-medium text-foreground">API 地址</div>
              <div className="mt-1 break-all text-xs leading-6 text-muted-foreground">
                {apiBaseUrl || "未配置"}
              </div>
            </div>

            <div className="rounded-xl border border-border bg-muted/20 px-4 py-3">
              <div className="text-sm font-medium text-foreground">当前会话 ID</div>
              <div className="mt-1 break-all text-xs leading-6 text-muted-foreground">
                {sessionId ?? "当前没有进行中的会话"}
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="py-4">
          <CardHeader className="px-5 pb-3">
            <CardTitle className="text-base">可执行操作</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 px-5">
            <Button
              variant="outline"
              className="w-full justify-start"
              onClick={onCopySessionId}
            >
              <Copy className="h-4 w-4" />
              复制当前会话 ID
            </Button>
            <Button
              variant="outline"
              className="w-full justify-start"
              onClick={onExportSession}
              disabled={!sessionId}
            >
              <Download className="h-4 w-4" />
              导出当前会话 JSON
            </Button>
            <Button
              variant="outline"
              className="w-full justify-start"
              onClick={onExportConversationImage}
              disabled={!sessionId}
            >
              <Camera className="h-4 w-4" />
              导出完整会话长截图
            </Button>
            <Button
              variant="outline"
              className="w-full justify-start border-amber-300 bg-amber-50 text-amber-800 hover:bg-amber-100 hover:text-amber-900"
              onClick={onDebugFillCurrentGap}
              disabled={!sessionId}
            >
              <FlaskConical className="h-4 w-4" />
              调试：一键补充当前缺口材料
            </Button>
            <Button
              variant="outline"
              className="w-full justify-start"
              onClick={onResetCurrentSession}
            >
              <RotateCcw className="h-4 w-4" />
              清空当前会话并重新开始
            </Button>

            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button
                  variant="outline"
                  className="w-full justify-start text-destructive hover:text-destructive"
                >
                  <Trash2 className="h-4 w-4" />
                  清空本地历史记录（{historyCount}）
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent className="rounded-2xl">
                <AlertDialogHeader>
                  <AlertDialogTitle>确认清空历史记录？</AlertDialogTitle>
                  <AlertDialogDescription>
                    此操作将永久删除保存在本浏览器中的所有历史会话摘要、消息记录和归档材料，且无法撤销。
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel className="rounded-xl">取消</AlertDialogCancel>
                  <AlertDialogAction
                    onClick={onClearHistory}
                    className="rounded-xl bg-destructive text-destructive-foreground hover:bg-destructive/90"
                  >
                    确认删除
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>

            {feedback ? (
              <div className="rounded-xl border border-border bg-muted/20 px-3 py-2 text-sm text-muted-foreground">
                {feedback}
              </div>
            ) : null}
          </CardContent>
        </Card>
      </div>
    </ScrollArea>
  )
}
