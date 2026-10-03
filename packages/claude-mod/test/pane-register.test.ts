// The pane's wiring through register(): /lens routing and tabs, the Ask field
// (its lookup started inside Lens's own `ui.input` hook), the review's Why
// button, and the brief offered after a compaction. MCP answers come from the
// replies recorded in test/fixtures/ask.
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Hook, McpToolResult, ModApi, On } from "../src/mod-api";
import { ASK_KEY } from "../src/views/pane";
import { fixture, mcpResult } from "./fake-host";

type Hooks = Record<string, Hook<any>>;

async function load(options: Record<string, boolean> = {}): Promise<{ hooks: Hooks; events: string[] }> {
  vi.resetModules();
  const { register } = await import("../src/register");
  const hooks: Hooks = {};
  const events: string[] = [];
  const on = ((event: string, a: unknown, b?: unknown) => {
    events.push(event);
    const m = a as { component?: string; command?: string; element?: string };
    hooks[b === undefined ? event : `${event}:${m.component ?? m.command ?? m.element}`] = (b ?? a) as Hook<any>;
  }) as On;
  register(on, options);
  return { hooks, events };
}

type Tree = { el: string; props: Record<string, any> };
function flatten(t: unknown): Tree[] {
  const n = t as Tree;
  if (typeof n !== "object" || n === null || n.props === undefined) return [];
  const kids = Array.isArray(n.props.children) ? (n.props.children as unknown[]) : [];
  return [n, ...kids.flatMap(flatten)];
}
const textOf = (t: unknown) =>
  flatten(t)
    .filter((n) => n.el === "Text" || n.el === "Markdown")
    .map((n) => (n.el === "Markdown" ? (n.props.text as string) : (n.props.children as string[]).join("")));
const buttonsOf = (t: unknown) => flatten(t).filter((n) => n.el === "Button");

function fakeDollar(mcp: (tool: string, args: Record<string, unknown>) => Promise<McpToolResult>) {
  const calls = { mcp: [] as Array<[string, Record<string, unknown>]>, open: [] as unknown[], submitted: [] as string[], logs: [] as string[] };
  const $: ModApi = {
    session: { cwd: async () => "/work/app" },
    fs: { read: async () => Promise.reject(new Error("ENOENT")), exists: async (p) => p === "/work/app/.repowise/state.json" },
    process: { run: async (argv) => ({ exitCode: 0, stdout: argv.includes("--is-inside-work-tree") ? "true\n" : "", stderr: "" }) },
    http: { fetch: async () => ({ status: 500, ok: false, headers: {}, text: "" }) },
    mcp: {
      connect: async () => ({ isConnected: true, server: "plugin:repowise:repowise" }),
      call: (_server, tool, args = {}) => {
        calls.mcp.push([tool, args]);
        return mcp(tool, args);
      },
    },
    prompt: {
      submit: async ({ text }) => {
        calls.submitted.push(text);
        return {};
      },
    },
    ui: {
      invalidate: () => undefined,
      log: (text) => {
        calls.logs.push(text);
      },
      resolve: () =>
        Object.fromEntries(["Box", "Text", "Button", "Input", "Markdown", "Raster"].map((el) => [el, (props: Record<string, unknown>) => ({ el, props })])),
      open: async (pane) => {
        calls.open.push(pane);
        return { isPlaced: true };
      },
      close: async () => undefined,
      blit: async () => ({}),
    },
    command: { register: async () => ({}) },
    settings: { read: async () => ({}) },
  };
  return { $, calls };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
const reply = (name: string) => mcpResult(JSON.parse(fixture(`ask/${name}.json`)));
const PANE = { surface: "terminal", requestId: "lens", props: { bodyColumns: 120, placement: "dock", scroll: { bodyRows: 40 } } };
const BAND = { props: { hasSurvey: false, bodyColumns: 120 } };
const answers = async (tool: string) => {
  if (tool === "get_why") return reply("requests-why-archaeology");
  if (tool === "get_answer") return reply("django-answer-filter");
  throw new Error(`no ${tool} here`);
};

let hooks: Hooks;
let events: string[];
beforeEach(async () => {
  ({ hooks, events } = await load());
});

/** A session in an indexed repo whose MCP server name has resolved. */
async function session(mcp = answers) {
  const d = fakeDollar(mcp);
  await hooks["session.start"]!(d.$, { cwd: "/work/app" }, async () => undefined);
  for (let i = 0; i < 6; i++) await settle();
  return d;
}

const pane = async (d: { $: ModApi }) => hooks["ui.render:Pane"]!(d.$, PANE, async () => null);
const band = async (d: { $: ModApi }) => hooks["ui.render:AbovePrompt"]!(d.$, BAND, async () => null);
const lens = (d: { $: ModApi }, args: string) => hooks["command.run:lens"]!(d.$, { command: "lens", args }, async () => ({}));

describe("/lens routing and tabs", () => {
  it("/lens recap opens the pane with the keyboard on the recap; a bare /lens keeps the tab", async () => {
    const d = await session();
    expect(await lens(d, "recap")).toEqual({});
    expect(d.calls.open).toEqual([{ id: "lens", title: "Lens", rows: 29, focus: true }]);
    const shown = textOf(await pane(d));
    expect(shown).toContain("Files touched: ");
    expect(shown.at(-1)).toBe("0 model calls · nothing uploaded");
    await lens(d, "");
    expect(textOf(await pane(d))).toContain("Files touched: ");
    expect(d.calls.mcp).toEqual([]);
  });

  it("a tab press switches the body: Map, then Ask with its field", async () => {
    const d = await session();
    await lens(d, "recap");
    const tabs = buttonsOf(await pane(d));
    expect(tabs.map((b) => [b.props.hotkey, b.props.label])).toEqual([["1", "Map"], ["2", "Ask"], ["3", "Recap"]]);
    tabs[1]!.props.onPress();
    const ask = flatten(await pane(d)).find((n) => n.el === "Input");
    expect(ask?.props).toMatchObject({ key: "lens-ask", autoFocus: true });
    tabs[0]!.props.onPress();
    expect(textOf(await pane(d))).toEqual(["Lens map needs the local server: repowise serve --no-ui"]);
  });

  it("/lens ask starts the lookup inside the command's own hook and shows the reply with its evidence", async () => {
    const d = await session();
    await lens(d, "ask why does Session merge environment settings?");
    // Started before the hook returned: approved as Lens's own call.
    expect(d.calls.mcp).toEqual([["get_why", { query: "why does Session merge environment settings?" }]]);
    await settle();
    const md = textOf(await pane(d)).at(-1)!;
    expect(md).toContain("**get_why** · basis: archaeology");
    expect(md).toContain("`ev_8964f795d9001bd297ca`");
  });
});

describe("the Ask field", () => {
  const submit = (d: { $: ModApi }, value: string, kind = "submit") =>
    hooks["ui.input:lens-ask"]!(d.$, { plugin: "repowise", element: "lens-ask", kind, value }, async (e) => ({ element: e.element, value: e.value }));

  it("Enter starts the lookup in the ui.input hook; the field's own closure does nothing", async () => {
    const d = await session();
    await lens(d, "ask");
    const input = flatten(await pane(d)).find((n) => n.el === "Input")!;
    input.props.onSubmit("how does filter() build SQL?");
    expect(d.calls.mcp).toEqual([]);
    expect(await submit(d, "how does filter() build SQL?")).toEqual({ element: "lens-ask", value: "how does filter() build SQL?" });
    expect(d.calls.mcp).toEqual([["get_answer", { question: "how does filter() build SQL?" }]]);
    await settle();
    const md = textOf(await pane(d)).at(-1)!;
    expect(md).toContain("**get_answer** · confidence: low · retrieval: weak · not synthesized: no-llm-provider");
    expect(md).toContain("cited: `django/utils/tree.py`, `django/db/models/sql/constants.py`");
  });

  it("typing and empty submits ask nothing; one question at a time", async () => {
    let land: (r: McpToolResult) => void = () => undefined;
    const d = await session(() => new Promise((resolve) => (land = resolve)));
    await submit(d, "why", "change");
    await submit(d, "   ");
    expect(d.calls.mcp).toEqual([]);
    await submit(d, "why A");
    await submit(d, "why B");
    expect(d.calls.mcp).toHaveLength(1);
    await lens(d, "ask");
    expect(textOf(await pane(d))).toEqual(["> why A", "asking get_why..."]);
    land(reply("django-why-no-record"));
  });

  it("a failed lookup says so in the tab, one line", async () => {
    const d = await session(async () => {
      throw new Error("refused\nsecond line");
    });
    await lens(d, "ask how?");
    await settle();
    expect(textOf(await pane(d)).at(-1)).toBe("get_answer could not answer: refused");
    expect(d.calls.logs).toContain("lens: ask failed: Error: refused\nsecond line");
  });

  it("/lens ask before the server connected waits for it inside the hook, then asks", async () => {
    const d = fakeDollar(answers);
    const waiting: Array<(v: { isConnected: boolean; server: string }) => void> = [];
    d.$.mcp.connect = () => new Promise((resolve) => waiting.push(resolve));
    const running = lens(d, "ask how?");
    await settle();
    expect(d.calls.mcp).toEqual([]);
    for (const connected of waiting) connected({ isConnected: true, server: "plugin:repowise:repowise" });
    await running;
    // Started before the hook returned.
    expect(d.calls.mcp).toEqual([["get_answer", { question: "how?" }]]);
  });

  it("a server that never connects: the ask fails at once and starts nothing late", async () => {
    const d = fakeDollar(answers);
    d.$.mcp.connect = async () => ({ isConnected: false });
    await lens(d, "ask how?");
    await submit(d, "how?");
    await settle();
    expect(d.calls.mcp).toEqual([]);
    expect(textOf(await pane(d)).at(-1)).toBe("get_answer could not answer: repowise MCP server name not resolved yet");
  });

  it("the field's address is the one the hook's literal matcher names", () => {
    expect(ASK_KEY).toBe("lens-ask");
  });
});

describe("Why on the review", () => {
  const EDIT = { tool: "Edit", tool_use_id: "toolu_e1", file_path: "/work/app/src/a.py" };
  const NOTICE = "[repowise] src/a.py is governed by a standing decision: Keep the cache bounded because memory is finite.";

  async function reviewed(notice: string | null) {
    const d = await session(async (tool) => (tool === "get_change_risk" ? mcpResult(JSON.parse(fixture("change-risk/clear.json"))) : answers(tool)));
    await hooks["turn.start"]!(d.$, {}, async () => undefined);
    await hooks["tool.call"]!(d.$, EDIT, async () => ({ result: { type: "update" } }));
    const post = { tool_name: "Edit", tool_use_id: "toolu_e1", tool_input: {} };
    await hooks["classic.PostToolUse"]!(d.$, post, async () => ({ additionalContext: notice === null ? [] : [notice] }));
    await hooks["turn.complete"]!(d.$, { answer: "Done.", reason: "answer" }, async () => ({ text: "Done." }));
    return d;
  }

  it("shows 2: Why when a decision governs the edit; the press opens Ask with the decision, asking nothing", async () => {
    const d = await reviewed(NOTICE);
    const why = buttonsOf(await band(d)).find((b) => b.props.label === "Why")!;
    expect(why.props.hotkey).toBe("2");
    const asked = d.calls.mcp.length;
    why.props.onPress();
    await settle();
    expect(d.calls.open).toEqual([{ id: "lens", title: "Lens", rows: 29, focus: true }]);
    expect(flatten(await pane(d)).find((n) => n.el === "Input")?.props.value).toBe("why Keep the cache bounded");
    expect(d.calls.mcp).toHaveLength(asked);
  });

  it("stays hidden without a decision; a clear review then keeps the band quiet", async () => {
    const d = await reviewed(null);
    expect(buttonsOf(await band(d)).map((b) => b.props.label)).not.toContain("Why");
  });

  it("the recap counts the edited file and the surfaced decision", async () => {
    const d = await reviewed(NOTICE);
    await lens(d, "recap");
    const shown = textOf(await pane(d));
    const at = (label: string) => shown[shown.indexOf(`${label}: `) + 1];
    expect(at("Files touched")).toBe("1 edited · 1 read or edited");
    expect(at("Decisions surfaced")).toBe("1: Keep the cache bounded");
    expect(at("Code health (last review)")).toBe("no new findings in the 1 changed file");
  });
});

describe("the brief after a compaction", () => {
  it("never hooks session.compact; PostCompact passes on untouched", async () => {
    expect(events).not.toContain("session.compact");
    expect(events.filter((e) => e === "classic.PostCompact")).toHaveLength(1);
    const d = await session();
    const out = { from: "next" };
    expect(await hooks["classic.PostCompact"]!(d.$, { trigger: "auto" }, async () => out)).toBe(out);
  });

  it("offers Brief Claude only when there is something to brief; the press submits it visibly, once", async () => {
    const d = await session();
    await hooks["classic.PostCompact"]!(d.$, { trigger: "manual" }, async () => ({}));
    expect(buttonsOf(await band(d))).toEqual([]);
    await hooks["tool.call"]!(d.$, { tool: "Write", tool_use_id: "toolu_w", file_path: "/work/app/src/new.py" }, async () => ({ result: {} }));
    expect(d.calls.submitted).toEqual([]);
    const brief = buttonsOf(await band(d)).find((b) => b.props.label === "Brief Claude")!;
    expect(brief.props.hotkey).toBe("1");
    brief.props.onPress();
    await settle();
    expect(d.calls.submitted).toEqual([
      "The context was compacted. Lens brief of this session, from Repowise index lookups (no model):\nFiles edited: src/new.py",
    ]);
    expect(buttonsOf(await band(d))).toEqual([]);
  });

  it("a new turn retires the offer unsent", async () => {
    const d = await session();
    await hooks["tool.call"]!(d.$, { tool: "Write", tool_use_id: "toolu_w", file_path: "/work/app/src/new.py" }, async () => ({ result: {} }));
    await hooks["classic.PostCompact"]!(d.$, {}, async () => ({}));
    await hooks["turn.start"]!(d.$, {}, async () => undefined);
    expect(buttonsOf(await band(d))).toEqual([]);
    expect(d.calls.submitted).toEqual([]);
  });
});
