import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import test from "node:test"

const rootDir = resolve(import.meta.dirname, "..")

function readProjectFile(path) {
  return readFileSync(resolve(rootDir, path), "utf8")
}

test("logout broadcasts to every auth hook instance", () => {
  const source = readProjectFile("hooks/use-auth.ts")

  assert.match(source, /AUTH_LOGOUT_EVENT = "auth:logout"/)
  assert.match(source, /window\.addEventListener\(AUTH_LOGOUT_EVENT, handleLogout\)/)
  assert.match(source, /window\.dispatchEvent\(new CustomEvent\(AUTH_LOGOUT_EVENT\)\)/)
  assert.match(source, /clearAuthState\(\)/)
})

test("workbench exposes current-key logout from session and pre-session chrome", () => {
  const source = readProjectFile("app/login/page.tsx")

  assert.match(source, /const \{ userProfile, accessKeyQuota, logout \} = useAuth\(\)/)
  assert.match(source, /const router = useRouter\(\)/)
  assert.match(source, /await logout\(\)/)
  assert.match(source, /router\.replace\("\/"\)/)
  assert.match(source, /onLogout=\{\(\) => void handleLogoutToHome\(\)\}/)
  assert.match(source, /退出当前 Key/)
})

test("top bar and settings panel provide switch-account actions", () => {
  const topBar = readProjectFile("components/ds160/top-bar.tsx")
  const settings = readProjectFile("components/ds160/settings-panel.tsx")

  assert.match(topBar, /onLogout: \(\) => void/)
  assert.match(topBar, /退出当前 Key/)
  assert.match(settings, /当前授权 Key/)
  assert.match(settings, /退出当前 Key \/ 切换账号/)
  assert.match(settings, /Key ID：\$\{accessKeyQuota\.key_id\}/)
})
