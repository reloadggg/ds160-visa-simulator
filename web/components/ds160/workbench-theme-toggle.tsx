"use client"

import { useSyncExternalStore } from "react"
import { Moon, Sun } from "lucide-react"
import { useTheme } from "next-themes"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

type WorkbenchThemeToggleProps = {
  className?: string
  compact?: boolean
}

const subscribeToHydration = () => () => undefined
const getClientSnapshot = () => true
const getServerSnapshot = () => false

export function WorkbenchThemeToggle({
  className,
  compact = false,
}: WorkbenchThemeToggleProps) {
  const mounted = useSyncExternalStore(
    subscribeToHydration,
    getClientSnapshot,
    getServerSnapshot,
  )
  const { resolvedTheme, setTheme } = useTheme()

  const isDark = mounted ? resolvedTheme !== "light" : true
  const nextTheme = isDark ? "light" : "dark"
  const label = isDark ? "切换白色主题" : "切换黑色主题"

  return (
    <Button
      type="button"
      variant="outline"
      size={compact ? "icon-sm" : "sm"}
      aria-label={label}
      title={label}
      disabled={!mounted}
      onClick={() => setTheme(nextTheme)}
      className={cn(
        "rounded-full border-white/70 bg-white/70 text-slate-700 shadow-sm hover:bg-white hover:text-slate-950",
        "dark:border-white/12 dark:bg-white/[0.06] dark:text-slate-100 dark:shadow-black/20 dark:hover:border-cyan-200/35 dark:hover:bg-cyan-200/10 dark:hover:text-white",
        className,
      )}
    >
      {isDark ? <Moon className="h-4 w-4" /> : <Sun className="h-4 w-4" />}
      {compact ? null : (
        <span className="hidden sm:inline">
          {isDark ? "黑色主题" : "白色主题"}
        </span>
      )}
    </Button>
  )
}
