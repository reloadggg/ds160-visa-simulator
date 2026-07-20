import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { test } from "node:test"
import { fileURLToPath } from "node:url"
import ts from "typescript"

const rootDir = resolve(dirname(fileURLToPath(import.meta.url)), "..")

function loadTypeScriptModule(relativePath, runtimeRequires = {}) {
  const filename = resolve(rootDir, relativePath)
  const source = readFileSync(filename, "utf8")
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      esModuleInterop: true,
    },
    fileName: filename,
  })
  const cjsModule = { exports: {} }
  const requireShim = (specifier) => {
    if (Object.hasOwn(runtimeRequires, specifier)) {
      return runtimeRequires[specifier]
    }
    throw new Error(`Unexpected runtime require from ${relativePath}: ${specifier}`)
  }
  const evaluate = new Function("exports", "module", "require", compiled.outputText)
  evaluate(cjsModule.exports, cjsModule, requireShim)
  return cjsModule.exports
}

const apiConfig = loadTypeScriptModule("lib/api/config.ts")
const mappers = loadTypeScriptModule("lib/api/mappers.ts", {
  "./config": apiConfig,
})

test("material package list mapper marks validated F-1 templates as importable product packages", () => {
  const mapped = mappers.mapMaterialPackageListResponse({
    packages: [
      {
        package_id: "pkg-f1-valid",
        label: "F-1 parent-sponsored consistent case",
        status: "ready",
        status_label: "可导入",
        validation_status: "passed",
        source_validation_session_id: "sess-source",
        demo_template_id: "f1_parent_sponsored_consistent_v1",
        archive_source_reason: "validated_f1_demo_material_package",
        intent: "pass_oriented_customer_demo",
        visa_family: "f1",
        document_count: 1,
        document_types: ["i20"],
        documents: [
          {
            document_id: "doc-i20",
            filename: "i20.pdf",
            document_type: "i20",
          },
        ],
      },
      {
        package_id: "pkg-failed",
        label: "Incomplete package",
        status: "failed",
        status_label: "失败不可导入",
        validation_status: "incomplete",
        document_count: 0,
        document_types: [],
        documents: [],
      },
    ],
  })

  assert.equal(mapped.packages[0].template_id, "f1_parent_sponsored_consistent_v1")
  assert.equal(mapped.packages[0].is_validated_template, true)
  assert.equal(mapped.packages[0].is_importable, true)
  assert.equal(mapped.packages[0].documents[0].document_type_label, "I-20 表格")
  assert.equal(mapped.packages[1].is_validated_template, false)
  assert.equal(mapped.packages[1].is_importable, false)
})

test("settings exposes material package selection as product UI outside debug tools", () => {
  const source = readFileSync(
    resolve(rootDir, "components/ds160/settings-panel.tsx"),
    "utf8",
  )

  const productStart = source.indexOf("Validated template / case package")
  assert.notEqual(productStart, -1)

  // Product section ends when practice-materials and/or debug tools open.
  // Prefer the joint gate; fall back to pure debug-only for older layouts.
  let productEnd = source.indexOf(
    "{showPracticeMaterials || showDebugTools ?",
    productStart,
  )
  if (productEnd === -1) {
    productEnd = source.indexOf("{showDebugTools ?", productStart)
  }
  assert.notEqual(productEnd, -1)

  const productBlock = source.slice(productStart, productEnd)
  assert.match(productBlock, /material package archive/)
  assert.match(productBlock, /onRefreshMaterialPackages/)
  assert.match(productBlock, /onImportMaterialPackage|handleImportPackage/)
  assert.match(productBlock, /isImportableMaterialPackage\(item\)/)
  assert.match(productBlock, /isValidatedMaterialTemplatePackage\(item\)/)
  assert.doesNotMatch(productBlock, /debug bundle|debug_bundle|调试合成材料|调试材料包/i)

  // Joint practice/debug section is product-capable (not pure debug-only).
  const jointSection = source.slice(productEnd, productEnd + 800)
  assert.match(jointSection, /showPracticeMaterials/)
  assert.match(jointSection, /产品功能|练习材料生成/)
})

test("imported archive materials are tagged as case packages, not debug bundle material", () => {
  const hookSource = readFileSync(
    resolve(rootDir, "hooks/use-session-workbench.ts"),
    "utf8",
  )
  const importStart = hookSource.indexOf("function materialPackageDocumentToMaterial")
  assert.notEqual(importStart, -1)
  const importEnd = hookSource.indexOf("function buildDebugBundleFinalMessage", importStart)
  assert.notEqual(importEnd, -1)
  const importMapper = hookSource.slice(importStart, importEnd)

  assert.match(importMapper, /material_package_id: result\.package_id/)
  assert.match(importMapper, /material_package_source: "archive_import"/)
  assert.match(importMapper, /synthetic_bundle_id: null/)
  assert.doesNotMatch(importMapper, /debug_bundle_scenario/)
})
