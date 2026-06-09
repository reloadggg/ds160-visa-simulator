const ACCESS_KEY_SHARE_PARAMS = ["ds160_access_key", "access_key", "key"] as const

export const ACCESS_KEY_SHARE_PARAM = ACCESS_KEY_SHARE_PARAMS[0]

type ShareLocation = Pick<Location, "hash" | "search">

export function maskAccessKeyForDisplay(accessKey: string): string {
  const trimmed = accessKey.trim()
  if (!trimmed) {
    return ""
  }
  if (trimmed.length <= 14) {
    return `${trimmed.slice(0, 4)}••••${trimmed.slice(-3)}`
  }
  return `${trimmed.slice(0, 10)}••••${trimmed.slice(-4)}`
}

function firstAccessKeyFromParams(params: URLSearchParams): string | null {
  for (const param of ACCESS_KEY_SHARE_PARAMS) {
    const value = params.get(param)?.trim()
    if (value) {
      return value
    }
  }
  return null
}

function paramsFromHash(hash: string): URLSearchParams {
  const rawHash = hash.startsWith("#") ? hash.slice(1) : hash
  if (!rawHash) {
    return new URLSearchParams()
  }

  const queryStart = rawHash.indexOf("?")
  const queryLike = queryStart >= 0 ? rawHash.slice(queryStart + 1) : rawHash
  const normalized = queryLike.startsWith("?") ? queryLike.slice(1) : queryLike
  return new URLSearchParams(normalized)
}

function hashContainsSharedAccessKey(hash: string): boolean {
  return Boolean(firstAccessKeyFromParams(paramsFromHash(hash)))
}

export function parseSharedAccessKeyFromLocation(
  location: ShareLocation,
): string | null {
  const fromHash = firstAccessKeyFromParams(paramsFromHash(location.hash))
  if (fromHash) {
    return fromHash
  }

  return firstAccessKeyFromParams(new URLSearchParams(location.search))
}

export function buildAccessKeyShareLink(
  accessKey: string,
  origin = typeof window !== "undefined" ? window.location.origin : "http://localhost:3000",
): string {
  const url = new URL("/", origin)
  const params = new URLSearchParams()
  params.set(ACCESS_KEY_SHARE_PARAM, accessKey.trim())
  url.hash = params.toString()
  return url.toString()
}

export function stripSharedAccessKeyFromCurrentUrl(): void {
  if (typeof window === "undefined") {
    return
  }

  const url = new URL(window.location.href)
  let changed = false

  for (const param of ACCESS_KEY_SHARE_PARAMS) {
    if (url.searchParams.has(param)) {
      url.searchParams.delete(param)
      changed = true
    }
  }

  if (hashContainsSharedAccessKey(url.hash)) {
    url.hash = ""
    changed = true
  }

  if (changed) {
    window.history.replaceState(
      window.history.state,
      document.title,
      `${url.pathname}${url.search}${url.hash}`,
    )
  }
}
