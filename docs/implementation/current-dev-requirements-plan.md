# 当前开发需求总结与实施计划

> 汇总截止：2026-07-20 会话  
> 分支：本地 `main`（源自 `simplify/agent-runtime-core` tip，相对 `origin/main` 超前大量提交，**尚未全部 push**）  
> 范围：后端 runtime 修复 · 前端 bug · 练习材料产品化 · 主题 · Open Design

---

## 1. 一页总览

| 领域 | 状态 | 下一动作 |
|------|------|----------|
| Git 分支整理（方案 1 归档） | 本地完成 | 可选 push tag/main |
| 后端 runtime 缺陷 B1–B8 | 本地代码已合 | commit/push |
| 前端功能 bug F1–F6 | 本地代码已合 | 真机验收 + commit |
| 亮色主题默认 | **完成**（工作台 light 默认） | Landing 仍暗色营销（P2） |
| 练习材料产品化 | **完成**（BE 默认 ON + FE Dialog/入口/badge） | 真机走通生成流 |
| 右侧引导一键生成 | **完成**（AnalysisPanel 主卡一点开 Dialog） | 真机验收 |
| 生成后中文总结 | **完成**（`user_summary_zh` + briefs 卡） | 真机验收 |
| 审美 Wave 1/2 落代码 | **Wave1 完成；Wave2 主体完成** | 可选共享 status 模块 |
| Open Design | 已建项目 + 示意稿 | 当设计板，非真站 |

---

## 2. 已完成（勿重复开干）

### 2.1 仓库 / 分支

- 本地 `main` = 最终线 tip（原 `simplify/agent-runtime-core`）
- 归档 tag：`archive/main-2026-05-24`、`archive/agent-runtime-graph-wip-2026-05-31`
- **未 push** 归档 tag 与更新后的 `main`

### 2.2 后端 runtime（B1–B8）

见 `docs/implementation/backend-runtime-defect-fix-plan.md`。

| 包 | 内容 |
|----|------|
| B1 | Quality guard 不卡死；session 锁；失败 rebuild claims |
| B2 | Case memory `rebuild_and_persist` / invalidate |
| B3 | Tombstone 不复活；gate 绑 understanding；`GET .../documents` |
| B4 | OpenAI 兼容口 ownership + 原子配额 |
| B5 | 终端 session 不重开 phase |
| B6 | Report `requested_documents`；proof 双字段 |
| B7 | 公开 `agent_runtime` 固定 native |
| B8 | Admin 限流、XFF 信任开关、wx ticket 等 |

配置：`MATERIAL_UNDERSTANDING_REQUIRED`、`TRUST_X_FORWARDED_FOR`。

### 2.3 前端 bug（F1–F6）

见 `docs/implementation/frontend-aesthetic-bug-review.md`。

| 包 | 内容 |
|----|------|
| F1 | `listSessionDocuments` + content_url 重写 |
| F2 | 材料理解轮询走公开 list，完成刷新 report |
| F3 | 恢复会话拉材料 |
| F4 | Report 软加载、403 文案、stream 断连补 transcript |
| F5 | WX 轮询 / 重试 / send guard |
| F6 | humanize 收敛、mapper 放宽、双 proof |

合同测试：`web` 下约 67 passed（当时快照）。

### 2.4 亮色主题（部分）

- 默认 `defaultTheme="light"`
- 浅色 token 调整；顶栏 `WorkbenchThemeToggle`（浅色/深色）
- Landing 仍写死暗色营销（非本轮范围除非另开需求）

### 2.5 Open Design

| 项 | 值 |
|----|-----|
| 项目 | **DS-160 Web Frontend** `22e60ae5-30f3-41a4-a675-ae35b1d3d217` |
| 绑定 | `import-folder` → 真实 `web/` 源码 |
| 示意稿 | `web/od-design/*`（**不是**部署站，是 HTML 模拟） |
| 入口说明 | `od-design/00-read-this-first.html` |
| 设计系统 | 已挂 Apple（融合方向见 `08-apple-fusion.html`） |
| Daemon/UI | tools-dev 时约 `daemon:36325` / `web:35231`（以 `tools-dev status` 为准） |

---

## 3. 产品需求状态（2026-07-20 更新）

### 3.1 P0 — 练习材料产品化 ✅

| 项 | 状态 | 说明 |
|----|------|------|
| `practice_materials_enabled` 默认 ON | ✅ | 与 debug console 解耦 |
| 路由 `/practice/material-bundles[+stream]` | ✅ | debug 路由仍独立限权 |
| `user_summary_zh` + `document_briefs_zh` | ✅ | AI schema + fallback |
| `PracticeMaterialsDialog` | ✅ | 大输入框 + 示例 chips + 免责 |
| 右侧冷启动主卡一点开 Dialog | ✅ | AnalysisPanel |
| 材料区 CTA + 「练习」badge | ✅ | MaterialsPanel |
| workbench 优先 practice stream | ✅ | 403 可回退 debug（兼容） |
| FE contract + BE integration tests | ✅ | web 75 pass；practice API 6+6 pass |

### 3.2 P0 — 冷启动引导 ✅

有会话无材料 → 右侧「用一段话生成练习材料」；有 brief → 中文说明 + 重新生成。

### 3.3 P0/P1 — 生成后中文总结 ✅

`PracticeBriefCard` 展示 `user_summary_zh` + 文档 briefs；不展示 expected_findings。

### 3.4 P1 — 审美落代码

| Wave | 内容 | 状态 |
|------|------|------|
| F7 | 名/logo、Auth 暗色 glass、Noto Sans SC、token、去铃铛/Unsplash/Mac 点 | ✅ |
| F8 | Case Board 层级、Empty/Skeleton、顶栏中文 | ✅ 主体 |
| 共享 status/risk 模块 | history/report 复用 | 可选后续 |
| Apple 融合 | grouped + cyan | 部分（light accent） |

### 3.5 P2 — 其它（未做 / 非本轮）

| 项 | 说明 |
|----|------|
| Landing 亮色版 | 首页仍暗色营销 |
| WX 练习材料 | 桌面先；小程序 Sheet 二期 |
| 冲突场景二级菜单 | 首版不暴露 |
| Git push / 默认分支 main | 运维，需你确认 |
| 限流 practice LLM | 可选 |

---

## 4. Open Design 内容清单（用途）

| 文件 | 用途 | 是否真前端 |
|------|------|------------|
| `00-read-this-first.html` | 说明 OD 示意 vs 源码 | 文档 |
| `index.html` | 示意导航 | 模拟 |
| `01-design-tokens.html` | Token / chip | 模拟 |
| `02 / 05` | Auth 目标与对比 | 模拟 |
| `03 / 06` | Case Board / 工作台壳 | 模拟 |
| `07` | Landing 连续 | 模拟 |
| `08-apple-fusion.html` | Apple×DS-160 | 模拟 |
| 文件树 `app/` `components/` | **真实源码** | 是（只读/对照） |

**原则：** 功能与真交互在 Next 里做；OD 只拍视觉与信息架构。

---

## 5. 实施计划（推荐顺序）

### Phase 0 — 基线固化（0.5d）

1. 本地跑后端关键 pytest + `web` contract tests  
2. 人工：light 主题、材料上传轮询、恢复会话  
3. 按需 commit 分批（backend / frontend-bug / theme），**push 另议**

### Phase 1 — 练习材料 MVP（前端为主，1.5–2.5d）**【当前主航道】**

| 步 | 交付 | 后端 |
|----|------|------|
| 1.1 | `PracticeMaterialsDialog`：免责 + 大输入框 + 生成 + 进度 | 否 |
| 1.2 | 材料区按钮 + **右侧无材料主卡一点即开 Dialog** | 否 |
| 1.3 | 接 `runDebugMaterialBundle`（默认 visa normal scenario） | 否 |
| 1.4 | 成功后材料列表 + 「练习」badge | 否 |
| 1.5 | **中文说明卡（字段级）** state 存 brief，右侧/材料顶展示 | 否 |
| 1.6 | 开关：读现有 debug_material 或 app config；关则隐藏入口 | 配置可读即可 |

**验收：** 新会话 → 点右侧 → 填中文描述 → 生成 → 材料出现 + 中文列表可读。

### Phase 2 — 中文总述 + 产品开关（后端小改 + 前端，0.5–1d）

| 步 | 交付 |
|----|------|
| 2.1 | AI 输出增加 `user_summary_zh`（或 `practice_brief_zh`） |
| 2.2 | API 响应带出；前端总结卡置顶显示 |
| 2.3 | （建议）`practice_materials_enabled` 与 debug console 解耦 |

### Phase 3 — 体验打磨（1d）

| 步 | 交付 |
|----|------|
| 3.1 | 示例 prompt chips（按签证） |
| 3.2 | 重新生成 / 已有真实材料时的确认文案 |
| 3.3 | 高级：冲突练习包折叠（可选） |
| 3.4 | WX Sheet 版（可选） |

### Phase 4 — 审美落代码（2–5d，可并行排期）

| 步 | 交付 |
|----|------|
| 4.1 | Wave 1：名 logo Auth token 去噪（对齐 OD 01/02/05） |
| 4.2 | Wave 2：Case Board 层级 + status 模块（03/06/08） |
| 4.3 | Landing 可选 light 或保持 dark marketing |

### Phase 5 — 发布与仓库

| 步 | 交付 |
|----|------|
| 5.1 | push archive tags + main（FF） |
| 5.2 | GitHub default branch = main |
| 5.3 | 生产：`MATERIAL_UNDERSTANDING_REQUIRED`、练习材料开关、限流策略 |

---

## 6. 依赖与风险

```text
Phase 1（练习材料 UI） ──不依赖──► 审美 Wave
Phase 1 ──可选增强──► Phase 2 user_summary_zh
Phase 1 验收 ──建议先于──► 大规模 push
材料理解轮询（已修） ──被 Phase 1 依赖──► 生成后状态刷新
debug_material 开关 ──若生产关──► 入口全隐：上线前必须配开关策略
```

| 风险 | 缓解 |
|------|------|
| 生成贵/慢 | 进度 UI；后续限流 |
| 英文材料难读 | Phase 1.5 + 2.1 中文卡 |
| 用户当真实材料 | 固定免责 + 「练习」badge |
| OD 与真站混淆 | 以 00-read-this-first 为准；功能只在 Next 验 |

---

## 7. 建议立刻开干的包（下一会话）

**唯一主包：Phase 1（练习材料 MVP + 右侧引导 + 字段级中文总结）**

不做：冲突场景复杂菜单、WX、完整审美重做、大改后端生成器。

可选同周：Phase 2.1 `user_summary_zh`。

---

## 8. 验收清单（产品）

- [x] 新用户进工作台，右侧一点打开大输入框（无多余跳转） — 代码已接  
- [x] 中文描述生成后，材料库有文件且带「练习」标 — 代码已接  
- [x] 中文总结卡能看懂学校/资金/姓名等关键点 — 代码已接  
- [x] 浅色主题默认可读；可切回深色  
- [x] 上传真实材料路径仍可用（未改破坏）  
- [x] 开关关闭时入口不可见（`practice_materials_enabled === false`）  
- [ ] **真机**：填 seed → stream → 材料 + 中文 brief 端到端（需运行时 + LLM/mock）

---

## 9. 相关文档索引

| 文档 | 内容 |
|------|------|
| `docs/implementation/backend-runtime-defect-fix-plan.md` | 后端缺陷与 B1–B8 |
| `docs/implementation/frontend-aesthetic-bug-review.md` | 前端 bug/审美/OD |
| `docs/implementation/current-dev-requirements-plan.md` | **本文：总需求 + 实施顺序** |
| `web/od-design/*` | OD 示意稿 |

---

## 10. 决策记录（已定）

1. 练习材料 = 调试 bundle 能力产品化，LLM 生成，seed 驱动。  
2. 首版 UI：大输入框为主，场景自动 normal。  
3. 右侧冷启动一点即弹生成。  
4. 生成后必须有中文说明（先字段，后人话）。  
5. 用户偏好亮色：工作台默认 light（已改）；Landing 另议。  
6. OD 仅设计板，真交互以 Next 为准。  
