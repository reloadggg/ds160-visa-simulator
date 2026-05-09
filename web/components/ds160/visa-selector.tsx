"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Spinner } from "@/components/ui/spinner"
import Image from "next/image"
import { cn } from "@/lib/utils"
import { VISA_FAMILIES, type VisaFamily } from "@/lib/api/types"
import { GraduationCap, Users, Briefcase, Building2 } from "lucide-react"

interface VisaSelectorProps {
  onSelect: (visaType: VisaFamily) => void
  isLoading: boolean
  error?: string | null
  mockMode?: boolean
  embedded?: boolean
}

const visaIcons: Record<VisaFamily, React.ComponentType<{ className?: string }>> = {
  "F-1": GraduationCap,
  "J-1": Users,
  "B-1/B-2": Briefcase,
  "H-1B": Building2,
}

export function VisaSelector({
  onSelect,
  isLoading,
  error,
  mockMode = false,
  embedded = false,
}: VisaSelectorProps) {
  const [selectedVisa, setSelectedVisa] = useState<VisaFamily | null>(null)

  const handleStart = () => {
    if (selectedVisa) {
      onSelect(selectedVisa)
    }
  }

  return (
    <div
      className={cn(
        "bg-background flex justify-center overflow-y-auto p-4 md:p-6",
        embedded
          ? "h-full min-h-0 items-start pb-[calc(5.5rem+env(safe-area-inset-bottom))] sm:items-center lg:pb-6"
          : "min-h-[100dvh] items-center",
      )}
    >
      <div className="w-full max-w-2xl">
        <div className="text-center mb-8">
          <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center overflow-hidden rounded-2xl bg-primary/10">
            <Image src="/brand-icon.svg" alt="面签模拟器" width={64} height={64} className="h-16 w-16" />
          </div>
          <h1 className="text-2xl font-semibold text-foreground mb-2">
            面签模拟器
          </h1>
          <p className="text-muted-foreground">
            选择您要模拟的签证类型，开始练习面签
          </p>
          {mockMode && (
            <div className="mt-3 inline-flex items-center rounded-full border border-amber-300 bg-amber-50 px-3 py-1 text-xs font-medium text-amber-800">
              开发模式：当前使用 Mock 数据
            </div>
          )}
        </div>

        <Card className="shadow-lg">
          <CardHeader>
            <CardTitle>选择签证类型</CardTitle>
            <CardDescription>
              请选择您计划申请的签证类别
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 p-4 md:p-6">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {VISA_FAMILIES.map((visa) => {
                const Icon = visaIcons[visa.value]
                const isSelected = selectedVisa === visa.value
                return (
                  <button
                    key={visa.value}
                    onClick={() => setSelectedVisa(visa.value)}
                    disabled={isLoading}
                    className={cn(
                      "p-4 rounded-xl border-2 text-left transition-all hover:shadow-md",
                      isSelected
                        ? "border-primary bg-primary/5"
                        : "border-border hover:border-primary/30"
                    )}
                  >
                    <div className="flex items-start gap-3">
                      <div
                        className={cn(
                          "w-10 h-10 rounded-lg flex items-center justify-center shrink-0",
                          isSelected ? "bg-primary/10" : "bg-muted"
                        )}
                      >
                        <Icon
                          className={cn(
                            "w-5 h-5",
                            isSelected ? "text-primary" : "text-muted-foreground"
                          )}
                        />
                      </div>
                      <div>
                        <div
                          className={cn(
                            "font-medium",
                            isSelected ? "text-primary" : "text-foreground"
                          )}
                        >
                          {visa.label}
                        </div>
                        <div className="text-sm text-muted-foreground">
                          {visa.description}
                        </div>
                      </div>
                    </div>
                  </button>
                )
              })}
            </div>

            <div className="pt-4">
              {error && (
                <div className="mb-3 rounded-lg border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                  {error}
                </div>
              )}
              <Button
                onClick={handleStart}
                disabled={!selectedVisa || isLoading}
                className="w-full"
                size="lg"
              >
                {isLoading ? (
                  <>
                    <Spinner className="mr-2" />
                    正在初始化会话...
                  </>
                ) : (
                  "开始模拟面签"
                )}
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
