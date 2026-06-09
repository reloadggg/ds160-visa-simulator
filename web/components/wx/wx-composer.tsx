"use client"

import { FormEvent, useState } from "react"
import { Send } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"

interface WxComposerProps {
  disabled?: boolean
  isSending?: boolean
  onSend: (content: string) => Promise<void>
}

export function WxComposer({ disabled, isSending, onSend }: WxComposerProps) {
  const [value, setValue] = useState("")

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const content = value.trim()
    if (!content) {
      return
    }
    setValue("")
    await onSend(content)
  }

  return (
    <form className="flex items-end gap-2" onSubmit={handleSubmit}>
      <Textarea
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder="输入你的回答..."
        rows={1}
        disabled={disabled || isSending}
        className="min-h-11 flex-1 resize-none rounded-2xl border-white/10 bg-white/10 text-sm text-white placeholder:text-slate-400"
      />
      <Button
        type="submit"
        size="icon"
        className="h-11 w-11 rounded-2xl bg-cyan-300 text-slate-950 hover:bg-cyan-200"
        disabled={disabled || isSending || !value.trim()}
      >
        <Send className="h-4 w-4" />
      </Button>
    </form>
  )
}
