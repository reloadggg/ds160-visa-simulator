"use client"

import { useEffect, useState } from "react"

import { LandingPage } from "@/components/landing/landing-page"
import { getAppConfig } from "@/lib/api/client"
import type { AppConfig } from "@/lib/api/types"

const DEFAULT_APP_CONFIG: AppConfig = {
  show_github_link: false,
  wx_entry_enabled: false,
  debug_console_enabled: false,
  debug_material_enabled: false,
  user_model_config_enabled: false,
  rag_status_user_visible: false,
}

export default function HomePage() {
  const [appConfig, setAppConfig] = useState<AppConfig>(DEFAULT_APP_CONFIG)

  useEffect(() => {
    let cancelled = false

    getAppConfig()
      .then((config) => {
        if (!cancelled) {
          setAppConfig(config)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setAppConfig(DEFAULT_APP_CONFIG)
        }
      })

    return () => {
      cancelled = true
    }
  }, [])

  return <LandingPage showGithubLink={appConfig.show_github_link} />
}
