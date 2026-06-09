"use client"

import { AlertTriangle, CheckCircle2, ClipboardList } from "lucide-react"
import type { UserReport } from "@/lib/api/types"

interface WxReportSummaryProps {
  report: UserReport | null
  error?: string | null
}

export function WxReportSummary({ report, error }: WxReportSummaryProps) {
  return (
    <section className="space-y-3 rounded-3xl border border-white/10 bg-white/[0.06] p-4 text-white">
      <div className="flex items-center gap-2">
        <ClipboardList className="h-4 w-4 text-cyan-200" />
        <h2 className="text-base font-semibold">报告摘要</h2>
      </div>
      {error ? <p className="text-xs leading-5 text-red-200">{error}</p> : null}
      {report ? (
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-3 rounded-2xl bg-black/20 px-3 py-2">
            <span className="text-sm text-slate-300">当前状态</span>
            <span className="text-sm font-medium text-cyan-100">
              {report.interview_status_label}
            </span>
          </div>
          <div className="flex items-center gap-2 text-sm">
            {report.risk_level === "high" ? (
              <AlertTriangle className="h-4 w-4 text-amber-200" />
            ) : (
              <CheckCircle2 className="h-4 w-4 text-emerald-200" />
            )}
            <span>{report.risk_level_label}</span>
          </div>
          <p className="text-sm leading-6 text-slate-200">{report.summary}</p>
          {report.current_key_question && report.current_key_question !== "暂无" ? (
            <div className="rounded-2xl border border-cyan-200/20 bg-cyan-200/10 px-3 py-2">
              <div className="text-xs text-cyan-100">当前关键问题</div>
              <p className="mt-1 text-sm leading-6 text-white">
                {report.current_key_question}
              </p>
            </div>
          ) : null}
          {report.recommended_improvements.length ? (
            <ul className="space-y-1 text-xs leading-5 text-slate-300">
              {report.recommended_improvements.slice(0, 3).map((item) => (
                <li key={item}>• {item}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : (
        <p className="text-sm leading-6 text-slate-300">
          会话开始后会在这里显示风险、当前问题和下一步建议。
        </p>
      )}
    </section>
  )
}
