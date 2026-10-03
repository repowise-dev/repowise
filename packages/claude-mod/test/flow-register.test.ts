// Flow's wiring through register(): tool.call still returns next(e)'s value
// untouched while Flow records the call, the review's own result feeds the
// turn's health, redraws stay under 8 a second, and the owl ticks only while
// Claude works with Flow on screen.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { REDRAW_MS } from "../src/flow-controller";
import type { Hook, ModApi, On } from "../src/mod-api";
import { BLINK_MS } from "../src/views/owl";
import { buttonsOf, fixture, flatten, textOf } from "./fake-host";

type Hooks = Record<string, Hook<any>>;

async function load(options: Record<string, boolean> = {}): Promise<Hooks> {
  vi.resetModules();
  const { register } = await import("../src/register");
  const hooks: Hooks = {};
  const on = ((event: string, a: unknown, b?: unknown) => {
    const m = a as { component?: string; command?: string; element?: string };
    (hooks as Record<string, unknown>)[b === undefined ? event : `${event}:${m.component ?? m.command ?? m.element}`] = b ?? a;
  }) as On;
  register(on, options);
  return hooks;
}

function fakeDollar() {
  const calls = { invalidate: 0, logs: [] as string[] };
  const $: ModApi = {
    session: { cwd: async () => "C:\\work\\django" },
    fs: { read: async () => Promise.reject(new Error("ENOENT")), exists: async () => false },
    process: { run: async () => ({ exitCode: 1, stdout: "", stderr: "" }) },
    http: { fetch: async () => ({ status: 500, ok: false, headers: {}, text: "" }) },
    mcp: { connect: async () => ({ isConnected: false }), call: async () => Promise.reject(new Error("no MCP")) },
    prompt: { submit: async () => ({}) },
    ui: {
      invalidate: () => {
        calls.invalidate++;
      },
      log: (text) => {
        calls.logs.push(text);
      },
      resolve: () => Object.fromEntries(["Box", "Text", "Button", "Input", "Markdown", "Raster"].map((el) => [el, (props: Record<string, unknown>) => ({ el, props })])),
      open: async () => ({ isPlaced: true }),
      close: async () => undefined,
      blit: async () => ({}),
    },
    command: { register: async () => ({}) },
    settings: { read: async () => ({}) },
  };
  return { $, calls };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
const PANE = { surface: "terminal", requestId: "lens", props: { bodyColumns: 120, placement: "dock", scroll: { bodyRows: 40 } } };

let hooks: Hooks;
beforeEach(async () => {
  hooks = await load();
});
afterEach(() => {
  vi.useRealTimers();
});

async function session() {
  const d = fakeDollar();
  await hooks["session.start"]!(d.$, { cwd: "C:\\work\\django" }, async () => undefined);
  for (let i = 0; i < 4; i++) await settle();
  return d;
}
const pane = (d: { $: ModApi }) => hooks["ui.render:Pane"]!(d.$, PANE, async () => null);

describe("lens_flow off", () => {
  it("records nothing and Flow leaves the tab bar; tool.call is untouched", async () => {
    hooks = await load({ lens_flow: false });
    expect(Object.keys(hooks)).not.toContain("turn.step");
    const d = await session();
    expect(buttonsOf(await pane(d)).map((b) => b.props.label)).toEqual(["Map", "Recap"]);
    const out = { text: "x" };
    expect(await hooks["tool.call"]!(d.$, { tool: "Bash", tool_use_id: "b", command: "ls" }, async () => out)).toBe(out);
  });
});

describe("tool.call", () => {
  const RW = { tool: "mcp__repowise__get_context", tool_use_id: "toolu_rw", targets: ["django/db/models/query.py"] };

  it("returns next(e)'s value untouched; the call and what its reply was built from land in Flow", async () => {
    const d = await session();
    await hooks["turn.start"]!(d.$, { text: "<system-reminder>r</system-reminder>add a comment", turnId: "t" }, async () => ({ turnId: "t" }));
    const out = { ref: 1, result: {}, text: fixture("flow/get_context.json") };
    expect(await hooks["tool.call"]!(d.$, RW, async () => out)).toBe(out);
    const shown = textOf(await pane(d)).join("|");
    expect(shown).toContain("Repowise 1 call");
    expect(shown).toContain('"add a comment"');
    expect(shown).toContain("420 ms".slice(0, 0) + "1 target · 1 documentation page");
  });

  it("the turn's health is the change review's own result: no second get_change_risk", async () => {
    const risk = { health_delta: { status: "available", introduced: 1, worsened: 0, resolved: 0, top_findings: [{ change: "introduced", biomarker: "complex_method", path: "a.py" }] } };
    const d = await session();
    let reviews = 0;
    d.$.mcp.connect = async () => ({ isConnected: true, server: "plugin:repowise:repowise" });
    d.$.mcp.call = async (_s, tool) => {
      if (tool === "get_change_risk") reviews++;
      return { content: [{ type: "text", text: JSON.stringify({ result: risk }) }], isError: false };
    };
    await hooks["session.start"]!(d.$, { cwd: "C:\\work\\django" }, async () => undefined);
    for (let i = 0; i < 4; i++) await settle();
    await hooks["turn.start"]!(d.$, { text: "edit", turnId: "t" }, async () => ({}));
    await hooks["tool.call"]!(d.$, { tool: "Edit", tool_use_id: "e", file_path: "C:\\work\\django\\a.py" }, async () => ({ result: { type: "update" } }));
    await hooks["turn.complete"]!(d.$, { reason: "answer", answer: "done" }, async () => ({ text: "done" }));
    await settle();
    const shown = textOf(await pane(d)).join("|");
    expect(shown).toContain("this turn introduced 1 finding: complex method in a.py");
    expect(reviews).toBe(1);
  });

  it("a call that throws rethrows the same error and shows as failed", async () => {
    const d = await session();
    const err = new Error("denied");
    await expect(hooks["tool.call"]!(d.$, { ...RW, tool_use_id: "bad" }, async () => Promise.reject(err))).rejects.toBe(err);
    expect(textOf(await pane(d))).toContain("error");
  });

  it("Lens's own calls are not Claude's steps", async () => {
    const d = await session();
    await hooks["tool.call"]!(d.$, { ...RW, tool_use_id: "toolu_plugin_1" }, async () => ({ text: "{}" }));
    expect(textOf(await pane(d))).not.toContain("get_context");
  });

  it("a row's press opens its detail; j and k step between calls", async () => {
    const d = await session();
    await hooks["tool.call"]!(d.$, RW, async () => ({ text: fixture("flow/get_context.json") }));
    await hooks["tool.call"]!(d.$, { ...RW, tool_use_id: "toolu_2", tool: "mcp__repowise__get_risk" }, async () => ({ text: fixture("flow/get_risk.json") }));
    const press = async (label: string) => buttonsOf(await pane(d)).find((b) => b.props.label === label)!.props.onPress();
    await press("get_context");
    expect(textOf(await pane(d)).join("|")).toContain("how it was answered");
    await press("Next call");
    const shown = textOf(await pane(d)).join("|");
    expect(shown).toContain("1,849 tokens left out, restorable");
    await press("Previous call");
    expect(textOf(await pane(d)).join("|")).toContain("reply 7.4 KB (cap 24,000 characters)");
  });

  it("turn.complete says how the turn ended: stopped and did not finish in plain words, no thistle", async () => {
    for (const [reason, word] of [["aborted", "Stopped"], ["error", "Did not finish"], ["answer", "Done"]] as const) {
      const d = await session();
      await hooks["turn.start"]!(d.$, { text: "go", turnId: "t" }, async () => ({}));
      await hooks["turn.complete"]!(d.$, { reason, durationMs: 2_000 }, async () => ({}));
      const shown = textOf(await pane(d));
      expect(shown).toContain(`${word} · 2.0 s`);
      expect(shown.some((t) => t.includes("⚘"))).toBe(reason === "answer");
    }
  });
});

describe("redraws and the owl's clock", () => {
  const bash = (d: { $: ModApi }, id: string) => hooks["tool.call"]!(d.$, { tool: "Bash", tool_use_id: id, command: "ls" }, async () => ({}));

  it("at most one redraw per 125 ms while Flow is shown; none while another tab is", async () => {
    const d = await session();
    vi.useFakeTimers();
    await pane(d);
    const before = d.calls.invalidate;
    for (let i = 0; i < 20; i++) await bash(d, `b${i}`);
    expect(d.calls.invalidate - before).toBe(1);
    vi.advanceTimersByTime(REDRAW_MS);
    expect(d.calls.invalidate - before).toBe(2);
    vi.advanceTimersByTime(REDRAW_MS * 10);
    expect(d.calls.invalidate - before).toBe(2);
    // On the Map tab Flow asks for no redraws.
    buttonsOf(await pane(d)).find((b) => b.props.label === "Map")!.props.onPress();
    await pane(d);
    const onMap = d.calls.invalidate;
    await bash(d, "late");
    vi.advanceTimersByTime(REDRAW_MS * 4);
    expect(d.calls.invalidate).toBe(onMap);
  });

  it("a closed pane: once redraws go unanswered for a second Flow stops asking, until it is drawn again", async () => {
    const d = await session();
    vi.useFakeTimers();
    await pane(d);
    const before = d.calls.invalidate;
    // The call's start redraws at once, its end once the throttle allows.
    await bash(d, "a");
    vi.advanceTimersByTime(REDRAW_MS);
    expect(d.calls.invalidate - before).toBe(2);
    // No render comes back.
    vi.advanceTimersByTime(1_000);
    await bash(d, "b");
    await bash(d, "c");
    vi.advanceTimersByTime(1_000);
    expect(d.calls.invalidate - before).toBe(2);
    await pane(d);
    await bash(d, "d");
    expect(d.calls.invalidate - before).toBe(3);
  });

  it("ticks while Claude works and renders come back; stops when they do not, and at the turn's end", async () => {
    const d = await session();
    vi.useFakeTimers();
    await hooks["turn.start"]!(d.$, { text: "go", turnId: "t" }, async () => ({}));
    await pane(d);
    let n = d.calls.invalidate;
    vi.advanceTimersByTime(BLINK_MS);
    expect(d.calls.invalidate).toBe(n + 1);
    await pane(d);
    vi.advanceTimersByTime(BLINK_MS);
    expect(d.calls.invalidate).toBe(n + 2);
    // No render comes back (the pane closed): within a second the clock stops.
    vi.advanceTimersByTime(BLINK_MS * 20);
    const stopped = d.calls.invalidate;
    expect(stopped - n).toBeLessThanOrEqual(2 + Math.ceil(1_000 / BLINK_MS));
    vi.advanceTimersByTime(BLINK_MS * 20);
    expect(d.calls.invalidate).toBe(stopped);
    await pane(d);
    await hooks["turn.complete"]!(d.$, { reason: "answer", durationMs: 4_000 }, async () => ({}));
    await pane(d);
    n = d.calls.invalidate;
    vi.advanceTimersByTime(BLINK_MS * 5);
    expect(d.calls.invalidate).toBe(n);
    // One redraw when the owl's happy moment ends, then quiet; the status stays.
    vi.advanceTimersByTime(3_000);
    expect(d.calls.invalidate).toBe(n + 1);
    vi.advanceTimersByTime(10_000);
    expect(d.calls.invalidate).toBe(n + 1);
    expect(textOf(await pane(d))).toContain("Done · 4.0 s");
  });

  it("a timer that fails logs it and keeps the runtime clear", async () => {
    const d = await session();
    vi.useFakeTimers();
    await pane(d);
    await bash(d, "a");
    d.$.ui.invalidate = () => {
      throw new Error("gone");
    };
    await bash(d, "b");
    vi.advanceTimersByTime(REDRAW_MS);
    expect(d.calls.logs).toContain("lens: flow timer failed: Error: gone");
  });

  it("with reduced motion the owl holds still and the turn's clock ticks once a second", async () => {
    const d = fakeDollar();
    d.$.settings.read = async () => ({ prefersReducedMotion: true });
    await hooks["session.start"]!(d.$, {}, async () => undefined);
    for (let i = 0; i < 4; i++) await settle();
    vi.useFakeTimers();
    await hooks["turn.start"]!(d.$, { text: "go", turnId: "t" }, async () => ({}));
    await pane(d);
    const n = d.calls.invalidate;
    vi.advanceTimersByTime(999);
    expect(d.calls.invalidate).toBe(n);
    vi.advanceTimersByTime(1);
    expect(d.calls.invalidate).toBe(n + 1);
    expect(flatten(await pane(d)).some((x) => x.el === "Text" && x.props.children?.[0] === "◉")).toBe(true);
  });
});
