"use client"

import Image from "next/image"
import { cn } from "@/lib/utils"
import { APP_VERSION_LABEL, appVersionDetailLabel } from "@/lib/app-version"
import { PROJECT_INFO } from "@/lib/project-info"
import { Bug, Clock, FolderOpen, Github, MessageSquare, Settings } from "lucide-react"

interface SidebarProps {
  activeItem: string
  onItemClick: (item: string) => void
}

export const navItems = [
  { id: "workbench", label: "面签工作台", icon: MessageSquare },
  { id: "history", label: "历史记录", icon: Clock },
  { id: "materials", label: "材料库", icon: FolderOpen },
  { id: "debug", label: "调试台", icon: Bug },
  { id: "settings", label: "设置", icon: Settings },
]

export function Sidebar({ activeItem, onItemClick }: SidebarProps) {
  return (
    <aside className="hidden lg:flex w-60 bg-card border-r border-border flex-col h-full">
      {/* Logo */}
      <div className="px-5 pt-12 pb-6 border-b border-border">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center overflow-hidden rounded-xl bg-primary/10">
            <Image src="/brand-icon.svg" alt="面签模拟器" width={40} height={40} className="h-10 w-10" />
          </div>
          <div className="min-w-0">
            <h1 className="truncate font-semibold text-foreground">面签模拟器</h1>
            <p className="text-xs font-mono text-muted-foreground">{APP_VERSION_LABEL}</p>
          </div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-3">
        <ul className="space-y-1">
          {navItems.map((item) => {
            const Icon = item.icon
            const isActive = activeItem === item.id
            return (
              <li key={item.id}>
                <button
                  onClick={() => onItemClick(item.id)}
                  className={cn(
                    "w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors",
                    isActive
                      ? "bg-primary/10 text-primary"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground"
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

      <div className="border-t border-border p-4">
        <div className="rounded-lg border border-border bg-muted/20 px-3 py-3">
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
            className="mt-3 inline-flex max-w-full items-center gap-2 rounded-md text-sm font-medium text-primary hover:text-primary/80"
          >
            <Github className="h-4 w-4 shrink-0" />
            <span className="truncate">GitHub</span>
          </a>
        </div>
      </div>
    </aside>
  )
}
