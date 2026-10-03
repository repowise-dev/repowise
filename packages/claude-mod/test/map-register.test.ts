// The map's wiring through register(): /lens, the Pane, the trail from tool
// calls, importers and the ripple, auto-open. Answers come from the recorded
// Django payloads (test/fixtures).
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Hook, ModApi, On, PluginOptions } from "../src/mod-api";
import { MAP_COPY, PANE_COPY } from "../src/views/copy";
import { DARK_CANVAS_BAND } from "@repowise-dev/ui/brand";
import { decode } from "./django";
import { fixture, flatten, textOf, type Tree } from "./fake-host";

type Hooks = Record<string, Hook<any>>;

/** Hooks by event, and by `event:component` / `event:command` where a matcher narrows them. */
async function load(options?: PluginOptions): Promise<Hooks> {
  vi.resetModules();
  const { register } = await import("../src/register");
  const hooks: Hooks = {};
  const on = ((event: string, a: unknown, b?: unknown) => {
    const matcher = b === undefined ? null : (a as { component?: string; command?: string });
    const name = matcher === null ? event : `${event}:${matcher.component ?? matcher.command}`;
    hooks[name] = (b ?? a) as Hook<any>;
  }) as On;
  register(on, options);
  return hooks;
}

interface Calls {
  cwd: number;
  invalidate: number;
  logs: string[];
  open: unknown[];
  close: unknown[];
  blit: { columns: number; rows: number; cells: string }[];
  commands: unknown[];
  http: string[];
}

/** The surface's side: redraws, logs, elements, and the pane and blit calls, all recorded. */
function uiFake(calls: Calls): ModApi["ui"] {
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
      Raster: (props) => ({ el: "Raster", props }),
      Button: (props) => ({ el: "Button", props }),
      Input: (props) => ({ el: "Input", props }),
      Markdown: (props) => ({ el: "Markdown", props }),
      Svg: (props) => ({ el: "Svg", props }),
    }),
    open: async (pane) => {
      calls.open.push(pane);
      return { isPlaced: true };
    },
    close: async (pane) => {
      calls.close.push(pane);
    },
    blit: async (args) => {
      calls.blit.push(args);
      return {};
    },
  };
}

/** A `$` for a git work tree with no index and a connecting MCP server. */
function fakeDollar() {
  const calls: Calls = { cwd: 0, invalidate: 0, logs: [], open: [], close: [], blit: [], commands: [], http: [] };
  let release: () => void = () => {};
  const gate = { hold: false, wait: Promise.resolve() };
  const $: ModApi = {
    session: {
      cwd: async () => {
        calls.cwd++;
        if (gate.hold) await gate.wait;
        return "/work/app";
      },
    },
    fs: { read: async () => Promise.reject(new Error("ENOENT")), exists: async () => false },
    process: {
      run: async (argv) => ({ exitCode: 0, stdout: argv.includes("--is-inside-work-tree") ? "true\n" : "", stderr: "" }),
    },
    http: { fetch: async () => ({ status: 500, ok: false, headers: {}, text: "" }) },
    mcp: {
      connect: async () => ({ isConnected: true }),
      call: async () => Promise.reject(new Error("no MCP in this test")),
    },
    prompt: { submit: async () => ({}) },
    ui: uiFake(calls),
    command: {
      register: async (c) => {
        calls.commands.push(c);
        return { command: c.name };
      },
    },
    config: { list: async () => [] },
    settings: { read: async () => ({}) },
  };
  const hold = () => {
    gate.hold = true;
    gate.wait = new Promise<void>((resolve) => (release = resolve));
  };
  return { $, calls, hold, release: () => release() };
}

const ROOT = "C:\\work\\django";
const SHA = "e78991410b78391bc07a4b5010b273dfec814ff6";
const json = (status: number, body: unknown) => ({ status, ok: status < 300, headers: {}, text: JSON.stringify(body) });

/** A `$` in full mode: an indexed Django copy with its local server up, answering from recorded payloads. */
function fullDollar() {
  const d = fakeDollar();
  const files: Record<string, string> = {
    "C:/work/django/.repowise/state.json": JSON.stringify({ last_sync_commit: SHA }),
    "C:/work/django/.repowise/serve.lock.json": fixture("locks/valid.json"),
  };
  d.$.session.cwd = async () => ROOT;
  d.$.fs = {
    exists: async (p) => p.replace(/\\/g, "/") in files,
    read: async (p) => files[p.replace(/\\/g, "/")] ?? Promise.reject(new Error("ENOENT")),
  };
  d.$.process.run = async (argv) => {
    if (argv[0] === "tasklist") return { exitCode: 0, stdout: '"python.exe","4242","Console"\r\n', stderr: "" };
    if (argv.includes("HEAD")) return { exitCode: 0, stdout: `${SHA}\n`, stderr: "" };
    return { exitCode: 0, stdout: "true\n", stderr: "" };
  };
  d.$.http.fetch = async (url) => {
    d.calls.http.push(url);
    if (url.endsWith("/health")) return json(200, { status: "healthy" });
    if (url.endsWith("/api/repos")) {
      return json(200, [{ id: "dj", name: "django", local_path: "c:/work/django/", updated_at: null }]);
    }
    if (url.includes("/health/map")) return { status: 200, ok: true, headers: {}, text: fixture("django-health-map.json") };
    if (url.endsWith("/blast-radius")) {
      return { status: 200, ok: true, headers: {}, text: fixture("django-blast-radius-query-depth1.json") };
    }
    return json(404, {});
  };
  return d;
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
async function until(check: () => boolean): Promise<void> {
  for (let i = 0; i < 200 && !check(); i++) await new Promise((resolve) => setTimeout(resolve, 5));
  expect(check()).toBe(true);
}
const band = { props: { hasSurvey: false, bodyColumns: 100 } };
const pane = (requestId = "lens", surface = "terminal") => ({
  surface,
  requestId,
  props: { bodyColumns: 180, placement: "dock", scroll: { bodyRows: 55 } },
});
const THEIRS = { el: "theirs" };

let hooks: Hooks;
beforeEach(async () => {
  hooks = await load();
});

describe("register, for the map", () => {
  it("an observer that cannot learn the cwd still returns the tool's result", async () => {
    const { $, calls } = fakeDollar();
    $.session.cwd = async () => {
      throw new Error("gone");
    };
    const marker = { result: {} };
    expect(await hooks["tool.call"]!($, { tool: "Read", file_path: "/a" }, async () => marker)).toBe(marker);
    await settle();
    expect(calls.logs).toEqual(["lens: cwd failed: Error: gone"]);
  });

  it("session.start registers /lens", async () => {
    const { $, calls } = fakeDollar();
    await hooks["session.start"]!($, {}, async () => undefined);
    expect(calls.commands).toEqual([
      { name: "lens", description: PANE_COPY.command, argumentHint: "[flow | map | ask <question> | recap]", immediate: true },
    ]);
  });
});

type Full = ReturnType<typeof fullDollar>;
const START = { cwd: ROOT };
const editQuery = { tool: "Edit", tool_use_id: "toolu_9", file_path: `${ROOT}\\django\\db\\models\\query.py` };
const asked = (d: Full, part: string) => d.calls.http.filter((u) => u.includes(part)).length;
const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function started(d: Full = fullDollar()): Promise<Full> {
  await hooks["session.start"]!(d.$, START, async () => undefined);
  await until(() => asked(d, "/api/repos") > 0);
  await settle();
  return d;
}

async function opened(d: Full = fullDollar()): Promise<Full> {
  await started(d);
  await hooks["command.run:lens"]!(d.$, { command: "lens", args: "map" }, async () => ({ text: "core" }));
  await until(() => asked(d, "/health/map?cap=4000") > 0);
  await settle();
  await settle();
  return d;
}

const render = async (d: Full) => (await hooks["ui.render:Pane"]!(d.$, pane(), async () => THEIRS)) as Tree;

describe("the map pane", () => {
  it("/lens opens the pane with the keyboard (it was asked for) and prints nothing", async () => {
    const d = await started();
    expect(await hooks["command.run:lens"]!(d.$, { command: "lens", args: "map" }, async () => ({ text: "core" }))).toEqual({});
    expect(d.calls.open).toEqual([{ id: "lens", title: "Lens", rows: 29, focus: true }]);
  });

  it("/lens on a surface that cannot place the pane says why in one band line", async () => {
    const d = await started();
    d.$.ui.open = async () => ({ isPlaced: false, reason: "below 110 columns (90 now)" });
    await hooks["command.run:lens"]!(d.$, { command: "lens", args: "map" }, async () => ({}));
    const shown = JSON.stringify(await hooks["ui.render:AbovePrompt"]!(d.$, band, async () => null));
    expect(shown).toContain("Lens map needs a wider terminal: below 110 columns (90 now)");
  });

  it("the map is quiet ghost tiles until the Health colours button turns the bands on; lens_map_health starts it on", async () => {
    const d = await opened();
    const bands = (tree: Tree) => decode(String(flatten(tree).find((n) => n.el === "Raster")!.props.cells)).some((w, i) => i % 3 === 1 && w === parseInt(DARK_CANVAS_BAND.good.slice(1), 16));
    let tree = await render(d);
    expect(bands(tree)).toBe(false);
    const toggle = flatten(tree).find((n) => n.props.key === "lens-map-health")!;
    expect(toggle.props.label).toBe(MAP_COPY.healthOff);
    toggle.props.onPress();
    tree = await render(d);
    expect(bands(tree)).toBe(true);
    expect(flatten(tree).find((n) => n.props.key === "lens-map-health")!.props.label).toBe(MAP_COPY.healthOn);
    hooks = await load({ lens_map_health: true });
    expect(bands(await render(await opened()))).toBe(true);
  });

  it("draws the Django map with a legend that counts what it drew", async () => {
    const d = await opened();
    const tree = await render(d);
    const r = flatten(tree).find((n) => n.el === "Raster")!;
    // The pane's 55 rows less the tab row and the rows under the map (story, scope, toggle).
    expect(r.props).toMatchObject({ key: "lens-map", columns: 180, rows: 47 });
    expect(r.props.cells).toHaveLength(180 * 47 * 16);
    expect(textOf(tree).some((t) => /^1,\d{3} of 2,970 files drawn at this size/.test(t))).toBe(true);
  });

  it("the desktop app gets the same map as SVG under the element's limit, and the same legend", async () => {
    const d = await opened();
    const tree = (await hooks["ui.render:Pane"]!(d.$, pane("lens", "desktop"), async () => THEIRS)) as Tree;
    const drawing = flatten(tree).find((n) => n.el === "Svg")!;
    expect(String(drawing.props.source).startsWith("<svg ")).toBe(true);
    expect(String(drawing.props.source).length).toBeLessThan(131_072);
    expect(drawing.props.alt).toMatch(/^Map of the repo: 1,\d{3} files drawn as tiles/);
    expect(flatten(tree).some((n) => n.el === "Raster")).toBe(false);
    expect(textOf(tree).some((t) => / of 2,970 files drawn at this size/.test(t))).toBe(true);
  });

  it("at rest nothing is asked of the server: no map, no importers, until /lens", async () => {
    const d = await started();
    await hooks["tool.call"]!(d.$, editQuery, async () => ({ result: {} }));
    await wait(30);
    expect([asked(d, "/health/map"), asked(d, "/blast-radius")]).toEqual([0, 0]);
    // Asked for later, the map fetches the importers of the edit it missed.
    await hooks["command.run:lens"]!(d.$, { command: "lens", args: "map" }, async () => ({}));
    await until(() => asked(d, "/blast-radius") === 1);
  });

  it("an edit fetches its direct importers once and ripples them in with blits", async () => {
    const d = await opened();
    await render(d);
    await hooks["tool.call"]!(d.$, editQuery, async () => ({ result: {} }));
    await until(() => d.calls.blit.length >= 2);
    expect(d.calls.blit[0]).toMatchObject({ requestId: "lens", key: "lens-map", columns: 180, rows: 47 });
    const shown = textOf(await render(d));
    expect(shown.some((t) => t.startsWith("12 importers: "))).toBe(true);
    expect(shown).toContain("◉ query.py");
    expect(asked(d, "/blast-radius")).toBe(1);
  });

  /** A turn that edits query.py, drawn: the prompt on Flow, the edit and its importers on the map. */
  async function editedTurn(): Promise<Full> {
    const d = await opened();
    await hooks["turn.start"]!(d.$, { text: "tidy the bulk create path", turnId: "t1" }, async () => ({}));
    await render(d);
    await hooks["tool.call"]!(d.$, editQuery, async () => ({ result: {} }));
    await until(() => asked(d, "/blast-radius") === 1);
    await settle();
    expect(textOf(await render(d)).some((t) => t.startsWith("12 importers: "))).toBe(true);
    return d;
  }
  const lens = (d: Full, args: string) => hooks["command.run:lens"]!(d.$, { command: "lens", args }, async () => ({}));
  const flowText = async (d: Full) => {
    await lens(d, "flow");
    return JSON.stringify(await render(d));
  };

  it("/clear starts the conversation over: Flow and the map's lighting go, the health map stays and is not asked again", async () => {
    const d = await editedTurn();
    expect(await flowText(d)).toContain("tidy the bulk create path");
    await hooks["session.end"]!(d.$, { reason: "clear", sessionId: "s1" }, async () => ({}));
    expect(await flowText(d)).not.toContain("tidy the bulk create path");
    await lens(d, "map");
    const tree = await render(d);
    expect(flatten(tree).some((n) => n.el === "Raster")).toBe(true);
    const shown = textOf(tree);
    expect(shown).toContain(MAP_COPY.quiet);
    expect(shown.some((t) => t.includes("importers") || t.includes("query.py"))).toBe(false);
    expect(asked(d, "/health/map")).toBe(1);
    expect(d.calls.logs.filter((l) => /clear failed|render failed/.test(l))).toEqual([]);
  });

  it("an end that is not a /clear (a resume, an exit) leaves the turn lit", async () => {
    const d = await editedTurn();
    await hooks["session.end"]!(d.$, { reason: "resume", sessionId: "s1" }, async () => ({}));
    expect(textOf(await render(d)).some((t) => t.startsWith("12 importers: "))).toBe(true);
    expect(await flowText(d)).toContain("tidy the bulk create path");
  });

  it("Flow reads the same blast radius answer: its importers before you accept, with no second request", async () => {
    const d = await opened();
    await render(d);
    await hooks["tool.call"]!(d.$, editQuery, async () => ({ result: {} }));
    await until(() => d.calls.blit.length >= 2);
    flatten(await render(d)).find((n) => n.props.key === "lens-tab-flow")!.props.onPress();
    const shown = textOf(await render(d));
    expect(shown).toContain("BEFORE YOU ACCEPT");
    expect(shown).toContain("    12 direct importers; Claude opened none");
    expect(asked(d, "/blast-radius")).toBe(1);
  });

  it("importers are asked once per edited file: five edits to one file, one request; a second file, a second", async () => {
    const d = await opened();
    for (let i = 0; i < 5; i++) await hooks["tool.call"]!(d.$, { ...editQuery, tool_use_id: `toolu_q${i}` }, async () => ({ result: {} }));
    await until(() => asked(d, "/blast-radius") === 1);
    await wait(30);
    expect(asked(d, "/blast-radius")).toBe(1);
    const editBase = { tool: "Edit", tool_use_id: "toolu_b", file_path: `${ROOT}\\django\\db\\models\\base.py` };
    await hooks["tool.call"]!(d.$, editBase, async () => ({ result: {} }));
    await hooks["tool.call"]!(d.$, { ...editBase, tool_use_id: "toolu_b2" }, async () => ({ result: {} }));
    await until(() => asked(d, "/blast-radius") === 2);
    await wait(30);
    expect(asked(d, "/blast-radius")).toBe(2);
  });

  it("edits before the first /lens are each asked for when it opens", async () => {
    const d = await started();
    await hooks["tool.call"]!(d.$, editQuery, async () => ({ result: {} }));
    await hooks["tool.call"]!(d.$, { tool: "Edit", tool_use_id: "toolu_b", file_path: `${ROOT}\\django\\db\\models\\base.py` }, async () => ({ result: {} }));
    expect(asked(d, "/blast-radius")).toBe(0);
    await hooks["command.run:lens"]!(d.$, { command: "lens", args: "map" }, async () => ({}));
    await until(() => asked(d, "/blast-radius") === 2);
  });

  it("observing a tool call waits on nothing once next(e) resolved", async () => {
    const d = await opened();
    d.$.session.cwd = () => new Promise(() => {});
    const out = { result: {} };
    expect(await hooks["tool.call"]!(d.$, editQuery, async () => out)).toBe(out);
  });

  it("drops the importers an earlier session asked for", async () => {
    const d = await opened();
    let release: () => void = () => {};
    const fetch = d.$.http.fetch;
    d.$.http.fetch = async (url, init) => {
      if (url.endsWith("/blast-radius")) await new Promise<void>((r) => (release = r));
      return fetch(url, init);
    };
    await hooks["tool.call"]!(d.$, editQuery, async () => ({ result: {} }));
    await settle();
    await hooks["session.start"]!(d.$, START, async () => undefined);
    release();
    await wait(30);
    expect(textOf(await render(d)).some((t) => t.startsWith("edited"))).toBe(false);
  });

  it("one blit at a time: a surface that never answers gets no second frame", async () => {
    const d = await opened();
    await render(d);
    d.$.ui.blit = (args) => {
      d.calls.blit.push(args);
      return new Promise(() => {});
    };
    await hooks["tool.call"]!(d.$, { tool: "Glob", pattern: "*" }, async () => ({ result: { filenames: ["setup.py"] } }));
    await wait(150);
    expect(d.calls.blit).toHaveLength(1);
  });

  it("a blit the surface refuses stops the animation and redraws", async () => {
    const d = await opened();
    await render(d);
    d.$.ui.blit = async (args) => {
      d.calls.blit.push(args);
      return { deny: "another size" };
    };
    const before = d.calls.invalidate;
    await hooks["tool.call"]!(d.$, { tool: "Glob", pattern: "*" }, async () => ({ result: { filenames: ["setup.py"] } }));
    await until(() => d.calls.blit.length >= 1);
    await wait(100);
    expect(d.calls.blit).toHaveLength(1);
    expect(d.calls.invalidate).toBeGreaterThan(before + 1);
  });

  it("no ripple when there is nowhere to ripple to: an edit off the map", async () => {
    const d = await opened();
    await render(d);
    await hooks["tool.call"]!(d.$, { tool: "Write", file_path: `${ROOT}\\new_module.py` }, async () => ({ result: {} }));
    await until(() => asked(d, "/blast-radius") === 1);
    await wait(100);
    expect(d.calls.blit).toEqual([]);
  });

  it("with reduced motion, attention redraws the resting frame and never blits", async () => {
    const d = fullDollar();
    d.$.settings.read = async () => ({ prefersReducedMotion: true });
    await opened(d);
    await render(d);
    await hooks["tool.call"]!(d.$, { tool: "Grep", tool_use_id: "toolu_g", pattern: "x" }, async () => ({ result: { filenames: ["django/db/models/query.py"] } }));
    await hooks["tool.call"]!(d.$, editQuery, async () => ({ result: {} }));
    await until(() => asked(d, "/blast-radius") === 1);
    await wait(120);
    expect(d.calls.blit).toEqual([]);
    expect(textOf(await render(d))).toContain("x: 1 file");
  });

  it("an edit with no local server asks nothing of the network", async () => {
    const d = fakeDollar();
    await hooks["session.start"]!(d.$, { cwd: "/work/app" }, async () => undefined);
    await settle();
    await hooks["command.run:lens"]!(d.$, { command: "lens", args: "map" }, async () => ({}));
    await hooks["tool.call"]!(d.$, { tool: "Edit", file_path: "/work/app/a.py" }, async () => ({ result: {} }));
    expect(d.calls.http).toEqual([]);
  });

  /** Flow is the first tab: press Map on the drawn tab bar. */
  async function showMap($: ModApi): Promise<void> {
    const tabs = flatten(await hooks["ui.render:Pane"]!($, pane(), async () => THEIRS)).filter((n) => n.el === "Button");
    tabs.find((b) => b.props.key === "lens-tab-map")!.props.onPress();
  }

  it("says why when there is no map: no local server, or another surface", async () => {
    const lite = fakeDollar();
    await hooks["session.start"]!(lite.$, {}, async () => undefined);
    await settle();
    await showMap(lite.$);
    const tree = (await hooks["ui.render:Pane"]!(lite.$, pane(), async () => THEIRS)) as Tree;
    expect(textOf(tree)).toEqual(["index this repo for Lens: repowise init --no-prose --yes"]);
    const vscode = (await hooks["ui.render:Pane"]!(lite.$, pane("lens", "vscode"), async () => THEIRS)) as Tree;
    expect(textOf(vscode)).toEqual([MAP_COPY.desktop]);
  });

  it("before discovery lands, the pane says it is looking", async () => {
    const { $ } = fakeDollar();
    await showMap($);
    expect(textOf((await hooks["ui.render:Pane"]!($, pane(), async () => THEIRS)) as Tree)).toEqual([MAP_COPY.looking]);
  });

  it("a failed health map says so, and /lens tries again", async () => {
    const d = fullDollar();
    const fetch = d.$.http.fetch;
    let mapCalls = 0;
    d.$.http.fetch = async (url, init) => {
      if (url.includes("/health/map") && ++mapCalls === 1) return json(500, { detail: "boom" });
      return fetch(url, init);
    };
    await started(d);
    await hooks["command.run:lens"]!(d.$, { command: "lens", args: "map" }, async () => ({}));
    await until(() => mapCalls === 1);
    await settle();
    expect(textOf(await render(d))).toEqual([MAP_COPY.failed]);
    await hooks["command.run:lens"]!(d.$, { command: "lens", args: "map" }, async () => ({}));
    await until(() => mapCalls === 2);
  });

  it("a render that throws hands the site back", async () => {
    const d = fakeDollar();
    d.$.ui.resolve = () => {
      throw new Error("no table");
    };
    expect(await hooks["ui.render:Pane"]!(d.$, pane(), async () => THEIRS)).toBe(THEIRS);
    expect(d.calls.logs).toEqual(["lens: pane render failed: Error: no table"]);
  });

  it("loaded mid-session: learns the cwd from the first call and observes from the next", async () => {
    const d = await started();
    await hooks["session.start"]!(d.$, {}, async () => undefined);
    await hooks["command.run:lens"]!(d.$, { command: "lens", args: "map" }, async () => ({}));
    await hooks["tool.call"]!(d.$, editQuery, async () => ({ result: {} }));
    await settle();
    await hooks["tool.call"]!(d.$, editQuery, async () => ({ result: {} }));
    await until(() => asked(d, "/blast-radius") === 1);
  });
});

describe("auto-open", () => {
  const read = { tool: "Read", file_path: `${ROOT}\\django\\db\\models\\query.py` };

  async function start(options: PluginOptions, placed: boolean): Promise<Full> {
    hooks = await load(options);
    const d = fullDollar();
    d.$.ui.open = async (p) => {
      d.calls.open.push(p);
      return placed ? { isPlaced: true } : { isPlaced: false, reason: "below 144 columns" };
    };
    return started(d);
  }

  it("stays shut when the toggle is off", async () => {
    const d = await start({}, true);
    await hooks["tool.call"]!(d.$, read, async () => ({ result: {} }));
    expect(d.calls.open).toEqual([]);
  });

  it("opens on the first read when on and placed, once, and fetches the map", async () => {
    const d = await start({ lens_pane_autoopen: true }, true);
    await hooks["tool.call"]!(d.$, read, async () => ({ result: {} }));
    await hooks["tool.call"]!(d.$, read, async () => ({ result: {} }));
    // Never the keyboard: the person did not ask for it.
    expect(d.calls.open).toEqual([{ id: "lens", title: "Lens", rows: 29 }]);
    await until(() => asked(d, "/health/map") === 1);
    // It opens to the map, not to Flow.
    const shown = await hooks["ui.render:Pane"]!(d.$, pane(), async () => THEIRS);
    expect(flatten(shown).some((n) => n.props.key === "lens-flow")).toBe(false);
  });

  it("never touches a pane the person opened with /lens", async () => {
    const d = await start({ lens_pane_autoopen: true }, false);
    await hooks["command.run:lens"]!(d.$, { command: "lens", args: "map" }, async () => ({}));
    await hooks["tool.call"]!(d.$, read, async () => ({ result: {} }));
    await settle();
    expect(d.calls.open).toHaveLength(1);
    expect(d.calls.close).toEqual([]);
  });

  it("too narrow: closes the waiting pane and says so in one band line until the turn ends", async () => {
    const d = await start({ lens_pane_autoopen: true }, false);
    await hooks["tool.call"]!(d.$, read, async () => ({ result: {} }));
    await until(() => d.calls.close.length === 1);
    await settle();
    expect(JSON.stringify(await hooks["ui.render:AbovePrompt"]!(d.$, band, async () => null))).toContain(MAP_COPY.waiting);
    await hooks["turn.complete"]!(d.$, {}, async () => undefined);
    const after = await hooks["ui.render:AbovePrompt"]!(d.$, band, async () => null);
    expect(JSON.stringify(after ?? null)).not.toContain(MAP_COPY.waiting);
  });
});
