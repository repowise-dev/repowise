// Runs under `claude plugin test` against the built bundle (npm run test:mod):
// the /lens pane's tabs, the Ask field through the engine's own `ui.input`
// chain, and the brief offered after a compaction. MCP is the test's stub.
import { expect, test } from 'claude-code/testing'

const PANE = {
  plugin: 'repowise',
  component: 'Pane',
  surface: 'terminal',
  requestId: 'lens',
  viewport: { columns: 160, rows: 50 },
  props: { title: 'Lens', isFocused: true, bodyColumns: 140, placement: 'dock', scroll: { offset: 0, bodyRows: 40 }, view: {} },
} as const

const BAND = {
  plugin: 'repowise',
  component: 'AbovePrompt',
  surface: 'terminal',
  viewport: { columns: 100, rows: 30 },
  props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 100, scroll: { offset: 0, bodyRows: 10 }, view: {} },
} as const

// A get_answer reply recorded on an indexed Django copy (test/fixtures/ask/django-answer-filter.json).
const ANSWER = {
  answer:
    'Synthesis is unavailable (no-llm-provider). Source rationale in django/utils/tree.py: A class for storing a tree graph. Primarily used for filter constructs in the ORM.',
  citations: ['django/utils/tree.py', 'django/db/models/sql/constants.py'],
  confidence: 'low',
  retrieval_quality: 'weak',
  degraded: 'no-llm-provider',
}

/** A git work tree with an index and no local server; the MCP server connects and answers get_answer. */
function stubs(on: any, seen: { mcp: string[]; prompts: string[]; opened: any[] }): void {
  on('session.start', () => ({ cwd: '/work' }))
  on('session.cwd', () => ({ value: '/work' }))
  on('fs.exists', (_$: any, e: any) => ({ value: /work[\\/]\.repowise[\\/]state\.json$/.test(e.path) }))
  on('fs.read', () => ({ value: '{}' }))
  on('process.run', () => ({ value: { exitCode: 0, stdout: 'true\n', stderr: '' } }))
  on('settings.read', () => ({ value: {} }))
  on('mcp.connect', () => ({ value: { isConnected: true, server: 'plugin:repowise:repowise' } }))
  on('mcp.call', (_$: any, e: any) => {
    seen.mcp.push(`${e.tool} ${JSON.stringify(e.args)}`)
    return { value: { content: [{ type: 'text', text: JSON.stringify({ result: ANSWER }) }], isError: false } }
  })
  on('ui.open', (_$: any, e: any) => (seen.opened.push(e), { value: { isPlaced: true } }))
  on('prompt.submit', (_$: any, e: any) => (seen.prompts.push(e.text), { text: e.text }))
  on('tool.call', () => ({ result: { type: 'create' } }))
  on('classic.PostCompact', () => ({}))
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['drawn by Claude Code'] }))
  on('ui.log', () => ({}))
}

async function begin($: any, on: any) {
  const seen = { mcp: [] as string[], prompts: [] as string[], opened: [] as any[] }
  stubs(on, seen)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  // The server name resolves in the background.
  await new Promise((resolve) => setTimeout(resolve, 300))
  return seen
}

test('/lens recap opens the pane with focus on the recap, and the tabs switch it', async ($: any, on: any) => {
  const seen = await begin($, on)
  await $.command.run({ command: 'lens', args: 'recap' })
  expect(seen.opened).toEqual([{ id: 'lens', title: 'Lens', rows: 29, focus: true }])
  const ui = await $.ui.mount(PANE)
  expect(await ui.find({ key: 'lens-recap' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Lens made no model calls. Every figure here is read from the local index.' })).toBeDefined()
  await ui.press({ key: 'lens-tab-flow' })
  await ui.unmount()
  const again = await $.ui.mount(PANE)
  expect(await again.find({ key: 'lens-flow' })).toBeDefined()
  await again.unmount()
  // The Ask field is off the bar: /lens ask opens it.
  await $.command.run({ command: 'lens', args: 'ask' })
  const field = await $.ui.mount(PANE)
  expect(await field.find({ key: 'lens-ask' })).toBeDefined()
  await field.unmount()
  expect(seen.mcp).toEqual([])
})

test('Enter in the Ask field asks get_answer and shows the reply with its evidence and confidence', async ($: any, on: any) => {
  const seen = await begin($, on)
  await $.command.run({ command: 'lens', args: 'ask' })
  const ui = await $.ui.mount(PANE)
  await ui.input({ key: 'lens-ask', text: 'how does filter() build SQL?' })
  expect(seen.mcp).toEqual(['get_answer {"question":"how does filter() build SQL?"}'])
  await ui.unmount()
  const after = await $.ui.mount(PANE)
  const md = await after.find({ type: 'Markdown' })
  expect(md?.props?.text ?? md?.text).toMatch(/\*\*Built from the index\*\* · confidence low · retrieval weak/)
  expect(md?.props?.text ?? md?.text).toMatch(/evidence: `django\/utils\/tree\.py`/)
  await after.unmount()
})

test('after a compaction the band offers the brief; only the press submits it', async ($: any, on: any) => {
  const seen = await begin($, on)
  await $.tool.call({ tool: 'Write', file_path: '/work/src/new.py', content: 'x = 1\n' } as any)
  await $.classic.PostCompact({ trigger: 'manual', compact_summary: 'summary' } as any)
  const ui = await $.ui.mount(BAND)
  expect(await ui.find({ type: 'Text', text: 'context compacted' })).toBeDefined()
  expect(seen.prompts).toEqual([])
  await ui.press({ key: 'lens-brief' })
  await ui.unmount()
  expect(seen.prompts).toHaveLength(1)
  expect(seen.prompts[0]).toMatch(/^The context was compacted\. This brief is built from the Repowise index and this session's edits:/)
  expect(seen.prompts[0]).toMatch(/Files edited: src\/new\.py$/)
})
