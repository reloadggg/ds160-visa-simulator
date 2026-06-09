"use client"

import { Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import type { BackendSessionListItem, VisaFamily } from "@/lib/api/types"
import { WX_VISA_FAMILIES } from "@/hooks/use-wx-workbench"

interface WxVisaPickerProps {
  sessions: BackendSessionListItem[]
  isCreating: boolean
  onStart: (family: VisaFamily) => Promise<void>
  onRestore: (session: BackendSessionListItem) => Promise<void>
}

function familyDescription(family: VisaFamily): string {
  switch (family) {
    case "F-1":
      return "留学签证，重点练习学校、资金、学习计划。"
    case "J-1":
      return "交流访问，重点练习项目目的和回国约束。"
    case "B-1/B-2":
      return "商务/旅游，重点练习行程、资金和真实目的。"
    case "H-1B":
      return "工作签证，重点练习岗位、雇主和专业匹配。"
  }
}

export function WxVisaPicker({
  sessions,
  isCreating,
  onStart,
  onRestore,
}: WxVisaPickerProps) {
  return (
    <section className="space-y-5 px-4 py-5">
      <div className="rounded-3xl border border-white/10 bg-white/[0.06] p-5 text-white shadow-xl">
        <p className="text-sm text-cyan-200">第一步</p>
        <h1 className="mt-2 text-2xl font-semibold">选择这次要练的签证类型</h1>
        <p className="mt-2 text-sm leading-6 text-slate-300">
          微信版先保留主流程：创建会话、回答问题、上传材料、看摘要。
        </p>
      </div>

      <div className="grid gap-3">
        {WX_VISA_FAMILIES.map((family) => (
          <button
            key={family}
            type="button"
            onClick={() => void onStart(family)}
            disabled={isCreating}
            className="rounded-3xl border border-white/10 bg-white/[0.08] p-4 text-left text-white transition hover:border-cyan-200/40 hover:bg-cyan-200/10 disabled:opacity-60"
          >
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-xl font-semibold">{family}</div>
                <p className="mt-1 text-sm leading-6 text-slate-300">
                  {familyDescription(family)}
                </p>
              </div>
              {isCreating ? <Loader2 className="h-5 w-5 animate-spin" /> : null}
            </div>
          </button>
        ))}
      </div>

      {sessions.length ? (
        <div className="space-y-3 rounded-3xl border border-white/10 bg-black/20 p-4 text-white">
          <div className="text-sm font-medium text-slate-200">已有会话</div>
          {sessions.slice(0, 3).map((session) => (
            <Button
              key={session.session_id}
              type="button"
              variant="outline"
              className="w-full justify-between border-white/10 bg-white/5 text-white hover:bg-white/10"
              onClick={() => void onRestore(session)}
            >
              <span className="truncate">{session.session_id}</span>
              <span className="text-xs text-slate-300">继续</span>
            </Button>
          ))}
        </div>
      ) : null}
    </section>
  )
}
