"use client"

import Image from "next/image"
import { cn } from "@/lib/utils"
import { APP_VERSION_LABEL, appVersionDetailLabel } from "@/lib/app-version"
import { PROJECT_INFO } from "@/lib/project-info"
import { Bug, Clock, FolderOpen, Github, MessageSquare, Settings } from "lucide-react"

interface SidebarProps {
  activeItem: string
  onItemClick: (item: string) => void
  showDebug?: boolean
  showGithub?: boolean
}

export const navItems = [
  { id: "workbench", label: "面签工作台", icon: MessageSquare },
  { id: "history", label: "历史记录", icon: Clock },
  { id: "materials", label: "材料库", icon: FolderOpen },
  { id: "debug", label: "调试台", icon: Bug },
  { id: "settings", label: "设置", icon: Settings },
]

export function Sidebar({
  activeItem,
  onItemClick,
  showDebug = true,
  showGithub = true,
}: SidebarProps) {
  const visibleNavItems = navItems.filter((item) => item.id !== "debug" || showDebug)
  return (
    <aside className="hidden h-full w-64 flex-col border-r border-white/60 bg-white/55 shadow-xl shadow-blue-950/5 backdrop-blur-2xl dark:border-white/10 dark:bg-black/30 dark:shadow-black/30 lg:flex">
      {/* Logo */}
      <div className="border-b border-white/60 px-5 pb-6 pt-12 dark:border-white/10">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center overflow-hidden rounded-2xl border border-white/70 bg-blue-50/80 shadow-sm dark:border-white/12 dark:bg-white/[0.06] dark:shadow-inner dark:shadow-white/10">
            <Image src="/brand-icon.svg" alt="DS-160 模拟面签" width={40} height={40} className="h-10 w-10" />
          </div>
          <div className="min-w-0">
            <h1 className="truncate font-semibold text-foreground">DS-160 模拟面签</h1>
            <p className="text-xs font-mono text-muted-foreground">{APP_VERSION_LABEL}</p>
          </div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-3">
        <ul className="space-y-1">
          {visibleNavItems.map((item) => {
            const Icon = item.icon
            const isActive = activeItem === item.id
            return (
              <li key={item.id}>
                <button
                  onClick={() => onItemClick(item.id)}
                  className={cn(
                    "flex w-full items-center gap-3 rounded-2xl px-3 py-2.5 text-sm font-medium transition-all duration-200",
                    isActive
                      ? "border border-blue-200/70 bg-blue-600/10 text-blue-700 shadow-sm dark:border-cyan-200/25 dark:bg-cyan-200/10 dark:text-cyan-100"
                      : "text-slate-600 hover:-translate-y-0.5 hover:bg-white/70 hover:text-slate-950 dark:text-slate-300 dark:hover:bg-white/[0.08] dark:hover:text-white"
                  )}
                >
                  <Icon className="w-5 h-5" />
                  {item.label}
                </button>
              </li>
            )
          })}
        </ul>
      </nav>

      {showGithub ? <div className="border-t border-white/60 p-4 dark:border-white/10">
        <div className="rounded-3xl border border-white/70 bg-white/55 px-3 py-3 shadow-sm backdrop-blur-xl dark:border-white/10 dark:bg-white/[0.045] dark:shadow-black/20">
          <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            Created by
          </div>
          <div className="mt-1 truncate text-sm font-medium text-foreground">
            {PROJECT_INFO.creatorName}
          </div>
          <div className="mt-1 truncate font-mono text-[11px] text-muted-foreground">
            {appVersionDetailLabel()}
          </div>
          <a
            href={PROJECT_INFO.githubUrl}
            target="_blank"
            rel="noreferrer"
            className="mt-3 inline-flex max-w-full items-center gap-2 rounded-xl text-sm font-medium text-blue-700 transition-colors hover:text-blue-600 dark:text-cyan-100 dark:hover:text-white"
          >
            <Github className="h-4 w-4 shrink-0" />
            <span className="truncate">GitHub</span>
          </a>
        </div>
      </div> : null}
    </aside>
  )
}
