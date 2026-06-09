import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { test } from "node:test"
import { fileURLToPath } from "node:url"
import ts from "typescript"

const rootDir = resolve(dirname(fileURLToPath(import.meta.url)), "..")

function read(relativePath) {
  return readFileSync(resolve(rootDir, relativePath), "utf8")
}

function loadTypeScriptModule(relativePath) {
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
  const evaluate = new Function("exports", "module", compiled.outputText)
  evaluate(cjsModule.exports, cjsModule)
  return cjsModule.exports
}

const share = loadTypeScriptModule("lib/access-key-share.ts")

test("access key share links use a hash parameter and parse hash/query variants", () => {
  const link = share.buildAccessKeyShareLink("ds160_test_secret", "https://example.test")
  assert.equal(link, "https://example.test/#ds160_access_key=ds160_test_secret")

  assert.equal(
    share.parseSharedAccessKeyFromLocation({
      hash: "#ds160_access_key=from_hash",
      search: "?ds160_access_key=from_query",
    }),
    "from_hash",
  )
  assert.equal(
    share.parseSharedAccessKeyFromLocation({ hash: "", search: "?access_key=from_query" }),
    "from_query",
  )
})

test("admin console exposes direct key copy and one-click share link actions", () => {
  const source = read("app/admin/page.tsx")
  assert.match(source, /buildAccessKeyShareLink/)
  assert.match(source, /copySecretForKey/)
  assert.match(source, /copyShareLinkForKey/)
  assert.match(source, /复制 Key/)
  assert.match(source, /一键分享链接/)
})

test("landing and guarded login can enable shared key without username input", () => {
  for (const file of [
    "components/landing/landing-login-dialog.tsx",
    "components/ds160/auth-guard.tsx",
    "components/wx/wx-auth-screen.tsx",
  ]) {
    const source = read(file)
    assert.match(source, /parseSharedAccessKeyFromLocation/)
    assert.match(source, /stripSharedAccessKeyFromCurrentUrl/)
    assert.doesNotMatch(source, /name="displayName"/)
  }
})

test("workbench settings own display-name editing", () => {
  const settings = read("components/ds160/settings-panel.tsx")
  const loginPage = read("app/login/page.tsx")
  const authHook = read("hooks/use-auth.ts")

  assert.match(authHook, /updateUserProfile/)
  assert.match(settings, /工作台用户名/)
  assert.match(settings, /onUpdateUserDisplayName/)
  assert.match(loginPage, /onUpdateUserDisplayName=\{handleUpdateUserDisplayName\}/)
})

test("key-authenticated users can copy their own share link from settings", () => {
  const settings = read("components/ds160/settings-panel.tsx")
  const loginPage = read("app/login/page.tsx")
  const authHook = read("hooks/use-auth.ts")

  assert.match(authHook, /AUTH_CURRENT_ACCESS_KEY_STORAGE_KEY/)
  assert.match(authHook, /sessionStorage\.setItem/)
  assert.doesNotMatch(authHook, /localStorage\.setItem\(AUTH_CURRENT_ACCESS_KEY_STORAGE_KEY/)
  assert.match(authHook, /response\.access_key_quota/)
  assert.match(authHook, /buildAccessKeyShareLink\(trimmedKey\)/)
  assert.match(authHook, /maskAccessKeyForDisplay\(trimmedKey\)/)
  assert.match(loginPage, /currentAccessKeyShareLink/)
  assert.match(loginPage, /maskedCurrentAccessKey/)
  assert.match(loginPage, /navigator\.clipboard\.writeText\(currentAccessKeyShareLink\)/)
  assert.match(loginPage, /onCopyCurrentKeyShareLink=\{handleCopyCurrentKeyShareLink\}/)
  assert.match(loginPage, /currentAccessKeyShareLink=\{currentAccessKeyShareLink\}/)
  assert.doesNotMatch(loginPage, /window\.prompt/)
  assert.match(settings, /复制本 Key 分享链接/)
  assert.match(settings, /onCopyCurrentKeyShareLink/)
  assert.match(settings, /当前浏览器没有保留本次登录的明文 Key/)
  assert.match(settings, /manualKeyShareLink/)
  assert.match(settings, /手动复制当前 Key 分享链接/)
  assert.match(settings, /分享链接等同于持有该 Key/)
})
