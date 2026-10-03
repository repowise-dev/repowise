import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CheckNext, Hook, McpToolResult, ModApi, On, ToolCheckEvent } from "../src/mod-api";
import { fixture } from "./fake-host";

type Hooks = Record<string, Hook<any>>;

async function load(options: Record<string, boolean> = {}): Promise<Hooks> {
  vi.resetModules();
  const { register } = await import("../src/register");
  const hooks: Hooks = {};
  // Render hooks are keyed by component: `ui.render:AbovePrompt`.
  const on = ((event: string, a: unknown, b?: unknown) => {
    const key = b === undefined ? event : `${event}:${(a as { component: string }).component}`;
    hooks[key] = (b ?? a) as Hook<any>;
  }) as On;
  register(on, options);
  return hooks;
}

interface DollarOptions {
  indexed?: boolean;
  mcp?: () => Promise<McpToolResult>;
  /** A live local server listing this repo, answering the savings route with each payload in turn. */
  savings?: unknown[];
  /** The repo id that server lists; default `r1`. */
  repoId?: string;
}

type Calls = { cwd: number; invalidate: number; logs: string[]; mcp: unknown[][]; savings: number };

const LOCK = "/work/app/.repowise/serve.lock.json";
const STATE = "/work/app/.repowise/state.json";

function fsFor(o: DollarOptions): ModApi["fs"] {
  const served = o.savings !== undefined;
  return {
    read: async (path) => (served && path === LOCK ? fixture("locks/valid.json") : Promise.reject(new Error("ENOENT"))),
    exists: async (path) => (o.indexed === true || served) && path === STATE,
  };
}

/** `ps` says the lock's pid is alive; git says this is a work tree. */
const processFake: ModApi["process"] = {
  run: async (argv) => {
    if (argv[0] === "ps") return { exitCode: 0, stdout: "4242\n", stderr: "" };
    return { exitCode: 0, stdout: argv.includes("--is-inside-work-tree") ? "true\n" : "", stderr: "" };
  },
};

const reply = (body: unknown) => ({ status: 200, ok: true, headers: {}, text: JSON.stringify(body) });
const status = (code: number) => ({ status: code, ok: false, headers: {}, text: "" });

function httpFor(o: DollarOptions, calls: Calls): ModApi["http"] {
  return {
    fetch: async (url) => {
      const id = o.repoId ?? "r1";
      if (o.savings === undefined) return status(500);
      if (url.endsWith("/health")) return reply({ status: "ok" });
      if (url.endsWith("/api/repos")) return reply([{ id, name: "app", local_path: "/work/app" }]);
      if (!url.includes(`/api/repos/${id}/savings`)) return status(404);
      calls.savings++;
      const next = o.savings.shift();
      return next === undefined ? status(503) : reply(next);
    },
  };
}

function mcpFor(o: DollarOptions, calls: Calls): ModApi["mcp"] {
  return {
    connect: async () => ({ isConnected: true, server: "plugin:repowise:repowise" }),
    call: async (...args) => {
      calls.mcp.push(args);
      return o.mcp ? o.mcp() : Promise.reject(new Error("no MCP in this test"));
    },
  };
}

function uiFor(calls: Calls): ModApi["ui"] {
  return {
    invalidate: () => {
      calls.invalidate++;
    },
    log: (text) => {
      calls.logs.push(text);
    },
    resolve: () => ({
      Box: (props) => ({ el: "Box", props }),
      Text: (props) => ({ el: "Text", props }),
    }),
  };
}

/** A `$` for a git work tree: no index by default, an index with `indexed`, a live server with `savings`. */
function fakeDollar(o: DollarOptions = {}) {
  const calls: Calls = { cwd: 0, invalidate: 0, logs: [], mcp: [], savings: 0 };
  let release: () => void = () => {};
  const gate = { hold: false, wait: Promise.resolve() };
  const session: ModApi["session"] = {
    cwd: async () => {
      calls.cwd++;
      if (gate.hold) await gate.wait;
      return "/work/app";
    },
  };
  const $: ModApi = { session, fs: fsFor(o), process: processFake, http: httpFor(o, calls), mcp: mcpFor(o, calls), ui: uiFor(calls) };
  const hold = () => {
    gate.hold = true;
    gate.wait = new Promise<void>((resolve) => (release = resolve));
  };
  return { $, calls, hold, release: () => release() };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
const band = { props: { hasSurvey: false, bodyColumns: 100 } };
const THEIRS = { el: "theirs" };

let hooks: Hooks;
beforeEach(async () => {
  hooks = await load();
});

describe("register", () => {
  it("registers its hooks and nothing else", () => {
    expect(Object.keys(hooks).sort()).toEqual([
      "classic.PostToolUse",
      "session.start",
      "tool.call",
      "tool.check",
      "turn.complete",
      "ui.render:AbovePrompt",
      "ui.render:Spinner",
      "ui.render:ToolResult",
      "ui.render:ToolUse",
    ].sort());
  });

  it("leaves the margin and squeeze hooks out when their toggles are off", async () => {
    const off = await load({ lens_margin: false, lens_squeeze: false });
    expect(Object.keys(off)).not.toContain("classic.PostToolUse");
    expect(Object.keys(off)).not.toContain("ui.render:ToolUse");
    expect(Object.keys(off)).not.toContain("ui.render:ToolResult");
    expect(Object.keys(off)).toContain("ui.render:Spinner");
  });

  it("every hook returns what next(e) returned", async () => {
    const { $ } = fakeDollar();
    const marker = { from: "next" };
    expect(await hooks["session.start"]!($, {}, async () => marker)).toBe(marker);
    expect(await hooks["turn.complete"]!($, {}, async () => marker)).toBe(marker);
    expect(await hooks["turn.complete"]!($, { agentId: "a" }, async () => marker)).toBe(marker);
  });

  it("the band passes the site on at rest", async () => {
    const { $ } = fakeDollar();
    expect(await hooks["ui.render:AbovePrompt"]!($, band, async () => THEIRS)).toBe(THEIRS);
  });

  it("draws its rows above whatever the next hook drew, and alone when nothing was", async () => {
    const { $, calls } = fakeDollar();
    await hooks["session.start"]!($, {}, async () => undefined);
    await settle();
    expect(calls.invalidate).toBe(1);
    const composed = (await hooks["ui.render:AbovePrompt"]!($, band, async () => THEIRS)) as { props: { children: unknown[] } };
    expect(composed.props.children[1]).toBe(THEIRS);
    expect(JSON.stringify(composed.props.children[0])).toContain("index this repo for Lens");
    const alone = await hooks["ui.render:AbovePrompt"]!($, band, async () => null);
    expect(JSON.stringify(alone)).toContain("index this repo for Lens");
  });

  it("a failing draw keeps the band as it was and says why in the debug log only", async () => {
    const { $, calls } = fakeDollar();
    await hooks["session.start"]!($, {}, async () => undefined);
    await settle();
    $.ui.resolve = () => {
      throw new Error("no table");
    };
    expect(await hooks["ui.render:AbovePrompt"]!($, band, async () => THEIRS)).toBe(THEIRS);
    expect(calls.logs).toEqual(["lens: band render failed: Error: no table"]);
  });

  it("a failing refresh is logged, not thrown", async () => {
    const { $, calls } = fakeDollar();
    $.session.cwd = async () => {
      throw new Error("no cwd");
    };
    await hooks["session.start"]!($, {}, async () => undefined);
    await settle();
    expect(calls.logs).toEqual(["lens: refresh failed: Error: no cwd"]);
    expect(calls.invalidate).toBe(0);
  });

  it("yields to a survey", async () => {
    const { $ } = fakeDollar();
    await hooks["session.start"]!($, {}, async () => undefined);
    await settle();
    expect(await hooks["ui.render:AbovePrompt"]!($, { props: { hasSurvey: true } }, async () => THEIRS)).toBe(THEIRS);
  });

  it("discovers on the first turn when the module loaded after the session started", async () => {
    const { $, calls } = fakeDollar();
    await hooks["turn.complete"]!($, {}, async () => undefined);
    await settle();
    expect(calls.cwd).toBe(1);
  });

  it("runs once more after a refresh asked for while one was running", async () => {
    const { $, calls, hold, release } = fakeDollar();
    hold();
    await hooks["session.start"]!($, {}, async () => undefined);
    await hooks["turn.complete"]!($, {}, async () => undefined);
    await hooks["turn.complete"]!($, {}, async () => undefined);
    expect(calls.cwd).toBe(1);
    release();
    await settle();
    await settle();
    expect(calls.cwd).toBe(2);
  });

  it("drops a refresh that finishes after a new session started", async () => {
    const old = fakeDollar();
    old.hold();
    await hooks["session.start"]!(old.$, {}, async () => undefined);
    const fresh = fakeDollar();
    await hooks["session.start"]!(fresh.$, {}, async () => undefined);
    old.release();
    await settle();
    await settle();
    // The old run lands nothing; the new session's own refresh then draws.
    expect(old.calls.invalidate).toBe(0);
    expect(fresh.calls.invalidate).toBe(1);
  });
});

const SESSIONS = JSON.parse(fixture("mcp/get_context-sessions.json")) as McpToolResult;
const READ = { tool: "Read", tool_use_id: "toolu_01", file_path: "/work/app/src/requests/sessions.py" };
const SPINNER = { props: { word: "Sauteing", message: null, suffix: "…", mode: "tool-use" } };

function checkNext(plugin: string, verdict: unknown): CheckNext {
  return Object.assign(async (_e: ToolCheckEvent) => verdict, { origin: { plugin, tier: "user" } });
}

describe("tool.check", () => {
  const own = { tool: "mcp__plugin_repowise_repowise__get_context", input: {}, tool_use_id: "toolu_plugin_1" };
  const core = { decision: "ask", reason: "core" };

  it("approves Lens's own lookup", async () => {
    const { $ } = fakeDollar();
    expect(await hooks["tool.check"]!($, own, checkNext("repowise", core))).toMatchObject({ decision: "allow" });
  });

  it("returns the engine's verdict for Claude's call, another plugin's call, and any other tool", async () => {
    const { $ } = fakeDollar();
    const asks = [
      [{ ...own, tool_use_id: "toolu_01abc" }, "engine"],
      [own, "someone-else"],
      [{ ...own, tool: "mcp__plugin_repowise_repowise__get_dead_code" }, "repowise"],
      [{ ...own, tool: "Bash", input: { command: "rm -rf /" } }, "repowise"],
    ] as const;
    for (const [e, plugin] of asks) {
      expect(await hooks["tool.check"]!($, e, checkNext(plugin, core))).toBe(core);
    }
  });
});

describe("tool.call and the spinner", () => {
  async function indexedSession(mcp?: () => Promise<McpToolResult>) {
    const d = fakeDollar({ indexed: true, ...(mcp ? { mcp } : {}) });
    await hooks["session.start"]!(d.$, {}, async () => undefined);
    await settle();
    await settle();
    return d;
  }

  it("returns what next(e) returned, for every tool", async () => {
    const { $ } = await indexedSession(async () => SESSIONS);
    const result = { from: "the tool" };
    expect(await hooks["tool.call"]!($, READ, async () => result)).toBe(result);
    expect(await hooks["tool.call"]!($, { tool: "Bash", tool_use_id: "t2", command: "ls" }, async () => result)).toBe(result);
  });

  it("starts the context fetch during the call without waiting for it", async () => {
    let land: (r: McpToolResult) => void = () => undefined;
    const { $, calls } = await indexedSession(() => new Promise((resolve) => (land = resolve)));
    let ranTool = false;
    await hooks["tool.call"]!($, READ, async () => {
      // The call has started (it is in flight) and the tool runs before it lands.
      await settle();
      expect(calls.mcp).toHaveLength(1);
      ranTool = true;
      return {};
    });
    expect(ranTool).toBe(true);
    expect(calls.mcp[0]).toEqual([
      "plugin:repowise:repowise",
      "get_context",
      { targets: ["src/requests/sessions.py"], include: ["callers", "ownership"] },
    ]);
    land(SESSIONS);
  });

  it("shows the suffix from the next render after the context lands, keeping the engine's own line", async () => {
    const { $ } = await indexedSession(async () => SESSIONS);
    const seen: unknown[] = [];
    const engine = async (e: typeof SPINNER) => {
      seen.push(e);
      return { drawn: e.props.suffix };
    };
    // First read: nothing cached yet, so the spinner is the engine's.
    await hooks["tool.call"]!($, READ, async () => {
      expect(await hooks["ui.render:Spinner"]!($, SPINNER, engine)).toEqual({ drawn: "…" });
      return {};
    });
    await settle();
    // Second read of the same file: the context is in, and it is not fetched again.
    await hooks["tool.call"]!($, { ...READ, tool_use_id: "toolu_02" }, async () => {
      const drawn = await hooks["ui.render:Spinner"]!($, SPINNER, engine);
      expect(drawn).toEqual({ drawn: "… sessions.py · 3 caller files · 6 contributors" });
      return {};
    });
    expect(seen[1]).toEqual({ props: { ...SPINNER.props, suffix: "… sessions.py · 3 caller files · 6 contributors" } });
    // After the tool ends the spinner is the engine's again.
    expect(await hooks["ui.render:Spinner"]!($, SPINNER, engine)).toEqual({ drawn: "…" });
  });

  it("asks again after a failed fetch, and logs the failure to debug only", async () => {
    let n = 0;
    const { $, calls } = await indexedSession(async () => {
      if (n++ === 0) throw new Error("refused");
      return SESSIONS;
    });
    await hooks["tool.call"]!($, READ, async () => ({}));
    await settle();
    expect(calls.logs.some((l) => l.includes("context for src/requests/sessions.py failed: Error: refused"))).toBe(true);
    await hooks["tool.call"]!($, READ, async () => ({}));
    expect(calls.mcp).toHaveLength(2);
  });

  it("fetches nothing without an index", async () => {
    const { $, calls } = fakeDollar();
    await hooks["session.start"]!($, {}, async () => undefined);
    await settle();
    await hooks["tool.call"]!($, { ...READ, file_path: "/work/app/a.py" }, async () => ({}));
    expect(calls.mcp).toEqual([]);
    expect(await hooks["ui.render:Spinner"]!($, SPINNER, async () => "engine")).toBe("engine");
  });
});

const DISTILLED = fixture("distill/git-log.txt");
const REPLAY = JSON.parse(fixture("replay/repowise-edit-session.json")) as Array<{
  tool_name: string;
  tool_use_id: string;
  tool_input: unknown;
  result: unknown;
}>;
const FLAGGED_EDIT = REPLAY.find((e) => e.tool_use_id === "toolu_01P77H3c1LrnnZBi8MTE2LvR")!;

describe("squeeze rows", () => {
  const bash = (output: unknown, id = "toolu_b1") => ({ props: { tool_use_id: id, tool: "Bash", output, isErrored: false } });

  it("draw under a distilled Bash result, keeping the engine's drawing above", async () => {
    const { $ } = fakeDollar();
    const drawn = (await hooks["ui.render:ToolResult"]!($, bash({ stdout: DISTILLED, stderr: "" }), async () => THEIRS)) as {
      props: { children: Array<{ props: { children: unknown } }> };
    };
    expect(drawn.props.children[0]).toBe(THEIRS);
    expect(JSON.stringify(drawn.props.children[1])).toContain("2,949 lines → 21 · ~25,947 tokens omitted · repowise expand eed993b23bc3");
  });

  it("pass the site on for output distill left alone, another tool, or a result with no text", async () => {
    const { $ } = fakeDollar();
    const plain = fixture("distill/git-status-undistilled.txt");
    expect(await hooks["ui.render:ToolResult"]!($, bash({ stdout: plain }), async () => THEIRS)).toBe(THEIRS);
    const read = { props: { tool_use_id: "r", tool: "Read", output: DISTILLED, isErrored: false } };
    expect(await hooks["ui.render:ToolResult"]!($, read, async () => THEIRS)).toBe(THEIRS);
    expect(await hooks["ui.render:ToolResult"]!($, bash({ exitCode: 1 }, "t3"), async () => THEIRS)).toBe(THEIRS);
  });

  it("a failing draw keeps the engine's result and logs to debug", async () => {
    const { $, calls } = fakeDollar();
    $.ui.resolve = () => {
      throw new Error("no table");
    };
    expect(await hooks["ui.render:ToolResult"]!($, bash({ stdout: DISTILLED }), async () => THEIRS)).toBe(THEIRS);
    expect(calls.logs).toEqual(["lens: squeeze render failed: Error: no table"]);
  });
});

describe("margin notes", () => {
  const row = (id: string) => ({ props: { tool_use_id: id, tool: "Edit", input: {}, isRunning: false, isErrored: false } });

  it("returns the settings hooks' result untouched and draws the notes under that edit's row only", async () => {
    const { $, calls } = fakeDollar();
    const out = await hooks["classic.PostToolUse"]!($, FLAGGED_EDIT, async () => FLAGGED_EDIT.result);
    expect(out).toBe(FLAGGED_EDIT.result);
    expect(calls.invalidate).toBe(1);
    const drawn = JSON.stringify(await hooks["ui.render:ToolUse"]!($, row(FLAGGED_EDIT.tool_use_id), async () => THEIRS));
    expect(drawn).toContain('"el":"theirs"');
    expect(drawn).toContain("a decision found in this file, not yet reviewed: Configure languages declaratively");
    expect(drawn).toContain("fixed 42 times in 6 months, most recently today, mostly in parse_file");
    expect(await hooks["ui.render:ToolUse"]!($, row("toolu_other"), async () => THEIRS)).toBe(THEIRS);
  });

  it("ignores Lens's own lookups, reads, and an edit the hook said nothing about", async () => {
    const { $, calls } = fakeDollar();
    for (const e of REPLAY.filter((x) => x.tool_name !== "Edit" || x.tool_use_id !== FLAGGED_EDIT.tool_use_id).slice(0, 5)) {
      await hooks["classic.PostToolUse"]!($, e, async () => e.result);
    }
    await hooks["classic.PostToolUse"]!($, { ...FLAGGED_EDIT, tool_use_id: "toolu_plugin_x" }, async () => FLAGGED_EDIT.result);
    await hooks["classic.PostToolUse"]!($, { ...FLAGGED_EDIT, tool_name: "Read" }, async () => FLAGGED_EDIT.result);
    expect(calls.invalidate).toBe(0);
  });

  it("a failing draw keeps the row, and a malformed result is passed on", async () => {
    const { $, calls } = fakeDollar();
    await hooks["classic.PostToolUse"]!($, FLAGGED_EDIT, async () => FLAGGED_EDIT.result);
    $.ui.resolve = () => {
      throw new Error("no table");
    };
    expect(await hooks["ui.render:ToolUse"]!($, row(FLAGGED_EDIT.tool_use_id), async () => THEIRS)).toBe(THEIRS);
    expect(calls.logs).toEqual(["lens: margin render failed: Error: no table"]);
    expect(await hooks["classic.PostToolUse"]!($, { ...FLAGGED_EDIT, tool_use_id: 7 }, async () => null)).toBeNull();
    expect(calls.logs[1]).toContain("lens: PostToolUse failed");
  });
});

describe("savings", () => {
  const before = JSON.parse(fixture("savings/before-distill.json")) as unknown;
  const after = JSON.parse(fixture("savings/after-distill.json")) as unknown;

  it("snapshots at session start, shows the growth on a later turn, at most once a minute", async () => {
    const now = vi.spyOn(Date, "now");
    try {
      now.mockReturnValue(1_000_000);
      const { $, calls } = fakeDollar({ savings: [before, after, after] });
      await hooks["session.start"]!($, {}, async () => undefined);
      for (let i = 0; i < 6; i++) await settle();
      expect(calls.savings).toBe(1);
      expect(await hooks["ui.render:AbovePrompt"]!($, band, async () => null)).toBeNull();

      // Inside the cool-down a turn reads nothing.
      now.mockReturnValue(1_030_000);
      await hooks["turn.complete"]!($, {}, async () => undefined);
      for (let i = 0; i < 6; i++) await settle();
      expect(calls.savings).toBe(1);

      now.mockReturnValue(1_061_000);
      await hooks["turn.complete"]!($, {}, async () => undefined);
      for (let i = 0; i < 6; i++) await settle();
      expect(calls.savings).toBe(2);
      expect(JSON.stringify(await hooks["ui.render:AbovePrompt"]!($, band, async () => null))).toContain(
        "7,099 tokens · $0.04 saved since this session started · all agents on this repo",
      );
    } finally {
      now.mockRestore();
    }
  });

  it("reads nothing outside full mode", async () => {
    const { $, calls } = fakeDollar({ indexed: true });
    await hooks["session.start"]!($, {}, async () => undefined);
    for (let i = 0; i < 4; i++) await settle();
    expect(calls.savings).toBe(0);
  });

  it("a failed read is logged to debug and shows nothing", async () => {
    const { $, calls } = fakeDollar({ savings: [] });
    await hooks["session.start"]!($, {}, async () => undefined);
    for (let i = 0; i < 6; i++) await settle();
    expect(calls.logs.some((l) => l.startsWith("lens: savings failed"))).toBe(true);
    expect(await hooks["ui.render:AbovePrompt"]!($, band, async () => null)).toBeNull();
  });
});

describe("follow-ups", () => {
  const after = JSON.parse(fixture("savings/after-distill.json")) as Record<string, unknown>;

  async function turnAt(d: ReturnType<typeof fakeDollar>, now: ReturnType<typeof vi.spyOn>, ms: number) {
    now.mockReturnValue(ms);
    await hooks["turn.complete"]!(d.$, {}, async () => undefined);
    for (let i = 0; i < 6; i++) await settle();
  }

  it("a ledger that shrank clears the row and counts again from there", async () => {
    const now = vi.spyOn(Date, "now");
    try {
      now.mockReturnValue(1_000_000);
      const shrunk = { ...after, saved_input_tokens: 10, priced_input_savings_usd: 0 };
      const grown = { ...shrunk, saved_input_tokens: 510, priced_input_savings_usd: 0.01 };
      const bigger = { ...after, saved_input_tokens: 8099, priced_input_savings_usd: 0.045495 };
      const d = fakeDollar({ savings: [after, bigger, shrunk, grown] });
      await hooks["session.start"]!(d.$, {}, async () => undefined);
      for (let i = 0; i < 6; i++) await settle();
      await turnAt(d, now, 1_061_000);
      expect(JSON.stringify(await hooks["ui.render:AbovePrompt"]!(d.$, band, async () => null))).toContain("1,000 tokens · $0.01 saved");
      await turnAt(d, now, 1_122_000);
      expect(await hooks["ui.render:AbovePrompt"]!(d.$, band, async () => null)).toBeNull();
      await turnAt(d, now, 1_183_000);
      expect(JSON.stringify(await hooks["ui.render:AbovePrompt"]!(d.$, band, async () => null))).toContain(
        "500 tokens · $0.01 saved",
      );
    } finally {
      now.mockRestore();
    }
  });

  it("a different repo starts its own baseline", async () => {
    const now = vi.spyOn(Date, "now");
    try {
      now.mockReturnValue(1_000_000);
      const bigger = { ...after, saved_input_tokens: 9099 };
      const opts = { savings: [after, bigger, { ...bigger, saved_input_tokens: 9599 }] as unknown[], repoId: "r1" };
      const d = fakeDollar(opts);
      await hooks["session.start"]!(d.$, {}, async () => undefined);
      for (let i = 0; i < 6; i++) await settle();
      // The server now lists this checkout under another id: its first read is a baseline, not a delta.
      opts.repoId = "r2";
      await turnAt(d, now, 1_061_000);
      expect(await hooks["ui.render:AbovePrompt"]!(d.$, band, async () => null)).toBeNull();
      await turnAt(d, now, 1_122_000);
      expect(JSON.stringify(await hooks["ui.render:AbovePrompt"]!(d.$, band, async () => null))).toContain("500 tokens");
    } finally {
      now.mockRestore();
    }
  });

  it("a squeeze is looked for again when an early render had no marker yet", async () => {
    const { $ } = fakeDollar();
    const row = (stdout: string) => ({ props: { tool_use_id: "toolu_b9", tool: "Bash", output: { stdout }, isErrored: false } });
    expect(await hooks["ui.render:ToolResult"]!($, row("300 commits (show"), async () => THEIRS)).toBe(THEIRS);
    expect(JSON.stringify(await hooks["ui.render:ToolResult"]!($, row(DISTILLED), async () => THEIRS))).toContain("2,949 lines");
  });

  it("the spinner suffix stands alone when the engine's is empty or missing", async () => {
    const d = fakeDollar({ indexed: true, mcp: async () => JSON.parse(fixture("mcp/get_context-sessions.json")) as McpToolResult });
    await hooks["session.start"]!(d.$, {}, async () => undefined);
    for (let i = 0; i < 4; i++) await settle();
    const read = { tool: "Read", tool_use_id: "toolu_s1", file_path: "/work/app/src/requests/sessions.py" };
    await hooks["tool.call"]!(d.$, read, async () => ({}));
    await settle();
    await hooks["tool.call"]!(d.$, { ...read, tool_use_id: "toolu_s2" }, async () => {
      for (const suffix of ["", undefined]) {
        const drawn = await hooks["ui.render:Spinner"]!(d.$, { props: { word: "W", message: null, suffix, mode: "tool-use" } }, async (e) => e);
        expect((drawn as { props: { suffix: string } }).props.suffix).toBe(" sessions.py · 3 caller files · 6 contributors");
      }
      return {};
    });
  });
});
