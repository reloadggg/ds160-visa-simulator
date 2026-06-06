"use client"

import { type ChangeEvent, useMemo, useRef, useState } from "react"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Textarea } from "@/components/ui/textarea"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { PROJECT_INFO } from "@/lib/project-info"
import { appVersionDetailLabel } from "@/lib/app-version"
import {
  DEFAULT_DEBUG_MATERIAL_BUNDLE_SCENARIO,
  getDebugMaterialBundleOptionsForVisaFamily,
  getDefaultDebugMaterialBundleScenarioForVisaFamily,
  getDebugMaterialBundleOption,
} from "@/lib/debug-material-bundles"
import {
  RotateCcw,
  Copy,
  Trash2,
  Settings2,
  Download,
  FlaskConical,
  Camera,
  RefreshCw,
  Upload,
  Github,
  ExternalLink,
  LogOut,
} from "lucide-react"
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
import type {
  AccessKeyQuota,
  DebugMaterialBundleScenario,
  MaterialPackageArchiveItem,
  RagStatus,
  RagUploadMetadata,
  VisaFamily,
} from "@/lib/api/types"

const VISA_FAMILY_OPTIONS = [
  { value: "f1", label: "F-1" },
  { value: "j1", label: "J-1" },
  { value: "b1_b2", label: "B-1/B-2" },
  { value: "h1b", label: "H-1B" },
]

const EMPTY_RAG_UPLOAD_FORM = {
  title: "",
  url: "",
  visaFamily: "",
  country: "",
  post: "",
  notes: "",
}


function isValidatedMaterialPackage(item: MaterialPackageArchiveItem): boolean {
  return (
    item.validation_status === "passed" ||
    item.archive_source_reason === "validated_f1_demo_material_package" ||
    item.demo_template_id === "f1_parent_sponsored_consistent_v1"
  )
}

function isImportableMaterialPackage(item: MaterialPackageArchiveItem): boolean {
  return item.status === "ready"
}

function materialPackageBadgeLabel(item: MaterialPackageArchiveItem): string {
  if (isValidatedMaterialPackage(item)) {
    return item.validation_status === "passed" ? "validated" : "template"
  }
  return item.validation_status ?? "archive"
}

function materialPackageMetaSummary(item: MaterialPackageArchiveItem): string {
  const parts = [
    item.visa_family ? item.visa_family.toUpperCase() : null,
    item.intent,
    item.demo_template_id ? `模板 ${item.demo_template_id}` : null,
    item.source_validation_session_id
      ? `验证会话 ${item.source_validation_session_id}`
      : item.source_session_id
        ? `来源 ${item.source_session_id}`
        : null,
  ].filter(Boolean)
  return parts.length ? parts.join(" / ") : "未提供验证元数据"
}

interface SettingsPanelProps {
  mockMode: boolean
  apiBaseUrl: string
  sessionId: string | null
  visaType: VisaFamily | null
  historyCount: number
  feedback?: string | null
  isDebugBundleGenerating?: boolean
  debugBundleProgress?: string[]
  materialPackages?: MaterialPackageArchiveItem[]
  isLoadingMaterialPackages?: boolean
  isImportingMaterialPackage?: boolean
  modelConfigError?: string | null
  ragStatus: RagStatus | null
  isLoadingRagStatus: boolean
  isUploadingRagFile: boolean
  ragError?: string | null
  onUploadRagFile: (file: File, metadata?: RagUploadMetadata) => void
  onRefreshRagStatus: () => void
  onCopySessionId: () => void
  onExportSession: () => void
  onExportConversationImage: () => void
  onDebugMaterialBundleScenario: (
    scenario: DebugMaterialBundleScenario,
    seedText?: string,
  ) => void
  onRefreshMaterialPackages: () => void
  onImportMaterialPackage: (packageId: string) => void
  onResetCurrentSession: () => void
  onClearHistory: () => void
  onLogout: () => void
  accessKeyQuota?: AccessKeyQuota | null
  showGithub?: boolean
  showUserModelConfig?: boolean
  showRagStatus?: boolean
  showDebugTools?: boolean
}

export function SettingsPanel({
  mockMode,
  apiBaseUrl,
  sessionId,
  visaType,
  historyCount,
  feedback,
  isDebugBundleGenerating = false,
  debugBundleProgress = [],
  materialPackages = [],
  isLoadingMaterialPackages = false,
  isImportingMaterialPackage = false,
  modelConfigError,
  ragStatus,
  isLoadingRagStatus,
  isUploadingRagFile,
  ragError,
  onUploadRagFile,
  onRefreshRagStatus,
  onCopySessionId,
  onExportSession,
  onExportConversationImage,
  onDebugMaterialBundleScenario,
  onRefreshMaterialPackages,
  onImportMaterialPackage,
  onResetCurrentSession,
  onClearHistory,
  onLogout,
  accessKeyQuota = null,
  showGithub = true,
  showUserModelConfig = true,
  showRagStatus = true,
  showDebugTools = true,
}: SettingsPanelProps) {
  const ragFileInputRef = useRef<HTMLInputElement | null>(null)
  const [ragUploadFile, setRagUploadFile] = useState<File | null>(null)
  const [ragUploadForm, setRagUploadForm] = useState(EMPTY_RAG_UPLOAD_FORM)
  const [selectedDebugBundleScenario, setSelectedDebugBundleScenario] =
    useState<DebugMaterialBundleScenario>(
      DEFAULT_DEBUG_MATERIAL_BUNDLE_SCENARIO,
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
  const sortedMaterialPackages = useMemo(
    () =>
      [...materialPackages].sort((left, right) => {
        const validatedDelta = Number(isValidatedMaterialPackage(right)) - Number(isValidatedMaterialPackage(left))
        if (validatedDelta !== 0) {
          return validatedDelta
        }
        const readyDelta = Number(isImportableMaterialPackage(right)) - Number(isImportableMaterialPackage(left))
        if (readyDelta !== 0) {
          return readyDelta
        }
        return left.label.localeCompare(right.label, "zh-Hans-CN")
      }),
    [materialPackages],
  )
  const updateRagUploadForm = (
    patch: Partial<typeof EMPTY_RAG_UPLOAD_FORM>,
  ) => {
    setRagUploadForm((current) => ({
      ...current,
      ...patch,
    }))
  }
  const handleRagFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0] ?? null
    event.target.value = ""
    setRagUploadFile(file)
    if (file && !ragUploadForm.title.trim()) {
      updateRagUploadForm({ title: file.name })
    }
  }
  const handleRagUploadSubmit = () => {
    if (!ragUploadFile) {
      return
    }
    onUploadRagFile(ragUploadFile, {
      title: ragUploadForm.title,
      url: ragUploadForm.url,
      visa_family: ragUploadForm.visaFamily,
      country: ragUploadForm.country,
      post: ragUploadForm.post,
      section_path: ragUploadForm.notes,
    })
    setRagUploadFile(null)
    setRagUploadForm(EMPTY_RAG_UPLOAD_FORM)
  }
  const handleDebugBundleSubmit = () => {
    onDebugMaterialBundleScenario(activeDebugBundleScenario, materialSeedText)
  }
  const handleImportPackage = (packageId: string) => {
    if (!packageId) {
      return
    }
    onImportMaterialPackage(packageId)
  }

  const ragStatusLabel = (() => {
    if (!ragStatus) {
      return "未知"
    }
    if (ragStatus.status === "available") {
      return "可用"
    }
    if (ragStatus.status === "index_empty") {
      return "索引为空"
    }
    if (ragStatus.skip_reason === "disabled") {
      return "已关闭"
    }
    if (ragStatus.skip_reason === "missing_siliconflow_api_key") {
      return "未配置服务密钥"
    }
    if (ragStatus.skip_reason === "mock_mode") {
      return "Mock 模式"
    }
    return "不可用"
  })()

  const indexedChunkCount =
    ragStatus?.collections.reduce(
      (total, collection) => total + collection.count,
      0,
    ) ?? 0
  const canUploadRagFile = Boolean(
    ragStatus &&
    ragStatus.enabled &&
    (ragStatus.ready || ragStatus.skip_reason === "index_empty"),
  )

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
                <div className="text-sm font-medium text-foreground">
                  Mock 模式
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  通过 `NEXT_PUBLIC_MOCK` 控制，运行时不可直接切换。
                </div>
              </div>
              <Badge variant="outline">{mockMode ? "已开启" : "已关闭"}</Badge>
            </div>

            <div className="rounded-xl border border-border bg-muted/20 px-4 py-3">
              <div className="text-sm font-medium text-foreground">
                前端版本
              </div>
              <div className="mt-1 break-all font-mono text-xs leading-6 text-muted-foreground">
                {appVersionDetailLabel()}
              </div>
            </div>

            <div className="rounded-xl border border-border bg-muted/20 px-4 py-3">
              <div className="text-sm font-medium text-foreground">
                API 地址
              </div>
              <div className="mt-1 break-all text-xs leading-6 text-muted-foreground">
                {apiBaseUrl || "未配置"}
              </div>
            </div>

            <div className="rounded-xl border border-border bg-muted/20 px-4 py-3">
              <div className="text-sm font-medium text-foreground">
                当前会话 ID
              </div>
              <div className="mt-1 break-all text-xs leading-6 text-muted-foreground">
                {sessionId ?? "当前没有进行中的会话"}
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="py-4">
          <CardHeader className="px-5 pb-3">
            <CardTitle className="text-base">当前授权 Key</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 px-5">
            <div className="rounded-xl border border-border bg-muted/20 px-4 py-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-sm font-medium text-foreground">
                    {accessKeyQuota
                      ? accessKeyQuota.label || accessKeyQuota.key_id
                      : "当前登录方式"}
                  </div>
                  <div className="mt-1 text-xs leading-5 text-muted-foreground">
                    {accessKeyQuota
                      ? `Key ID：${accessKeyQuota.key_id} · 剩余 ${accessKeyQuota.remaining_uses}/${accessKeyQuota.usage_limit} 次创建额度`
                      : "当前登录方式不限制创建额度。"}
                  </div>
                </div>
                {accessKeyQuota ? (
                  <Badge
                    variant={
                      accessKeyQuota.can_create_session
                        ? "outline"
                        : "destructive"
                    }
                  >
                    {accessKeyQuota.can_create_session ? "可创建" : "额度已用尽"}
                  </Badge>
                ) : null}
              </div>
            </div>

            <Button
              variant="outline"
              className="w-full justify-center gap-2 border-destructive/30 text-destructive hover:bg-destructive/10 hover:text-destructive"
              onClick={onLogout}
            >
              <LogOut className="h-4 w-4" />
              退出当前 Key / 切换账号
            </Button>
          </CardContent>
        </Card>

        {showRagStatus ? (
          <Card className="py-4">
            <CardHeader className="px-5 pb-3">
              <CardTitle className="text-base">知识库 / RAG 状态</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 px-5">
              <div className="rounded-xl border border-border bg-muted/20 px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="text-sm font-medium text-foreground">
                      服务端知识库
                    </div>
                    <div className="mt-1 text-xs leading-5 text-muted-foreground">
                      RAG
                      使用后端通用知识库和向量检索配置，不依赖用户侧模型凭据。
                    </div>
                  </div>
                  <Badge variant={ragStatus?.ready ? "default" : "outline"}>
                    {isLoadingRagStatus ? "检查中" : ragStatusLabel}
                  </Badge>
                </div>

                <div className="mt-3 grid gap-2 text-xs leading-5 text-muted-foreground">
                  <div>Embedding：{ragStatus?.embedding_model ?? "未获取"}</div>
                  <div>Rerank：{ragStatus?.rerank_model ?? "未获取"}</div>
                  <div>索引版本：{ragStatus?.index_version ?? "未获取"}</div>
                  <div>已索引分块：{indexedChunkCount}</div>
                </div>
              </div>

              {ragStatus?.collections.length ? (
                <div className="space-y-2 rounded-xl border border-border bg-muted/20 px-4 py-3">
                  {ragStatus.collections.map((collection) => (
                    <div
                      key={collection.name}
                      className="flex items-center justify-between gap-3 text-xs"
                    >
                      <span className="min-w-0 truncate text-muted-foreground">
                        {collection.source_type}
                      </span>
                      <span className="font-medium text-foreground">
                        {collection.count}
                      </span>
                    </div>
                  ))}
                </div>
              ) : null}

              <input
                ref={ragFileInputRef}
                type="file"
                className="hidden"
                accept=".txt,.md,.pdf,.docx,.png,.jpg,.jpeg,text/plain,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,image/png,image/jpeg"
                onChange={handleRagFileChange}
              />

              <div className="space-y-3 rounded-xl border border-border bg-muted/20 px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="text-sm font-medium text-foreground">
                      上传资料
                    </div>
                    <div className="mt-1 text-xs leading-5 text-muted-foreground">
                      可直接上传案例文件；下方标签全是选填，只用于检索过滤和排序。
                    </div>
                  </div>
                  <Badge variant="outline">third_party_reference</Badge>
                </div>

                <div className="grid gap-3">
                  <div className="grid gap-2">
                    <Label htmlFor="rag-upload-title">标题</Label>
                    <Input
                      id="rag-upload-title"
                      value={ragUploadForm.title}
                      onChange={(event) =>
                        updateRagUploadForm({ title: event.target.value })
                      }
                      placeholder="默认使用文件名"
                    />
                  </div>
                  <div className="grid gap-2">
                    <Label htmlFor="rag-upload-url">来源链接</Label>
                    <Input
                      id="rag-upload-url"
                      value={ragUploadForm.url}
                      onChange={(event) =>
                        updateRagUploadForm({ url: event.target.value })
                      }
                      placeholder="https://..."
                    />
                  </div>
                  <div className="grid gap-3 sm:grid-cols-3">
                    <div className="grid gap-2">
                      <Label htmlFor="rag-upload-visa-family">签证类型</Label>
                      <Select
                        value={ragUploadForm.visaFamily || "unspecified"}
                        onValueChange={(value) =>
                          updateRagUploadForm({
                            visaFamily: value === "unspecified" ? "" : value,
                          })
                        }
                      >
                        <SelectTrigger
                          id="rag-upload-visa-family"
                          className="w-full"
                        >
                          <SelectValue placeholder="不指定" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="unspecified">不指定</SelectItem>
                          {VISA_FAMILY_OPTIONS.map((option) => (
                            <SelectItem key={option.value} value={option.value}>
                              {option.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="grid gap-2">
                      <Label htmlFor="rag-upload-country">国家</Label>
                      <Input
                        id="rag-upload-country"
                        value={ragUploadForm.country}
                        onChange={(event) =>
                          updateRagUploadForm({ country: event.target.value })
                        }
                        placeholder="例：china"
                      />
                    </div>
                    <div className="grid gap-2">
                      <Label htmlFor="rag-upload-post">领馆/地区</Label>
                      <Input
                        id="rag-upload-post"
                        value={ragUploadForm.post}
                        onChange={(event) =>
                          updateRagUploadForm({ post: event.target.value })
                        }
                        placeholder="例：uk"
                      />
                    </div>
                  </div>
                  <div className="grid gap-2">
                    <Label htmlFor="rag-upload-notes">备注</Label>
                    <Textarea
                      id="rag-upload-notes"
                      value={ragUploadForm.notes}
                      onChange={(event) =>
                        updateRagUploadForm({ notes: event.target.value })
                      }
                      placeholder="可记录来源分类、案例批次或其它检索备注"
                      rows={3}
                    />
                  </div>
                </div>

                <div className="flex flex-col gap-2 sm:flex-row">
                  <Button
                    type="button"
                    variant="outline"
                    className="justify-start"
                    onClick={() => ragFileInputRef.current?.click()}
                    disabled={!canUploadRagFile || isUploadingRagFile}
                  >
                    <Upload className="h-4 w-4" />
                    {ragUploadFile ? "更换文件" : "选择文件"}
                  </Button>
                  <Button
                    type="button"
                    className="justify-start"
                    onClick={handleRagUploadSubmit}
                    disabled={
                      !canUploadRagFile || !ragUploadFile || isUploadingRagFile
                    }
                  >
                    <Upload className="h-4 w-4" />
                    {isUploadingRagFile ? "写入中" : "写入知识库"}
                  </Button>
                </div>
                <div className="min-h-5 truncate text-xs text-muted-foreground">
                  {ragUploadFile ? ragUploadFile.name : "尚未选择文件"}
                </div>
              </div>

              <div className="grid gap-2 sm:grid-cols-2">
                <Button
                  type="button"
                  variant="outline"
                  className="justify-start"
                  onClick={onRefreshRagStatus}
                  disabled={isLoadingRagStatus}
                >
                  <RefreshCw
                    className={
                      isLoadingRagStatus ? "h-4 w-4 animate-spin" : "h-4 w-4"
                    }
                  />
                  刷新状态
                </Button>
              </div>

              {ragError ? (
                <div className="rounded-xl border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                  {ragError}
                </div>
              ) : null}
            </CardContent>
          </Card>
        ) : null}

        {showUserModelConfig ? (
          <Card className="py-4 opacity-75">
            <CardHeader className="px-5 pb-3">
              <CardTitle className="text-base">模型运行配置</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 px-5">
              <div className="rounded-xl border border-border bg-muted/20 px-4 py-3">
                <div className="text-sm font-medium text-foreground">
                  由后台统一配置
                </div>
                <div className="mt-1 text-xs leading-5 text-muted-foreground">
                  当前产品不向普通用户开放自带模型参数；如需调整模型来源，请联系管理员在后台控制台处理。
                </div>
              </div>
              <Button
                type="button"
                variant="outline"
                className="w-full justify-start"
                disabled
              >
                <RefreshCw className="h-4 w-4" />
                模型列表仅管理员可维护
              </Button>
              {modelConfigError ? (
                <div className="rounded-xl border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                  {modelConfigError}
                </div>
              ) : null}
            </CardContent>
          </Card>
        ) : null}

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
            {showDebugTools ? (
              <div className="space-y-3 rounded-xl border border-border bg-muted/20 px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="text-sm font-medium text-foreground">
                      调试合成材料
                    </div>
                    <div className="mt-1 text-xs leading-5 text-muted-foreground">
                      内部诊断用：生成合成材料来测试核验、追问和报告，不代表已验证案例模板。
                    </div>
                  </div>
                  <Badge variant="outline">internal diagnostic</Badge>
                </div>

                <div className="grid gap-2">
                  <Label htmlFor="debug-material-bundle-scenario">场景</Label>
                  <Select
                    value={activeDebugBundleScenario}
                    onValueChange={(value) =>
                      setSelectedDebugBundleScenario(
                        value as DebugMaterialBundleScenario,
                      )
                    }
                  >
                    <SelectTrigger
                      id="debug-material-bundle-scenario"
                      className="w-full"
                    >
                      <SelectValue placeholder="选择材料包场景" />
                    </SelectTrigger>
                    <SelectContent>
                      {debugBundleOptions.map((option) => (
                        <SelectItem
                          key={option.scenario}
                          value={option.scenario}
                        >
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <div className="min-h-10 text-xs leading-5 text-muted-foreground">
                    {selectedDebugBundleOption.description}
                  </div>
                </div>

                <div className="grid gap-2">
                  <Label htmlFor="debug-material-seed">材料生成依据</Label>
                  <Textarea
                    id="debug-material-seed"
                    value={materialSeedText}
                    onChange={(event) =>
                      setMaterialSeedOverride(event.target.value)
                    }
                    placeholder="例如：我会去 UC Irvine 读 Data Science，父母资助，第一年费用约 8 万美元。"
                    className="min-h-24 resize-y"
                  />
                  <div className="text-xs leading-5 text-muted-foreground">
                    这里是唯一生成依据，必填；生成失败不会写入材料。
                  </div>
                </div>

                <Button
                  variant="outline"
                  className="w-full justify-start"
                  onClick={handleDebugBundleSubmit}
                  disabled={
                    !sessionId ||
                    isDebugBundleGenerating ||
                    !materialSeedText.trim()
                  }
                >
                  <FlaskConical
                    className={
                      isDebugBundleGenerating
                        ? "h-4 w-4 animate-pulse"
                        : "h-4 w-4"
                    }
                  />
                  {isDebugBundleGenerating
                    ? "正在生成调试合成材料"
                    : `生成调试${selectedDebugBundleOption.shortLabel}`}
                </Button>

                {debugBundleProgress.length ? (
                  <div className="max-h-44 overflow-y-auto rounded-md border border-border bg-background px-3 py-2 text-xs leading-5 text-muted-foreground">
                    {debugBundleProgress.slice(-8).map((line, index) => (
                      <div key={`${line}-${index}`} className="break-words">
                        {line}
                      </div>
                    ))}
                  </div>
                ) : null}

                <div className="space-y-2 rounded-lg border border-border bg-background px-3 py-3">
                  <div className="flex items-center justify-between gap-2">
                    <div>
                      <div className="text-sm font-medium text-foreground">
                        已验证案例包 / 材料包
                      </div>
                      <div className="mt-0.5 text-xs text-muted-foreground">
                        只应选择通过完整材料理解与面签验证的案例包；partial/failed 会被禁用。
                      </div>
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={onRefreshMaterialPackages}
                      disabled={isLoadingMaterialPackages}
                    >
                      <RefreshCw
                        className={
                          isLoadingMaterialPackages
                            ? "h-4 w-4 animate-spin"
                            : "h-4 w-4"
                        }
                      />
                      刷新
                    </Button>
                  </div>

                  {materialPackages.length ? (
                    <Select
                      onValueChange={handleImportPackage}
                      disabled={
                        !sessionId ||
                        isLoadingMaterialPackages ||
                        isImportingMaterialPackage
                      }
                    >
                      <SelectTrigger className="w-full">
                        <SelectValue
                          placeholder={
                            isImportingMaterialPackage
                              ? "正在导入案例包"
                              : "选择已验证案例包导入"
                          }
                        />
                      </SelectTrigger>
                      <SelectContent>
                        {sortedMaterialPackages.map((item) => (
                          <SelectItem
                            key={item.package_id}
                            value={item.package_id}
                            disabled={!isImportableMaterialPackage(item)}
                          >
                            {isValidatedMaterialPackage(item) ? "✅ " : ""}
                            {item.label} · {item.document_count} 份 ·{" "}
                            {item.status_label}
                            {!isImportableMaterialPackage(item) ? " · 不可导入" : ""}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : (
                    <div className="rounded-md border border-dashed border-border px-3 py-2 text-xs text-muted-foreground">
                      {isLoadingMaterialPackages
                        ? "正在读取已验证案例包..."
                        : "暂无可导入的已验证案例包。"}
                    </div>
                  )}

                  {sortedMaterialPackages.slice(0, 3).map((item) => (
                    <div
                      key={`${item.package_id}-summary`}
                      className="flex items-start justify-between gap-3 rounded-md border border-border/70 px-3 py-2"
                    >
                      <div className="min-w-0">
                        <div className="truncate text-xs font-medium text-foreground">
                          {item.label}
                        </div>
                        <div className="mt-1 text-[11px] leading-4 text-muted-foreground">
                          {item.document_count} 份材料 / {materialPackageMetaSummary(item)}
                        </div>
                        {item.warning || !isImportableMaterialPackage(item) ? (
                          <div
                            className={
                              item.status === "failed"
                                ? "mt-1 text-[11px] leading-4 text-destructive"
                                : "mt-1 text-[11px] leading-4 text-amber-700"
                            }
                          >
                            {item.warning ?? "该案例包尚未 ready，当前不可导入。"}
                          </div>
                        ) : null}
                      </div>
                      <Badge
                        variant={
                          item.status === "failed"
                            ? "destructive"
                            : item.status === "ready"
                              ? "secondary"
                              : "outline"
                        }
                      >
                        {materialPackageBadgeLabel(item)} · {item.status_label}
                      </Badge>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
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
                  清理本账号的会话历史记录（{historyCount}）
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent className="rounded-2xl">
                <AlertDialogHeader>
                  <AlertDialogTitle>确认清理本账号的会话历史记录？</AlertDialogTitle>
                  <AlertDialogDescription>
                    此操作将删除本账号下已保存的历史会话摘要、消息记录和归档材料，并同步清理旧版本 Kit 留在本浏览器中的会话历史；当前进行中的会话会保留。
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel className="rounded-xl">
                    取消
                  </AlertDialogCancel>
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

        {showGithub ? (
          <Card className="py-4">
            <CardHeader className="px-5 pb-3">
              <CardTitle className="text-base">项目信息</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 px-5">
              <div className="rounded-xl border border-border bg-muted/20 px-4 py-3">
                <div className="text-sm font-medium text-foreground">
                  项目创建者
                </div>
                <div className="mt-1 text-sm text-muted-foreground">
                  {PROJECT_INFO.creatorName}
                </div>
              </div>
              <a
                href={PROJECT_INFO.githubUrl}
                target="_blank"
                rel="noreferrer"
                className="flex items-center justify-between gap-3 rounded-xl border border-border bg-muted/20 px-4 py-3 text-sm font-medium text-foreground transition-colors hover:bg-muted/40"
              >
                <span className="flex min-w-0 items-center gap-2">
                  <Github className="h-4 w-4 shrink-0 text-primary" />
                  <span className="truncate">GitHub 仓库</span>
                </span>
                <ExternalLink className="h-4 w-4 shrink-0 text-muted-foreground" />
              </a>
            </CardContent>
          </Card>
        ) : null}
      </div>
    </ScrollArea>
  )
}
