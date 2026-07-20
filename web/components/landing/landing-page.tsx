"use client"

import { useState } from "react"
import Link from "next/link"
import {
  ArrowRight,
  BadgeCheck,
  Brain,
  FileText,
  Github,
  Radar,
  ShieldCheck,
} from "lucide-react"

import { PROJECT_INFO } from "@/lib/project-info"
import { cn } from "@/lib/utils"

import { LandingLoginDialog } from "./landing-login-dialog"

type LandingPageProps = {
  showGithubLink: boolean
}

type LoginDialogTriggerProps = {
  onStartExperience: () => void
}

const navItems = [
  { label: "产品能力", href: "#capabilities" },
  { label: "使用流程", href: "#workflow" },
  { label: "面签预览", href: "#preview" },
  { label: "系统状态", href: "/health" },
]

const capabilityCards = [
  {
    title: "材料理解",
    description:
      "围绕 I-20、资金、学校、家庭与出行信息建立面签上下文，而不是只做表面问答。",
    icon: FileText,
    code: "材料上下文",
  },
  {
    title: "风险追问",
    description:
      "根据回答矛盾、资金解释不足、学习计划薄弱等信号触发更接近真实面签的追问。",
    icon: Radar,
    code: "风险线索",
  },
  {
    title: "真实节奏",
    description:
      "会话按签证类型、材料状态和当前回答推进，保留暂停、结束、复盘等工作台能力。",
    icon: Brain,
    code: "面签节奏",
  },
  {
    title: "结果复盘",
    description:
      "沉淀会话记录、风险摘要和训练方向，帮助申请人在正式面签前知道该补哪里。",
    icon: BadgeCheck,
    code: "复盘输出",
  },
]

const workflowSteps = [
  "获取授权 Key",
  "选择签证类型",
  "上传或导入材料",
  "开始模拟面签",
  "查看复盘报告",
]

const previewTurns = [
  {
    role: "签证官",
    text: "Why did you choose this university instead of a school in China?",
  },
  {
    role: "申请人",
    text: "The program matches my research plan in data-driven product systems...",
  },
  {
    role: "追问线索",
    text: "Funding source and return plan need stronger evidence before the next round.",
  },
]

export function LandingPage({ showGithubLink }: LandingPageProps) {
  const [loginDialogOpen, setLoginDialogOpen] = useState(false)

  const openLoginDialog = () => setLoginDialogOpen(true)

  return (
    <main className="min-h-[100dvh] overflow-hidden bg-[#050608] text-white">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_18%_10%,rgba(14,165,233,0.18),transparent_32%),radial-gradient(circle_at_82%_16%,rgba(125,211,252,0.10),transparent_30%),linear-gradient(180deg,rgba(255,255,255,0.045),transparent_32%)]" />
      <div className="pointer-events-none fixed left-1/2 top-0 h-[420px] w-[720px] -translate-x-1/2 rounded-full bg-cyan-300/8 blur-3xl" />

      <div className="relative mx-auto flex w-full max-w-7xl flex-col px-4 py-5 sm:px-6 lg:px-8">
        <LandingNav showGithubLink={showGithubLink} onStartExperience={openLoginDialog} />
        <Hero showGithubLink={showGithubLink} onStartExperience={openLoginDialog} />
        <ProductPreview />
        <Capabilities />
        <Workflow onStartExperience={openLoginDialog} />
        <FinalCta showGithubLink={showGithubLink} onStartExperience={openLoginDialog} />
      </div>

      <LandingLoginDialog open={loginDialogOpen} onOpenChange={setLoginDialogOpen} />
    </main>
  )
}

function LandingNav({
  showGithubLink,
  onStartExperience,
}: LandingPageProps & LoginDialogTriggerProps) {
  return (
    <header className="sticky top-4 z-20 flex items-center justify-between rounded-full border border-white/10 bg-black/35 px-4 py-3 shadow-2xl shadow-black/20 backdrop-blur-2xl md:px-5">
      <Link href="/" className="flex items-center gap-3">
        <span className="flex h-9 w-9 items-center justify-center rounded-[10px] bg-gradient-to-br from-sky-300 to-blue-600 text-[10px] font-extrabold tracking-tight text-[#001a33] shadow-lg shadow-cyan-950/40">
          DS
        </span>
        <span className="hidden text-sm font-semibold tracking-[-0.02em] text-white sm:inline">
          DS-160 模拟面签
        </span>
        <span className="text-sm font-semibold tracking-[-0.02em] text-white sm:hidden">
          DS-160
        </span>
      </Link>

      <nav className="hidden items-center gap-6 text-sm text-white/58 lg:flex">
        {navItems.map((item) => (
          <a key={item.href} href={item.href} className="transition hover:text-white">
            {item.label}
          </a>
        ))}
      </nav>

      <div className="flex items-center gap-2">
        {showGithubLink ? (
          <a
            href={PROJECT_INFO.githubUrl}
            target="_blank"
            rel="noreferrer"
            className="hidden h-9 items-center gap-2 rounded-full border border-white/10 bg-white/[0.04] px-3 text-sm text-white/70 transition hover:border-white/25 hover:text-white md:inline-flex"
          >
            <Github className="h-4 w-4" />
            GitHub
          </a>
        ) : null}
        <Link
          href="/admin"
          className="hidden h-9 items-center rounded-full px-3 text-sm text-white/62 transition hover:text-white sm:inline-flex"
        >
          后台
        </Link>
        <button
          type="button"
          onClick={onStartExperience}
          className="inline-flex h-9 items-center gap-2 rounded-full bg-white px-4 text-sm font-semibold text-black shadow-lg shadow-white/10 transition hover:-translate-y-0.5 hover:bg-cyan-50"
        >
          开始模拟面签
          <ArrowRight className="h-4 w-4" />
        </button>
      </div>
    </header>
  )
}

function Hero({
  showGithubLink,
  onStartExperience,
}: LandingPageProps & LoginDialogTriggerProps) {
  return (
    <section className="grid min-h-[calc(100dvh-96px)] items-center gap-10 py-20 lg:grid-cols-[1.02fr_0.98fr] lg:py-24">
      <div className="max-w-3xl">
        <div className="relative overflow-hidden rounded-[2.25rem] border border-white/10 bg-white/[0.035] p-5 shadow-2xl shadow-black/30 backdrop-blur-2xl sm:p-7">
          <div
            aria-hidden="true"
            className={cn(
              "pointer-events-none absolute -right-6 -top-8 select-none text-[5.5rem] font-black leading-none tracking-[0.08em] text-white/[0.035] sm:text-[7rem]",
              "[font-family:'Arial_Rounded_MT_Bold','Trebuchet_MS','Avenir_Next','Inter','PingFang_SC','Microsoft_YaHei_UI',system-ui,sans-serif]",
            )}
          >
            DS160
          </div>

          <div className="relative z-10 flex flex-wrap items-center gap-2">
            <div className="inline-flex items-center gap-2 rounded-full border border-cyan-200/15 bg-cyan-200/[0.06] px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.22em] text-cyan-100/80">
              <span className="h-1.5 w-1.5 rounded-full bg-cyan-200 shadow-[0_0_16px_rgba(125,211,252,0.9)]" />
              DS-160 面签预演
            </div>
            <Link
              href="/health"
              className="inline-flex items-center gap-2 rounded-full border border-emerald-300/20 bg-emerald-300/10 px-3 py-1.5 text-xs font-semibold text-emerald-100 transition hover:border-emerald-200/40 hover:bg-emerald-300/15"
            >
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-300 shadow-[0_0_14px_rgba(110,231,183,0.9)]" />
              状态健康
            </Link>
          </div>

          <h1
            className={cn(
              "relative z-10 mt-7 max-w-4xl text-5xl font-black leading-[0.96] tracking-[0.035em] text-white sm:text-6xl lg:text-7xl xl:text-[5.7rem]",
              "[font-family:'Arial_Rounded_MT_Bold','Trebuchet_MS','Avenir_Next','Inter','PingFang_SC','Microsoft_YaHei_UI',system-ui,sans-serif]",
            )}
          >
            面签之前，
            <span className="block bg-gradient-to-r from-white via-cyan-100 to-sky-200 bg-clip-text text-transparent">
              先预演一遍
            </span>
          </h1>

          <p className="relative z-10 mt-7 max-w-2xl text-base leading-8 text-slate-300 sm:text-lg">
            根据签证类型、申请材料和回答一致性生成追问节奏，让申请人在正式窗口前先看见风险点、薄弱证据和下一步训练方向。
          </p>

          <div className="relative z-10 mt-8 grid gap-3 sm:grid-cols-[1fr_auto]">
            <button
              type="button"
              onClick={onStartExperience}
              className="group flex min-h-24 items-center justify-between rounded-[1.5rem] border border-white/12 bg-white px-5 text-left text-black shadow-2xl shadow-cyan-200/10 transition hover:-translate-y-0.5 hover:bg-cyan-50"
            >
              <span>
                <span className="block text-lg font-black tracking-[-0.03em]">开始模拟面签</span>
                <span className="mt-1 block text-sm text-black/55">用授权 Key 进入受保护工作台</span>
              </span>
              <span className="flex h-11 w-11 items-center justify-center rounded-full bg-black text-white transition group-hover:translate-x-0.5">
                <ArrowRight className="h-4 w-4" />
              </span>
            </button>

            <div className="grid gap-3 sm:w-44">
              <Link
                href="/health"
                className="inline-flex h-11 items-center justify-center gap-2 rounded-full border border-emerald-300/20 bg-emerald-300/10 px-4 text-sm font-semibold text-emerald-100 transition hover:border-emerald-200/40 hover:bg-emerald-300/15"
              >
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-300 shadow-[0_0_14px_rgba(110,231,183,0.9)]" />
                系统状态
              </Link>
              {showGithubLink ? (
                <a
                  href={PROJECT_INFO.githubUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex h-11 items-center justify-center gap-2 rounded-full border border-white/10 px-4 text-sm font-semibold text-white/64 transition hover:border-white/25 hover:text-white"
                >
                  <Github className="h-4 w-4" />
                  GitHub
                </a>
              ) : null}
            </div>
          </div>
        </div>

        <div className="mt-4 grid max-w-3xl gap-3 sm:grid-cols-3">
          {[
            ["训练对象", "F-1 申请人", "bg-cyan-300/10 text-cyan-100 border-cyan-300/20"],
            ["复盘重点", "风险与材料", "bg-sky-300/10 text-sky-100 border-sky-300/20"],
          ].map(([label, value, tone]) => (
            <div key={label} className="rounded-2xl border border-white/10 bg-white/[0.035] p-4">
              <div className="text-xs text-white/42">{label}</div>
              <div className={cn("mt-3 inline-flex rounded-full border px-3 py-1 font-mono text-xs uppercase tracking-[0.14em]", tone)}>
                {value}
              </div>
            </div>
          ))}
          <Link
            href="/health"
            className="group rounded-2xl border border-emerald-300/20 bg-emerald-300/10 p-4 transition hover:-translate-y-0.5 hover:border-emerald-200/40 hover:bg-emerald-300/15"
          >
            <div className="text-xs text-emerald-100/60">运行状态</div>
            <div className="mt-3 inline-flex items-center gap-2 rounded-full border border-emerald-300/20 bg-black/20 px-3 py-1 font-mono text-xs uppercase tracking-[0.14em] text-emerald-100">
              查看健康页
              <ArrowRight className="h-3.5 w-3.5 transition group-hover:translate-x-0.5" />
            </div>
          </Link>
        </div>
      </div>

      <div id="preview" className="relative">
        <div className="absolute -inset-6 rounded-[2.5rem] bg-gradient-to-br from-cyan-400/16 via-blue-500/8 to-sky-300/10 blur-2xl" />
        <InterviewConsole />
      </div>
    </section>
  )
}

function InterviewConsole() {
  return (
    <div className="relative overflow-hidden rounded-[2rem] border border-white/12 bg-[#090b10]/88 p-4 shadow-2xl shadow-black/50 backdrop-blur-2xl sm:p-5">
      <div className="mb-5 flex items-center justify-between rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-3">
        <div>
          <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-cyan-100/70">
            面签预览
          </div>
          <div className="mt-1 text-sm font-semibold text-white">F-1 申请人会话</div>
        </div>
        <div className="rounded-full border border-emerald-300/20 bg-emerald-300/10 px-3 py-1 font-mono text-[10px] uppercase tracking-[0.16em] text-emerald-100">
          进行中
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[0.86fr_1.14fr]">
        <div className="space-y-3 rounded-3xl border border-white/10 bg-white/[0.035] p-4">
          <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-white/45">
            申请概况
          </div>
          {[
            ["签证", "F-1 Student"],
            ["学校", "Graduate Program"],
            ["资金", "Family Support"],
            ["线索", "Intent · Funding · Ties"],
          ].map(([label, value]) => (
            <div key={label} className="rounded-2xl border border-white/8 bg-black/20 px-3 py-3">
              <div className="text-[11px] text-white/38">{label}</div>
              <div className="mt-1 text-sm text-white/86">{value}</div>
            </div>
          ))}
        </div>

        <div className="space-y-3">
          {previewTurns.map((turn) => (
            <div key={turn.role} className="rounded-3xl border border-white/10 bg-white/[0.04] p-4">
              <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.2em] text-cyan-100/64">
                {turn.role}
              </div>
              <p className="text-sm leading-6 text-white/78">{turn.text}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function ProductPreview() {
  return (
    <section className="border-y border-white/10 py-12">
      <div className="grid gap-4 md:grid-cols-3">
        {[
          ["不是普通聊天", "围绕 DS-160 材料、签证类型和风险点推进。"],
          ["不是静态模板", "每轮回答都会改变后续追问和复盘方向。"],
          ["不是泛泛建议", "把材料、回答和复盘连成一条可继续训练的路径。"],
        ].map(([title, desc]) => (
          <div key={title} className="rounded-[1.75rem] border border-white/10 bg-white/[0.035] p-5">
            <ShieldCheck className="mb-4 h-5 w-5 text-cyan-100" />
            <h2 className="text-lg font-semibold tracking-[-0.03em] text-white">{title}</h2>
            <p className="mt-2 text-sm leading-6 text-white/52">{desc}</p>
          </div>
        ))}
      </div>
    </section>
  )
}

function Capabilities() {
  return (
    <section id="capabilities" className="py-20">
      <div className="mb-8 flex flex-col justify-between gap-4 md:flex-row md:items-end">
        <div>
          <p className="text-xs font-semibold tracking-[0.18em] text-cyan-100/62">
            核心能力
          </p>
          <h2 className="mt-3 text-3xl font-semibold tracking-[-0.05em] text-white sm:text-4xl">
            把面签训练拆成能看见、能复盘的能力。
          </h2>
        </div>
        <p className="max-w-xl text-sm leading-6 text-white/50">
          从授权、材料导入到结果复盘，用户看到的是一条完整训练路径；后台能力保持独立，不打断主体验。
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {capabilityCards.map((card) => {
          const Icon = card.icon
          return (
            <article
              key={card.title}
              className="group rounded-[1.75rem] border border-white/10 bg-white/[0.035] p-5 transition hover:-translate-y-1 hover:border-cyan-100/25 hover:bg-white/[0.055]"
            >
              <div className="mb-8 flex items-center justify-between">
                <div className="flex h-11 w-11 items-center justify-center rounded-2xl border border-white/10 bg-black/25">
                  <Icon className="h-5 w-5 text-cyan-100" />
                </div>
                <span className="text-[10px] font-semibold tracking-[0.12em] text-cyan-100/45">
                  {card.code}
                </span>
              </div>
              <h3 className="text-xl font-semibold tracking-[-0.04em] text-white">{card.title}</h3>
              <p className="mt-3 text-sm leading-6 text-white/54">{card.description}</p>
            </article>
          )
        })}
      </div>
    </section>
  )
}

function Workflow({ onStartExperience }: LoginDialogTriggerProps) {
  return (
    <section id="workflow" className="rounded-[2rem] border border-white/10 bg-white/[0.035] p-5 sm:p-8">
      <div className="mb-8 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-xs font-semibold tracking-[0.18em] text-cyan-100/62">
            使用路径
          </p>
          <h2 className="mt-3 text-3xl font-semibold tracking-[-0.05em] text-white">
            从授权到复盘，路径保持清楚。
          </h2>
        </div>
        <button
          type="button"
          onClick={onStartExperience}
          className="inline-flex items-center gap-2 text-sm font-semibold text-cyan-100 hover:text-white"
        >
          开始模拟面签
          <ArrowRight className="h-4 w-4" />
        </button>
      </div>

      <div className="grid gap-3 md:grid-cols-5">
        {workflowSteps.map((step, index) => (
          <div key={step} className="rounded-3xl border border-white/10 bg-black/20 p-4">
            <div className="font-mono text-xs text-cyan-100/70">0{index + 1}</div>
            <div className="mt-7 text-sm font-medium text-white">{step}</div>
          </div>
        ))}
      </div>
    </section>
  )
}

function FinalCta({
  showGithubLink,
  onStartExperience,
}: LandingPageProps & LoginDialogTriggerProps) {
  return (
    <footer className="py-16">
      <div className="rounded-[2rem] border border-white/10 bg-gradient-to-br from-white/[0.08] to-white/[0.025] p-6 text-center shadow-2xl shadow-black/30 sm:p-10">
        <p className="text-xs font-semibold tracking-[0.18em] text-cyan-100/62">
          准备开始
        </p>
        <h2 className="mx-auto mt-4 max-w-2xl text-3xl font-semibold tracking-[-0.05em] text-white sm:text-5xl">
          让申请人在真正面签前，先知道哪里还不够稳。
        </h2>
        <div className="mt-8 flex flex-col justify-center gap-3 sm:flex-row">
          <button
            type="button"
            onClick={onStartExperience}
            className="inline-flex h-12 items-center justify-center gap-2 rounded-full bg-white px-6 text-sm font-semibold text-black transition hover:bg-cyan-50"
          >
            开始模拟面签
            <ArrowRight className="h-4 w-4" />
          </button>
          {showGithubLink ? (
            <a href={PROJECT_INFO.githubUrl} target="_blank" rel="noreferrer" className="inline-flex h-12 items-center justify-center gap-2 rounded-full border border-white/12 px-6 text-sm font-semibold text-white/64 transition hover:border-white/25 hover:text-white">
              <Github className="h-4 w-4" />
              GitHub
            </a>
          ) : null}
        </div>
      </div>
    </footer>
  )
}
