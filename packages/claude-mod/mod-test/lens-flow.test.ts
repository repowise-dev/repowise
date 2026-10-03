// Runs under `claude plugin test` against the built bundle (npm run test:mod):
// tool.call returns what core returned, and the Flow tab shows the turn's
// Repowise call with what its reply was built from, its detail on a press.
// Core is the test's own stubs.
import { expect, test } from 'claude-code/testing'

const PANE = {
  plugin: 'repowise',
  component: 'Pane',
  surface: 'terminal',
  requestId: 'lens',
  viewport: { columns: 160, rows: 50 },
  props: { title: 'Lens', isFocused: true, bodyColumns: 140, placement: 'dock', scroll: { offset: 0, bodyRows: 40 }, view: {} },
} as const

// A get_risk reply recorded on an indexed Django copy (test/fixtures/flow/get_risk.json), cut to the fields Flow reads.
const REPLY = JSON.stringify({
  result: {
    targets: { 'django/db/models/query.py': { dependents_count: 12, co_change_partners_total: 25, contributor_count: 172, is_hotspot: true } },
    _meta: { indexed_commit: 'e78991410b78', index_age_days: 0, completeness: { capped: true }, omitted: { tokens: 1849 } },
  },
})

function stubs(on: any, seen: { core: any[] }): void {
  on('session.start', () => ({ cwd: '/work' }))
  on('session.cwd', () => ({ value: '/work' }))
  on('fs.exists', () => ({ value: false }))
  on('process.run', () => ({ value: { exitCode: 0, stdout: 'true\n', stderr: '' } }))
  on('settings.read', () => ({ value: {} }))
  on('mcp.connect', () => ({ value: { isConnected: true, server: 'plugin:repowise:repowise' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['drawn by Claude Code'] }))
  on('ui.log', () => ({}))
  on('turn.start', (_$: any, e: any) => ({ turnId: e.turnId }))
  on('tool.call', (_$: any, e: any) => {
    const out = { ref: 1, result: {}, text: e.tool === 'mcp__repowise__get_risk' ? REPLY : '' }
    seen.core.push(out)
    return out
  })
}

const RISK = { tool: 'mcp__repowise__get_risk', tool_use_id: 'toolu_01rw', targets: ['django/db/models/query.py'] }

test('tool.call returns what core returned; Flow shows the call, what its reply was built from, and its detail on a press', async ($: any, on: any) => {
  const seen = { core: [] as any[] }
  stubs(on, seen)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await $.turn.start({ text: '<system-reminder>be brief</system-reminder>add a comment above bulk_create', turnId: 't1' })
  const out = await $.tool.call(RISK as any)
  expect(out).toEqual(seen.core[0])
  await $.command.run({ command: 'lens' })
  const ui = await $.ui.mount(PANE)
  expect(await ui.find({ key: 'lens-flow' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /Working · \d+:\d\d/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Repowise 1 call' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: '"add a comment above bulk_create"' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /\d+ ms · 1 target · 12 direct dependents · index 0 days old/ })).toBeDefined()
  await ui.press({ key: 'lens-flow-toolu_01rw' })
  await ui.unmount()
  const open = await $.ui.mount(PANE)
  expect(await open.find({ type: 'Text', text: /1,849 tokens left out, restorable/ })).toBeDefined()
  expect(await open.find({ type: 'Text', text: /indexed at e78991410b78/ })).toBeDefined()
  await open.unmount()
})
