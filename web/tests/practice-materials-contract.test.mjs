import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { test } from "node:test"
import { fileURLToPath } from "node:url"

const rootDir = resolve(dirname(fileURLToPath(import.meta.url)), "..")

function read(relativePath) {
  return readFileSync(resolve(rootDir, relativePath), "utf8")
}

test("PracticeMaterialsDialog exposes seed textarea, EXAMPLE_CHIPS, 练习 disclaimer, and generate CTA", () => {
  const source = read("components/ds160/practice-materials-dialog.tsx")

  assert.match(source, /export function PracticeMaterialsDialog/)
  assert.match(source, /const EXAMPLE_CHIPS/)
  assert.match(source, /id="practice-materials-seed"/)
  assert.match(source, /Textarea/)
  assert.match(source, /EXAMPLE_CHIPS\.map/)
  assert.match(source, /练习用|仅练习|虚构练习材料/)
  assert.match(source, /生成练习材料/)
  assert.match(source, /正在生成…|生成/)
  assert.match(source, /onGenerate/)
  assert.match(source, /handleGenerate/)
})

test("AnalysisPanel cold-start CTA and practice brief wire practiceMaterialsEnabled", () => {
  const source = read("components/ds160/analysis-panel.tsx")

  assert.match(source, /practiceMaterialsEnabled/)
  assert.match(source, /onOpenPracticeMaterials/)
  assert.match(source, /用一段话生成练习材料/)
  assert.match(source, /练习材料说明/)
  assert.match(source, /PracticeGuideCard/)
  assert.match(source, /PracticeBriefCard/)
  assert.match(
    source,
    /Boolean\(practiceMaterialsEnabled\)\s*&&\s*Boolean\(hasSession\)\s*&&\s*hasNoMaterials/,
  )
})

test("MaterialsPanel exposes practice empty-state CTA and 练习 badge", () => {
  const source = read("components/ds160/materials-panel.tsx")

  assert.match(source, /practiceMaterialsEnabled/)
  assert.match(source, /onOpenPracticeMaterials/)
  assert.match(source, /用文字生成练习材料/)
  assert.match(source, /生成练习材料/)
  assert.match(source, /PracticeEmptyState/)
  // Practice badge on material cards / history entries
  assert.match(source, /练习/)
})

test("login workbench wires PracticeMaterialsDialog and defaults practice_materials_enabled true", () => {
  const source = read("app/login/page.tsx")

  assert.match(source, /import \{ PracticeMaterialsDialog \}/)
  assert.match(source, /practice_materials_enabled:\s*true/)
  assert.match(
    source,
    /const practiceMaterialsEnabled\s*=\s*[\s\S]*?appConfig\.practice_materials_enabled\s*!==\s*false/,
  )
  assert.match(source, /openPracticeMaterialsDialog/)
  assert.match(source, /onOpenPracticeMaterials=\{openPracticeMaterialsDialog\}/)
  assert.match(source, /practiceMaterialsEnabled=\{practiceMaterialsEnabled\}/)
  assert.match(source, /<PracticeMaterialsDialog/)
  assert.match(source, /open=\{practiceMaterialsDialogOpen\}/)
  assert.match(source, /onOpenChange=\{setPracticeMaterialsDialogOpen\}/)
  assert.match(source, /onGenerate=\{handlePracticeGenerate\}/)
  assert.match(
    source,
    /showPracticeMaterials=\{\s*appConfig\.practice_materials_enabled\s*!==\s*false\s*\}/,
  )
})

test("client createPracticeMaterialBundleStream hits practice material-bundles stream path", () => {
  const source = read("lib/api/client.ts")

  assert.match(source, /export async function createPracticeMaterialBundleStream/)
  assert.match(
    source,
    /\/v1\/sessions\/\$\{sessionId\}\/practice\/material-bundles\/stream/,
  )
  // Product path must not be only the debug stream.
  assert.match(source, /export async function createDebugMaterialBundleStream/)
  assert.match(
    source,
    /\/v1\/sessions\/\$\{sessionId\}\/debug\/material-bundles\/stream/,
  )

  const practiceFnStart = source.indexOf(
    "export async function createPracticeMaterialBundleStream",
  )
  assert.notEqual(practiceFnStart, -1)
  const practiceFnEnd = source.indexOf(
    "export async function createDebugMaterialBundleStream",
    practiceFnStart,
  )
  assert.notEqual(practiceFnEnd, -1)
  const practiceFn = source.slice(practiceFnStart, practiceFnEnd)
  assert.match(practiceFn, /practice\/material-bundles\/stream/)
  assert.doesNotMatch(practiceFn, /debug\/material-bundles\/stream/)
})

test("appConfig practice_materials_enabled defaults true on landing and login", () => {
  const landing = read("app/page.tsx")
  const login = read("app/login/page.tsx")

  assert.match(landing, /practice_materials_enabled:\s*true/)
  assert.match(login, /practice_materials_enabled:\s*true/)
})

test("admin labels practice materials as 产品功能 not pure debug", () => {
  const source = read("app/admin/page.tsx")

  assert.match(
    source,
    /\["practice_materials_enabled",\s*"练习材料生成（产品功能，默认开启）"\]/,
  )
  // Practice flag label should not be framed as debug-only tooling.
  const practiceLabelMatch = source.match(
    /\["practice_materials_enabled",\s*"([^"]+)"\]/,
  )
  assert.ok(practiceLabelMatch, "practice_materials_enabled label present")
  assert.match(practiceLabelMatch[1], /产品功能/)
  assert.doesNotMatch(practiceLabelMatch[1], /调试|debug/i)
})

test("use-session-workbench prefers practice stream and exposes dialog open/brief state", () => {
  const source = read("hooks/use-session-workbench.ts")

  assert.match(source, /openPracticeMaterialsDialog/)
  assert.match(source, /setPracticeMaterialsBrief/)
  assert.match(source, /setPracticeMaterialsDialogOpen/)
  assert.match(source, /createPracticeMaterialBundleStream/)
  assert.match(source, /createDebugMaterialBundleStream/)
  assert.match(source, /handlePracticeGenerate/)

  // Product path tries practice stream first; debug is 403 fallback only.
  const preferStart = source.indexOf(
    "Product path: practice materials",
  )
  assert.notEqual(
    preferStart,
    -1,
    "documents product-first practice stream preference",
  )
  const preferBlock = source.slice(preferStart, preferStart + 1200)
  assert.match(preferBlock, /createPracticeMaterialBundleStream/)
  assert.match(preferBlock, /status !== 403/)
  assert.match(preferBlock, /createDebugMaterialBundleStream/)

  // Practice stream call appears before debug fallback in the try/catch.
  const practiceCall = preferBlock.indexOf("createPracticeMaterialBundleStream")
  const debugCall = preferBlock.indexOf("createDebugMaterialBundleStream")
  assert.notEqual(practiceCall, -1)
  assert.notEqual(debugCall, -1)
  assert.ok(
    practiceCall < debugCall,
    "practice stream must be preferred over debug stream",
  )
})
