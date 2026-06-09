"use client"

import { FileText, ImageIcon, Paperclip } from "lucide-react"
import type { UploadedMaterial } from "@/lib/api/types"

interface WxMaterialStripProps {
  materials: UploadedMaterial[]
}

function MaterialIcon({ material }: { material: UploadedMaterial }) {
  if (material.kind === "image") {
    return <ImageIcon className="h-4 w-4 text-sky-200" />
  }
  if (material.kind === "pdf") {
    return <FileText className="h-4 w-4 text-rose-200" />
  }
  return <Paperclip className="h-4 w-4 text-slate-200" />
}

export function WxMaterialStrip({ materials }: WxMaterialStripProps) {
  return (
    <section className="space-y-3 rounded-3xl border border-white/10 bg-white/[0.06] p-4 text-white">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold">当前材料</h2>
        <span className="text-xs text-slate-300">{materials.length} 个</span>
      </div>
      {materials.length ? (
        <div className="flex gap-2 overflow-x-auto pb-1">
          {materials.map((material) => (
            <div
              key={material.id}
              className="min-w-44 rounded-2xl border border-white/10 bg-black/20 p-3"
            >
              <div className="flex items-center gap-2">
                <MaterialIcon material={material} />
                <span className="truncate text-sm font-medium">{material.name}</span>
              </div>
              <p className="mt-2 line-clamp-2 text-xs leading-5 text-slate-300">
                {material.document_type_label ?? material.status_label}
              </p>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm leading-6 text-slate-300">
          还没有上传材料。建议先补充护照、I-20/邀请信、资金证明等关键文件。
        </p>
      )}
    </section>
  )
}
