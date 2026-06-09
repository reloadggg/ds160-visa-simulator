"use client"

import { ChangeEvent, useRef } from "react"
import { FileUp, MessageSquareMore } from "lucide-react"
import { Button } from "@/components/ui/button"

interface WxUploadEntryProps {
  disabled?: boolean
  isUploading: boolean
  isNativeUploadStarting: boolean
  isRefreshingUploadTicket: boolean
  uploadError?: string | null
  nativeUploadNotice?: string | null
  onH5Upload: (files: File[]) => Promise<void>
  onNativeUpload: () => Promise<void>
}

export function WxUploadEntry({
  disabled,
  isUploading,
  isNativeUploadStarting,
  isRefreshingUploadTicket,
  uploadError,
  nativeUploadNotice,
  onH5Upload,
  onNativeUpload,
}: WxUploadEntryProps) {
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleFileChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? [])
    event.target.value = ""
    if (files.length) {
      await onH5Upload(files)
    }
  }

  return (
    <section className="space-y-3 rounded-3xl border border-white/10 bg-white/[0.06] p-4 text-white">
      <div>
        <h2 className="text-base font-semibold">上传材料</h2>
        <p className="mt-1 text-xs leading-5 text-slate-300">
          普通浏览器可上传本地图片/PDF；微信小程序里可跳到原生页选择聊天文件。
        </p>
      </div>
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept="image/*,.pdf,.doc,.docx"
        className="hidden"
        onChange={handleFileChange}
      />
      <div className="grid grid-cols-2 gap-2">
        <Button
          type="button"
          variant="outline"
          className="h-auto min-h-12 flex-col border-white/10 bg-white/5 py-3 text-white hover:bg-white/10"
          disabled={disabled || isUploading}
          onClick={() => fileInputRef.current?.click()}
        >
          <FileUp className="h-4 w-4" />
          {isUploading ? "上传中" : "本地上传"}
        </Button>
        <Button
          type="button"
          variant="outline"
          className="h-auto min-h-12 flex-col border-cyan-200/20 bg-cyan-200/10 py-3 text-cyan-50 hover:bg-cyan-200/15"
          disabled={disabled || isNativeUploadStarting || isRefreshingUploadTicket}
          onClick={() => void onNativeUpload()}
        >
          <MessageSquareMore className="h-4 w-4" />
          {isNativeUploadStarting || isRefreshingUploadTicket ? "处理中" : "微信聊天文件"}
        </Button>
      </div>
      {uploadError ? (
        <p className="rounded-2xl border border-red-400/25 bg-red-500/10 px-3 py-2 text-xs leading-5 text-red-100">
          {uploadError}
        </p>
      ) : null}
      {nativeUploadNotice ? (
        <p className="rounded-2xl border border-cyan-300/20 bg-cyan-300/10 px-3 py-2 text-xs leading-5 text-cyan-50">
          {nativeUploadNotice}
        </p>
      ) : null}
    </section>
  )
}
