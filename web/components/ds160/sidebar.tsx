"use client"

import Image from "next/image"
import { cn } from "@/lib/utils"
import { MessageSquare, Clock, FolderOpen, Settings } from "lucide-react"

interface SidebarProps {
  activeItem: string
  onItemClick: (item: string) => void
}

export const navItems = [
  { id: "workbench", label: "面签工作台", icon: MessageSquare },
  { id: "history", label: "历史记录", icon: Clock },
  { id: "materials", label: "材料库", icon: FolderOpen },
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
            <p className="text-xs text-muted-foreground">工作台</p>
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
    </aside>
  )
}
