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

  // Default product theme is light; avoid flashing dark before hydration.
  const isDark = mounted ? resolvedTheme === "dark" : false
  const nextTheme = isDark ? "light" : "dark"
  const label = isDark ? "切换到浅色主题" : "切换到深色主题"
  const shortLabel = isDark ? "浅色" : "深色"

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
        "rounded-full border-border bg-card text-foreground shadow-sm",
        "hover:bg-accent hover:text-accent-foreground",
        "dark:border-white/12 dark:bg-white/[0.06] dark:text-slate-100",
        "dark:hover:border-cyan-200/35 dark:hover:bg-cyan-200/10 dark:hover:text-white",
        className,
      )}
    >
      {isDark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
      {compact ? null : (
        <span className="hidden sm:inline">{shortLabel}</span>
      )}
    </Button>
  )
}
