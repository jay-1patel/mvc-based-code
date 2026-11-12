#!/usr/bin/env node
/**
 * Token guard for the remake.
 *
 * The historical frontend bug was markup referencing colour tokens that
 * were never defined, which silently rendered transparent. This check
 * makes that class of bug fail loudly:
 *
 *   1. Every CSS variable the Tailwind semantic palette references must
 *      be defined in src/theme/tokens.css (and vice versa — no orphans).
 *   2. Remake-owned files must not contain colour literals (hex / rgb /
 *      hsl) — literals live in the token file only.
 *   3. Any colour utility in a remake-owned file whose name starts with
 *      a semantic stem (bg-att…, text-tx…, border-chip…) must be a real
 *      defined semantic colour — catches typos like `bg-attbgg`, which
 *      Tailwind would otherwise silently drop.
 *
 * Run directly (`npm run check:tokens`) or through vitest
 * (src/test/tokens.test.ts). Exits non-zero / returns problems on failure.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

export const REMAKE_FILES = [
  'src/components/remake/ui.tsx',
  'src/components/layout/app-shell.tsx',
  'src/components/layout/page-header.tsx',
  'src/components/tenants/today-panel.tsx',
  'src/components/tenants/super-today.tsx',
  'src/components/tenants/chats-panel.tsx',
  'src/components/tenants/bot-menu-panel.tsx',
  'src/components/tenants/tenant-list.tsx',
  // Phase 2: business-data views.
  'src/components/tenants/records-panel.tsx',
  'src/components/tenants/uploads-panel.tsx',
  'src/components/tenants/website-knowledge-panel.tsx',
  'src/components/tenants/customers-panel.tsx',
  'src/components/tenants/orders-panel.tsx',
  'src/components/tenants/distributors-panel.tsx',
  'src/components/tenants/info-page-panel.tsx',
  'src/components/onboarding/onboarding-kit-widget.tsx',
  'src/components/onboarding/onboarding-review-modal.tsx',
  // Phase 3: comms + grow views.
  'src/components/tenants/chat-history-panel.tsx',
  'src/components/tenants/complaints-panel.tsx',
  'src/components/tenants/callbacks-panel.tsx',
  'src/components/tenants/campaigns-panel.tsx',
  'src/components/tenants/intents-editor.tsx',
  'src/components/tenants/admin-chat.tsx',
  'src/components/tenants/test-and-smoke.tsx',
  'src/components/tenants/layers-panel.tsx',
  'src/components/tenants/chat-attachment.tsx',
  // Phase 4: manage + super views + auth surfaces.
  'src/components/auth/login-view.tsx',
  'src/components/team/team-screen.tsx',
  'src/components/team/audit-history.tsx',
  'src/components/team/errors-panel.tsx',
  'src/components/team/api-onboarding-review.tsx',
  'src/components/tenants/tokens-panel.tsx',
  'src/components/tenants/versions-panel.tsx',
  'src/components/tenants/profile-editor.tsx',
  'src/components/tenants/tenant-approvals-panel.tsx',
  'src/components/tenants/api-onboarding-panel.tsx',
  'src/components/tenants/menu-editor.tsx',
  'src/components/tenants/offerings-panel.tsx',
  'src/components/onboarding/register-wizard.tsx',
  // Final sweep: App-level NotPermitted / unknown-view notices.
  'src/App.tsx',
]

const STEMS = [
  'paper', 'card', 'sidebg', 'inputbg', 'tx', 'line', 'acc', 'att', 'bad',
  'ok', 'chip', 'bubblein', 'switchoff', 'onacc',
]

export function runTokenCheck(root) {
  const problems = []
  const tokensCss = readFileSync(join(root, 'src/theme/tokens.css'), 'utf8')
  const definedVars = new Set(
    [...tokensCss.matchAll(/--([a-z0-9-]+)\s*:/g)].map((m) => m[1]),
  )

  const config = readFileSync(join(root, 'tailwind.config.ts'), 'utf8')
  const semanticBlock = config.match(/const semantic = \{([\s\S]*?)\n\}/)?.[1] ?? ''
  const semantic = new Map(
    [...semanticBlock.matchAll(/(\w+):\s*v\('([a-z0-9-]+)'\)/g)].map((m) => [m[1], m[2]]),
  )
  if (!semantic.size) problems.push('tailwind.config.ts: semantic palette not found')

  for (const [key, varName] of semantic) {
    if (!definedVars.has(varName)) {
      problems.push(`semantic colour "${key}" references --${varName}, not defined in tokens.css`)
    }
  }
  for (const varName of definedVars) {
    if (![...semantic.values()].includes(varName)) {
      problems.push(`tokens.css defines --${varName} but no semantic colour uses it`)
    }
  }

  const colourUtility = /(?:^|[\s"'`])((?:[a-z]+:)*)(bg|text|border|ring|divide|from|via|to)-([a-z0-9]+(?:-[a-z0-9]+)*)(?:\/\d+)?(?=[\s"'`]|$)/g
  for (const rel of REMAKE_FILES) {
    let text
    try {
      text = readFileSync(join(root, rel), 'utf8')
    } catch {
      problems.push(`${rel}: remake file missing`)
      continue
    }
    if (/#[0-9a-fA-F]{3,8}\b/.test(text) || /\b(?:rgb|hsl)a?\(/.test(text)) {
      problems.push(`${rel}: contains a colour literal — literals belong in src/theme/tokens.css`)
    }
    for (const m of text.matchAll(colourUtility)) {
      const name = m[3]
      // The legacy palette's `accent-*` scale shares a prefix with the
      // semantic `acc` — it is a defined legacy colour, not a typo.
      if (name.startsWith('accent')) continue
      if (STEMS.some((s) => name === s || name.startsWith(s)) && !semantic.has(name)) {
        problems.push(`${rel}: class "${m[2]}-${name}" looks like a semantic colour but is not defined`)
      }
    }
  }
  return problems
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const problems = runTokenCheck(process.cwd())
  if (problems.length) {
    console.error(problems.map((p) => `✗ ${p}`).join('\n'))
    process.exit(1)
  }
  console.log('✓ token check passed')
}
