"use client"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { RotateCcw, Copy, Trash2, Settings2, Download, FlaskConical, Camera, RefreshCw } from "lucide-react"
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
import type { ModelListItem, UserModelConfig } from "@/lib/api/types"

interface SettingsPanelProps {
  mockMode: boolean
  apiBaseUrl: string
  sessionId: string | null
  historyCount: number
  feedback?: string | null
  userModelConfig: UserModelConfig
  availableModels: ModelListItem[]
  isLoadingModels: boolean
  modelConfigError?: string | null
  onUserModelConfigChange: (config: UserModelConfig) => void
  onFetchUserModels: () => void
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
  userModelConfig,
  availableModels,
  isLoadingModels,
  modelConfigError,
  onUserModelConfigChange,
  onFetchUserModels,
  onCopySessionId,
  onExportSession,
  onExportConversationImage,
  onDebugFillCurrentGap,
  onResetCurrentSession,
  onClearHistory,
}: SettingsPanelProps) {
  const updateModelConfig = (patch: Partial<UserModelConfig>) => {
    onUserModelConfigChange({
      ...userModelConfig,
      ...patch,
    })
  }

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
            <CardTitle className="text-base">自带模型配置</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 px-5">
            <div className="flex items-center justify-between gap-4 rounded-xl border border-border bg-muted/20 px-4 py-3">
              <div>
                <div className="text-sm font-medium text-foreground">启用前端模型设置</div>
                <div className="mt-1 text-xs leading-5 text-muted-foreground">
                  需要后端开启 ALLOW_USER_MODEL_CONFIG；聊天、材料和报告仍保存在后端数据库。
                </div>
              </div>
              <Switch
                checked={userModelConfig.enabled}
                onCheckedChange={(checked) => updateModelConfig({ enabled: checked })}
                aria-label="启用自带模型配置"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="model-base-url">Base URL</Label>
              <Input
                id="model-base-url"
                value={userModelConfig.baseUrl}
                onChange={(event) => updateModelConfig({ baseUrl: event.target.value })}
                placeholder="https://api.openai.com/v1"
                autoComplete="off"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="model-api-key">API Key</Label>
              <Input
                id="model-api-key"
                type="password"
                value={userModelConfig.apiKey}
                onChange={(event) => updateModelConfig({ apiKey: event.target.value })}
                placeholder="sk-..."
                autoComplete="off"
              />
              <div className="text-xs leading-5 text-muted-foreground">
                API Key 只保存在当前页面内存中，刷新页面后需要重新填写。
              </div>
            </div>

            <div className="grid gap-3 md:grid-cols-[1fr_auto]">
              <div className="space-y-2">
                <Label htmlFor="model-name">Model</Label>
                {availableModels.length ? (
                  <Select
                    value={userModelConfig.model}
                    onValueChange={(value) => updateModelConfig({ model: value })}
                  >
                    <SelectTrigger id="model-name" className="w-full">
                      <SelectValue placeholder="选择模型" />
                    </SelectTrigger>
                    <SelectContent>
                      {availableModels.map((model) => (
                        <SelectItem key={model.id} value={model.id}>
                          {model.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : (
                  <Input
                    id="model-name"
                    value={userModelConfig.model}
                    onChange={(event) => updateModelConfig({ model: event.target.value })}
                    placeholder="gpt-4.1-mini"
                    autoComplete="off"
                  />
                )}
              </div>
              <Button
                type="button"
                variant="outline"
                className="self-end"
                onClick={onFetchUserModels}
                disabled={isLoadingModels}
              >
                <RefreshCw className={isLoadingModels ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
                拉取模型
              </Button>
            </div>

            <div className="flex items-center justify-between gap-4 rounded-xl border border-border bg-muted/20 px-4 py-3">
              <div>
                <div className="text-sm font-medium text-foreground">事件式流式输出</div>
                <div className="mt-1 text-xs leading-5 text-muted-foreground">
                  先流式显示处理阶段，最终回复仍由后端结构化评估完成后返回。
                </div>
              </div>
              <Switch
                checked={userModelConfig.streamingEnabled}
                onCheckedChange={(checked) => updateModelConfig({ streamingEnabled: checked })}
                aria-label="启用事件式流式输出"
              />
            </div>

            {modelConfigError ? (
              <div className="rounded-xl border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {modelConfigError}
              </div>
            ) : null}
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
