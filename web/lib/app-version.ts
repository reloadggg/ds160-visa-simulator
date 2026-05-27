import packageJson from "@/package.json"

export const APP_VERSION = {
  version: process.env.NEXT_PUBLIC_APP_VERSION ?? packageJson.version,
  gitSha:
    process.env.NEXT_PUBLIC_GIT_SHA ??
    process.env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA ??
    null,
  buildTime: process.env.NEXT_PUBLIC_BUILD_TIME ?? null,
} as const

export const APP_VERSION_LABEL = `v${APP_VERSION.version}`

export function appVersionDetailLabel(): string {
  const details = [
    APP_VERSION_LABEL,
    APP_VERSION.gitSha ? APP_VERSION.gitSha.slice(0, 7) : null,
    APP_VERSION.buildTime,
  ].filter(Boolean)
  return details.join(" · ")
}
