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
  // Core resolves turn.complete to the answer's own text.
  on('turn.complete', (_$: any, e: any) => ({ text: e.answer }))
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

const OWN_TOOL = 'mcp__plugin_repowise_repowise__get_context'
const CORE = { decision: 'ask', reason: 'core' }

test("Lens leaves every permission decision to Claude Code, its own lookups included", async ($, on) => {
  on('tool.check', () => CORE)
  expect(await $.tool.check({ tool: OWN_TOOL, input: {}, tool_use_id: 'toolu_01abc' } as any)).toEqual(CORE)
  expect(await $.tool.check({ tool: OWN_TOOL, input: {}, tool_use_id: 'toolu_plugin_01' } as any)).toEqual(CORE)
  expect(await $.tool.check({ tool: 'Bash', input: { command: 'ls' }, tool_use_id: 'toolu_plugin_02' } as any)).toEqual(CORE)
})

const SPINNER = {
  plugin: 'repowise',
  component: 'Spinner',
  surface: 'terminal',
  props: { word: 'Sauteing', message: null, suffix: '…', mode: 'tool-use' },
} as const

/** An indexed repo at /work with no local server; get_context answers for src/a.py. */
function indexedStubs(on: any, calls: string[]): void {
  on('session.start', () => ({ cwd: '/work' }))
  on('session.cwd', () => ({ value: '/work' }))
  // The engine hands paths over resolved for this OS (`C:\work\...` on Windows).
  on('fs.exists', (_$: any, e: any) => ({ value: /work[\\/]\.repowise[\\/]state\.json$/.test(e.path) }))
  on('fs.read', () => ({ value: '{}' }))
  on('process.run', () => ({ value: { exitCode: 1, stdout: '', stderr: '' } }))
  on('mcp.connect', () => ({ value: { isConnected: true, server: 'plugin:repowise:repowise' } }))
  on('mcp.call', (_$: any, e: any) => {
    calls.push(`${e.server} ${e.tool} ${JSON.stringify(e.args)}`)
    const card = { callers: [{ file: 'b.py' }, { file: 'c.py' }], callers_total: 41, ownership: { contributor_count: 3 } }
    return { value: { content: [{ type: 'text', text: JSON.stringify({ result: { targets: { 'src/a.py': card } } }) }], isError: false } }
  })
  // The engine's own spinner line: the word, then whatever suffix it is handed.
  on('ui.render', (_$: any, e: any) => ({ type: 'Text', props: {}, children: [`${e.props.word}${e.props.suffix}`] }))
}

async function spinnerText($: any): Promise<string | undefined> {
  const ui = await $.ui.mount(SPINNER)
  const found = await ui.find({ type: 'Text', text: /Sauteing/ })
  await ui.unmount()
  return found?.children?.join('')
}

test("the spinner names the file and its reach while a file tool runs, and is the engine's own line otherwise", async ($, on) => {
  const calls: string[] = []
  const seen: Array<string | undefined> = []
  indexedStubs(on, calls)
  on('tool.call', async () => {
    seen.push(await spinnerText($))
    return { result: { ok: true } }
  })
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await new Promise((resolve) => setTimeout(resolve, 600))

  expect(await $.tool.call({ tool: 'Read', file_path: '/work/src/a.py' } as any)).toMatchObject({ result: { ok: true } })
  await new Promise((resolve) => setTimeout(resolve, 100))
  await $.tool.call({ tool: 'Read', file_path: '/work/src/a.py' } as any)

  // The mocked lookup lands at once, so both reads show it; it was fetched once.
  expect(calls).toEqual(['plugin:repowise:repowise get_context {"targets":["src/a.py"],"include":["callers","ownership"]}'])
  expect(seen).toEqual(['Sauteing… a.py · 41 caller files · 3 contributors', 'Sauteing… a.py · 41 caller files · 3 contributors'])
  expect(await spinnerText($)).toBe('Sauteing…')
})

// Recorded from the plugin's augment hook on a real Edit (see test/fixtures/replay).
const AUGMENT_EDIT =
  '[repowise] packages/core/src/repowise/core/analysis/dead_code/analyzer.py has a decision recorded in it, mined but not reviewed: Share one identifier shape for absence scans.\n' +
  '[repowise] packages/core/src/repowise/core/analysis/dead_code/analyzer.py has been bug-fixed 53x in the last 6 months, last today (bug magnet); mostly in _member_is_used.'

const TOOL_ROW = {
  plugin: 'repowise',
  component: 'ToolUse',
  surface: 'terminal',
  requestId: 'toolu_01edit',
  props: { tool_use_id: 'toolu_01edit', tool: 'Edit', input: {}, isRunning: false, isErrored: false, isInterrupted: false },
} as const

function rowStubs(on: any): void {
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['Update(analyzer.py)'] }))
  on('classic.PostToolUse', () => ({ additionalContext: [AUGMENT_EDIT] }))
}

async function postEdit($: any, id: string, tool = 'Edit'): Promise<unknown> {
  return $.classic.PostToolUse({ tool_name: tool, tool_use_id: id, tool_input: { file_path: '/work/a.py' }, tool_response: {} })
}

test('a margin note sits under the edit the hook flagged, and the hook result reaches Claude unchanged', async ($, on) => {
  rowStubs(on)
  expect(await postEdit($, 'toolu_01edit')).toEqual({ additionalContext: [AUGMENT_EDIT] })
  const ui = await $.ui.mount(TOOL_ROW as any)
  expect(await ui.find({ type: 'Text', text: 'Update(analyzer.py)' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /a decision found in this file, not yet reviewed: Share one identifier shape/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /fixed 53 times in 6 months, most recently today, mostly in _member_is_used/ })).toBeDefined()
  await ui.unmount()
})

test("no margin under another row, under a read, or under Lens's own lookups", async ($, on) => {
  rowStubs(on)
  await postEdit($, 'toolu_plugin_1')
  await postEdit($, 'toolu_01read', 'Read')
  for (const id of ['toolu_plugin_1', 'toolu_01read', 'toolu_01never']) {
    const ui = await $.ui.mount({ ...TOOL_ROW, requestId: id, props: { ...TOOL_ROW.props, tool_use_id: id } } as any)
    expect(await ui.find({ key: 'lens-margin' })).toBeUndefined()
    await ui.unmount()
  }
})

test('the margin toggle off draws no notes', { options: { lens_margin: false } }, async ($, on) => {
  rowStubs(on)
  await postEdit($, 'toolu_01edit')
  const ui = await $.ui.mount(TOOL_ROW as any)
  expect(await ui.find({ key: 'lens-margin' })).toBeUndefined()
  await ui.unmount()
})

// Real `repowise distill git log -n 300` output on the requests index, abridged
// to its first kept lines; the marker is verbatim.
const DISTILLED =
  '300 commits (showing 20 most recent; subjects only)\n' +
  '3bb56ba5  Oct 3 2026   scratch: two files\n' +
  '4ed3d1b3  Jun 24 2026  Bump actions/checkout from 6.0.2 to 7.0.0 in the actions group (#7540)\n' +
  '\n' +
  '[repowise#eed993b23bc3: 2928 lines omitted (~25947 tokens); restore: repowise expand eed993b23bc3]\n'

function bashResult(stdout: string) {
  return {
    plugin: 'repowise',
    component: 'ToolResult',
    surface: 'terminal',
    props: { tool_use_id: 'toolu_01bash', tool: 'Bash', output: { stdout, stderr: '', interrupted: false }, isErrored: false },
  } as any
}

test('a squeeze row sits under a distilled Bash result', async ($, on) => {
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['engine result'] }))
  const ui = await $.ui.mount(bashResult(DISTILLED))
  expect(await ui.find({ type: 'Text', text: 'engine result' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /2,931 lines → 3 · ~25,947 tokens omitted · repowise expand eed993b23bc3$/ })).toBeDefined()
  await ui.unmount()
})

test('the squeeze toggle off draws no row', { options: { lens_squeeze: false } }, async ($, on) => {
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['engine result'] }))
  const ui = await $.ui.mount(bashResult(DISTILLED))
  expect(await ui.find({ type: 'Text', text: /repowise expand/ })).toBeUndefined()
  await ui.unmount()
})

test('no squeeze row for output distill left alone', async ($, on) => {
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['engine result'] }))
  const ui = await $.ui.mount(bashResult('On branch main\nnothing to commit, working tree clean\n'))
  expect(await ui.find({ type: 'Text', text: /lines →/ })).toBeUndefined()
  await ui.unmount()
})

// A get_change_risk result recorded on an indexed copy of `requests` after a
// complex helper was added (test/fixtures/change-risk/findings.json, the
// blocks the card reads; this runtime has no fs to load the file).
const FINDINGS =
  {"directive": {"status": "review_required", "headline": "2 new findings need review, starting with nested_complexity in src/requests/_internal_utils.py.", "reasons": ["critical defect: nested_complexity in classify_headers (added_lines)", "high defect: complex_method in classify_headers (added_lines)"], "next_actions": ["Inspect src/requests/_internal_utils.py:55 (chf_7c35d63fcc13e52b)", "Inspect src/requests/_internal_utils.py:55 (chf_1d478916e70baee8)", "Run: tests/test_requests.py tests/test_utils.py"]}, "ref": "working tree", "working_tree": true, "risk_percentile": 92.0, "impacted_tests": {"status": "selected", "run_all": false, "reasons": [], "reasons_total": 0, "basis": "inferred", "map_present": false, "tests_to_run": ["tests/test_requests.py", "tests/test_utils.py"], "tests_to_run_kind": "test_file", "total": 2, "truncated": false, "always_run_total": 0, "why": {"tests/test_requests.py": "src/requests/_internal_utils.py changed (import-graph)", "tests/test_utils.py": "src/requests/_internal_utils.py changed (import-graph)"}, "basis_by_file": {"src/requests/_internal_utils.py": "import-graph"}, "summary": "2 test(s) to run for this change; the rest can be skipped."}, "health_delta": {"status": "available", "explanation": "Compared 1 changed files on both sides.", "basis": "both_sides_analyzed", "introduced": 2, "worsened": 0, "resolved": 0, "scope": {"changed": 1, "eligible": 1, "analyzed": 1, "skipped": 0, "failed": 0}, "top_findings": [{"id": "chf_7c35d63fcc13e52b", "change": "introduced", "dimension": "defect", "biomarker": "nested_complexity", "severity": "critical", "path": "src/requests/_internal_utils.py", "reason": "classify_headers nests 7 levels deep", "attribution": {"basis": "added_lines", "confidence": "high", "why": "Lines 55-85 are added or rewritten by this change."}, "inspect": "get_change_risk(finding_id='chf_7c35d63fcc13e52b')", "symbol": "classify_headers", "lines": [55, 85]}, {"id": "chf_1d478916e70baee8", "change": "introduced", "dimension": "defect", "biomarker": "complex_method", "severity": "high", "path": "src/requests/_internal_utils.py", "reason": "classify_headers has cyclomatic complexity 15", "attribution": {"basis": "added_lines", "confidence": "high", "why": "Lines 55-85 are added or rewritten by this change."}, "inspect": "get_change_risk(finding_id='chf_1d478916e70baee8')", "symbol": "classify_headers", "lines": [55, 85]}], "findings_total": 2, "findings_emitted": 2}}

/** A git work tree; get_change_risk answers with FINDINGS; records prompts and transcript lines. */
function reviewStubs(on: any, seen: { mcp: string[]; prompts: string[]; logs: string[] }): void {
  noIndexStubs(on)
  on('turn.start', () => ({ turnId: 't1' }))
  on('tool.call', () => ({ result: { type: 'create' }, text: 'File created' }))
  on('mcp.call', (_$: any, e: any) => {
    seen.mcp.push(e.tool)
    return { value: { content: [{ type: 'text', text: JSON.stringify({ result: FINDINGS }) }], isError: false } }
  })
  on('prompt.submit', (_$: any, e: any) => {
    seen.prompts.push(e.text)
    return { text: e.text }
  })
  on('ui.log', (_$: any, e: any) => {
    if (e.to === 'transcript') seen.logs.push(e.text)
  })
}

const TURN = { turnId: 't1', answer: 'Added the helper.', durationMs: 1, isAborted: false, reason: 'answer', usage: null }

test('after a turn that edited a file, the review goes beneath the answer and the band offers its buttons', async ($, on) => {
  const seen = { mcp: [] as string[], prompts: [] as string[], logs: [] as string[] }
  reviewStubs(on, seen)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await ($ as any).turn.start({ turnId: 't1', prompt: 'add a helper' })
  await $.tool.call({ tool: 'Write', file_path: '/elsewhere/x.py', content: 'x = 1\n' } as any)
  const done: any = await $.turn.complete(TURN as any)

  // Started when the edit landed and reused at the turn's end: one call.
  expect(seen.mcp).toEqual(['get_change_risk'])
  // One row: the engine stores a line break in this text as U+FFFD.
  expect(done.text).not.toContain('\n')
  expect(done.text.split(' · ').slice(0, 2)).toEqual([
    'Change review (working tree, 1 changed file)',
    'Health: 2 new findings need review, starting with nested_complexity in src/requests/_internal_utils.py',
  ])

  const ui = await $.ui.mount(BAND)
  expect((await ui.find({ type: 'Text', text: /review · health/ }))?.children?.join('')).toBe(
    'review · health: 2 new findings, review required',
  )
  // Nothing reaches Claude until a button is pressed.
  expect(seen.prompts).toEqual([])
  await ui.press({ key: 'lens-review-tests' })
  await ui.press({ key: 'lens-review-details' })
  await ui.unmount()
  expect(seen.prompts).toEqual([
    'Run the tests Repowise names for this change, inferred from the dependency graph, not measured: tests/test_requests.py tests/test_utils.py',
  ])
  expect(seen.logs.slice(0, 2)).toEqual([
    'Change review: review required',
    '2 new findings need review, starting with nested_complexity in src/requests/_internal_utils.py.',
  ])
})

test('a turn that edited nothing gets no review and no card', async ($, on) => {
  const seen = { mcp: [] as string[], prompts: [] as string[], logs: [] as string[] }
  reviewStubs(on, seen)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await ($ as any).turn.start({ turnId: 't1', prompt: 'explain' })
  await $.tool.call({ tool: 'Read', file_path: '/elsewhere/x.py' } as any)
  expect(await $.turn.complete(TURN as any)).toMatchObject({ text: 'Added the helper.' })
  expect(seen.mcp).toEqual([])
})

test('with lens_review off, an editing turn is not reviewed', { options: { lens_review: false } }, async ($, on) => {
  const seen = { mcp: [] as string[], prompts: [] as string[], logs: [] as string[] }
  reviewStubs(on, seen)
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await ($ as any).turn.start({ turnId: 't1', prompt: 'add a helper' })
  await $.tool.call({ tool: 'Write', file_path: '/elsewhere/x.py', content: 'x = 1\n' } as any)
  expect(await $.turn.complete(TURN as any)).toMatchObject({ text: 'Added the helper.' })
  expect(seen.mcp).toEqual([])
})
