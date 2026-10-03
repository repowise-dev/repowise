// Runs under `claude plugin test` against the built bundle, from a temporary
// copy of plugins/claude-code (npm run test:mod). Not shipped in the plugin.
import { expect, test } from 'claude-code/testing'

const BAND = {
  plugin: 'repowise',
  component: 'AbovePrompt',
  surface: 'terminal',
  viewport: { columns: 100, rows: 30 },
  props: {
    hasSurvey: false,
    isWorking: false,
    maxRows: 10,
    bodyColumns: 100,
    scroll: { offset: 0, bodyRows: 10 },
    view: {},
  },
} as const

const ENGINE_DRAWING = { type: 'Text', props: {}, children: ['drawn by Claude Code'] }
const HINT = 'index this repo for Lens: repowise init --no-prose --yes'

/** A git work tree with no index: the walk finds no state file, the MCP server connects. */
function noIndexStubs(on: any, inWorkTree = true): void {
  on('session.start', () => ({ cwd: '/work' }))
  on('session.cwd', () => ({ value: '/work' }))
  on('fs.exists', () => ({ value: false }))
  on('process.run', () => ({
    value: inWorkTree
      ? { exitCode: 0, stdout: 'true\n', stderr: '' }
      : { exitCode: 128, stdout: '', stderr: 'fatal: not a git repository' },
  }))
  on('mcp.connect', () => ({ value: { isConnected: true, server: 'plugin:repowise:repowise' } }))
  on('ui.render', () => ENGINE_DRAWING)
  on('turn.complete', () => ({ text: '' }))
}

async function waitFor(check: () => Promise<boolean>): Promise<boolean> {
  for (let i = 0; i < 40; i++) {
    if (await check()) return true
    await new Promise((resolve) => setTimeout(resolve, 25))
  }
  return false
}

/** Lens's row in the band, and whether the drawing beneath it survived. */
async function band($: any): Promise<{ lens: string | undefined; engineKept: boolean }> {
  const ui = await $.ui.mount(BAND)
  const found = await ui.find({ type: 'Text', text: /repowise|Lens|index/ })
  const engine = await ui.find({ type: 'Text', text: 'drawn by Claude Code' })
  await ui.unmount()
  return { lens: found?.children?.join(''), engineKept: engine !== undefined }
}

test('registers and draws nothing in the band at rest', async ($, on) => {
  on('ui.render', () => ENGINE_DRAWING)
  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeDefined()
  expect(await ui.find({ key: 'lens-band' })).toBeUndefined()
  await ui.unmount()
})

test('the no-index hint shows once per session, above what the band already held', async ($, on) => {
  noIndexStubs(on)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  expect(await waitFor(async () => (await band($)).lens === HINT)).toBe(true)
  expect((await band($)).engineKept).toBe(true)

  // The hint retires after the first turn ends, and a re-discovery does not bring it back.
  await $.turn.complete({ turnId: 't1', answer: 'ok', durationMs: 1, isAborted: false, usage: null })
  expect((await band($)).lens).toBeUndefined()
  await $.turn.complete({ turnId: 't2', answer: 'ok', durationMs: 1, isAborted: false, usage: null })
  await new Promise((resolve) => setTimeout(resolve, 200))
  expect(await band($)).toEqual({ lens: undefined, engineKept: true })
})

test('a subagent turn does not retire the hint', async ($, on) => {
  noIndexStubs(on)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  expect(await waitFor(async () => (await band($)).lens !== undefined)).toBe(true)
  await $.turn.complete({ turnId: 't1', agentId: 'sub-1', answer: 'ok', durationMs: 1, isAborted: false, usage: null })
  expect((await band($)).lens).toBe(HINT)
})

test('outside a git work tree the band stays empty', async ($, on) => {
  noIndexStubs(on, false)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await new Promise((resolve) => setTimeout(resolve, 200))
  expect(await band($)).toEqual({ lens: undefined, engineKept: true })
})
