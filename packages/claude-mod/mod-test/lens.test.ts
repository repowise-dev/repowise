// Runs under `claude plugin test` against the built bundle, from a temporary
// copy of plugins/claude-code (npm run test:mod). Not shipped in the plugin.
import { expect, test } from 'claude-code/testing'

const OWN_TOOL = 'mcp__plugin_repowise_repowise__get_context'

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

/** A repo with no index: the walk finds no state file, the MCP server connects. */
function noIndexStubs(on: any): void {
  on('session.start', () => ({ cwd: '/work' }))
  on('session.cwd', () => ({ value: '/work' }))
  on('fs.exists', () => ({ value: false }))
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

async function bandText($: any): Promise<string | undefined> {
  const ui = await $.ui.mount(BAND)
  const found = await ui.find({ type: 'Text', text: /repowise|Lens|index/ })
  await ui.unmount()
  return found?.children?.join('')
}

test('registers and draws nothing in the band at rest', async ($, on) => {
  on('ui.render', () => ENGINE_DRAWING)
  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeDefined()
  expect(await ui.find({ key: 'lens-band' })).toBeUndefined()
  await ui.unmount()
})

test("Claude's tool calls keep the engine's verdict, whatever it is", async ($, on) => {
  let verdict: 'allow' | 'ask' | 'deny' = 'ask'
  on('tool.check', () => ({ decision: verdict }))
  for (const v of ['ask', 'allow', 'deny'] as const) {
    verdict = v
    expect(await $.tool.check({ tool: 'Bash', input: { command: 'ls' }, tool_use_id: 'toolu_01' })).toEqual({ decision: v })
  }
  // Same tool Lens may approve, but Claude's call: untouched.
  verdict = 'ask'
  expect(await $.tool.check({ tool: OWN_TOOL, input: {}, tool_use_id: 'toolu_02' })).toEqual({ decision: 'ask' })
  // A plugin-shaped id fired by the engine, not by Lens: still untouched.
  expect(await $.tool.check({ tool: OWN_TOOL, input: {}, tool_use_id: 'toolu_plugin_03' })).toEqual({ decision: 'ask' })
})

test('tool calls pass through Lens unchanged', async ($, on) => {
  on('tool.call', () => ({ result: 'ran' }))
  expect(await $.tool.call({ tool: 'Bash', command: 'ls' })).toEqual({ result: 'ran' })
})

// Inline plugins are loaded from their source alone, so the tool name is spelled out.
const foreign = {
  name: 'other-plugin',
  // Loaded after Lens, so its calls pass through Lens's hooks.
  tier: 'append',
  register(on: any) {
    on('command.run', { command: 'probe-check' }, async ($: any) => {
      const verdict = await $.tool.check({
        tool: 'mcp__plugin_repowise_repowise__get_context',
        input: {},
        tool_use_id: 'toolu_plugin_09',
      })
      return { text: JSON.stringify(verdict) }
    })
  },
}

test("another plugin's call to the same tool is not approved by Lens", { plugins: [foreign] }, async ($, on) => {
  on('tool.check', () => ({ decision: 'ask' }))
  const answer = await $.command.run({ command: 'probe-check', args: '' })
  expect(JSON.parse(answer.text)).toEqual({ decision: 'ask' })
})

test('the no-index hint shows once per session', async ($, on) => {
  noIndexStubs(on)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  const shown = await waitFor(async () => (await bandText($)) === 'Index this repo for Lens: repowise init --no-prose -y')
  expect(shown).toBe(true)

  // The hint retires after the first turn ends, and a re-discovery does not bring it back.
  await $.turn.complete({ turnId: 't1', answer: 'ok', durationMs: 1, isAborted: false, usage: null })
  expect(await bandText($)).toBeUndefined()
  await $.turn.complete({ turnId: 't2', answer: 'ok', durationMs: 1, isAborted: false, usage: null })
  await new Promise((resolve) => setTimeout(resolve, 200))
  expect(await bandText($)).toBeUndefined()
})

test('a subagent turn does not retire the hint', async ($, on) => {
  noIndexStubs(on)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  expect(await waitFor(async () => (await bandText($)) !== undefined)).toBe(true)
  await $.turn.complete({ turnId: 't1', agentId: 'sub-1', answer: 'ok', durationMs: 1, isAborted: false, usage: null })
  expect(await bandText($)).toBe('Index this repo for Lens: repowise init --no-prose -y')
})
