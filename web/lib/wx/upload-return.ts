export interface PendingWxUploadTicket {
  ticket: string
  sessionId: string
  createdAt: number
}

const STORAGE_KEY = "ds160-wx-pending-upload-ticket"
const MAX_PENDING_AGE_MS = 15 * 60 * 1000

function isPendingWxUploadTicket(value: unknown): value is PendingWxUploadTicket {
  if (!value || typeof value !== "object") {
    return false
  }
  const candidate = value as Partial<PendingWxUploadTicket>
  return (
    typeof candidate.ticket === "string" &&
    candidate.ticket.length > 0 &&
    typeof candidate.sessionId === "string" &&
    candidate.sessionId.length > 0 &&
    typeof candidate.createdAt === "number" &&
    Number.isFinite(candidate.createdAt)
  )
}

export function storePendingWxUploadTicket(ticket: PendingWxUploadTicket): void {
  if (typeof window === "undefined") {
    return
  }
  localStorage.setItem(STORAGE_KEY, JSON.stringify(ticket))
}

export function readPendingWxUploadTicket(): PendingWxUploadTicket | null {
  if (typeof window === "undefined") {
    return null
  }
  const raw = localStorage.getItem(STORAGE_KEY)
  if (!raw) {
    return null
  }
  try {
    const parsed = JSON.parse(raw) as unknown
    if (!isPendingWxUploadTicket(parsed)) {
      localStorage.removeItem(STORAGE_KEY)
      return null
    }
    if (Date.now() - parsed.createdAt > MAX_PENDING_AGE_MS) {
      localStorage.removeItem(STORAGE_KEY)
      return null
    }
    return parsed
  } catch {
    localStorage.removeItem(STORAGE_KEY)
    return null
  }
}

export function clearPendingWxUploadTicket(ticket?: string): void {
  if (typeof window === "undefined") {
    return
  }
  if (!ticket) {
    localStorage.removeItem(STORAGE_KEY)
    return
  }
  const pending = readPendingWxUploadTicket()
  if (pending?.ticket === ticket) {
    localStorage.removeItem(STORAGE_KEY)
  }
}

export function hasUploadReturnSignal(search: string): boolean {
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search)
  return params.get("upload_done") === "1" || Boolean(params.get("upload_ticket"))
}
