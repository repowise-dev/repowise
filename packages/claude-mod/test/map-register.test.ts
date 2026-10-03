// The map's wiring through register(): /lens, the Pane, the trail from tool
// calls, importers and the ripple, auto-open. Answers come from the recorded
// Django payloads (test/fixtures).
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Hook, ModApi, On, PluginOptions } from "../src/mod-api";
import { MAP_COPY } from "../src/views/copy";
import { fixture } from "./fake-host";

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
    expect(calls.commands).toEqual([{ name: "lens", description: MAP_COPY.command, immediate: true }]);
  });
});

type Tree = { el: string; props: Record<string, any> };
function flatten(t: Tree): Tree[] {
  const kids = Array.isArray(t.props.children) ? (t.props.children as unknown[]) : [];
  return [t, ...kids.filter((k): k is Tree => typeof k === "object" && k !== null).flatMap(flatten)];
}
const textOf = (t: Tree) =>
  flatten(t)
    .filter((n) => n.el === "Text")
    .map((n) => (n.props.children as string[]).join(""));

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
  await hooks["command.run:lens"]!(d.$, { command: "lens" }, async () => ({ text: "core" }));
  await until(() => asked(d, "/health/map?cap=4000") > 0);
  await settle();
  await settle();
  return d;
}

const render = async (d: Full) => (await hooks["ui.render:Pane"]!(d.$, pane(), async () => THEIRS)) as Tree;

describe("the map pane", () => {
  it("/lens opens the pane without taking the keyboard and prints nothing", async () => {
    const d = await started();
    expect(await hooks["command.run:lens"]!(d.$, { command: "lens" }, async () => ({ text: "core" }))).toEqual({});
    expect(d.calls.open).toEqual([{ id: "lens", title: "Lens", rows: 28 }]);
  });

  it("/lens on a surface that cannot place the pane says why in one band line", async () => {
    const d = await started();
    d.$.ui.open = async () => ({ isPlaced: false, reason: "below 110 columns (90 now)" });
    await hooks["command.run:lens"]!(d.$, { command: "lens" }, async () => ({}));
    const shown = JSON.stringify(await hooks["ui.render:AbovePrompt"]!(d.$, band, async () => null));
    expect(shown).toContain("Lens map needs a wider terminal: below 110 columns (90 now)");
  });

  it("draws the Django map with a legend that counts what it drew", async () => {
    const d = await opened();
    const tree = await render(d);
    const r = flatten(tree).find((n) => n.el === "Raster")!;
    expect(r.props).toMatchObject({ key: "lens-map", columns: 180, rows: 49 });
    expect(r.props.cells).toHaveLength(180 * 49 * 16);
    expect(textOf(tree).some((t) => /^1,\d{3} of 2,970 files drawn at this size/.test(t))).toBe(true);
  });

  it("at rest nothing is asked of the server: no map, no importers, until /lens", async () => {
    const d = await started();
    await hooks["tool.call"]!(d.$, editQuery, async () => ({ result: {} }));
    await wait(30);
    expect([asked(d, "/health/map"), asked(d, "/blast-radius")]).toEqual([0, 0]);
    // Asked for later, the map fetches the importers of the edit it missed.
    await hooks["command.run:lens"]!(d.$, { command: "lens" }, async () => ({}));
    await until(() => asked(d, "/blast-radius") === 1);
  });

  it("an edit fetches its direct importers once and ripples them in with blits", async () => {
    const d = await opened();
    await render(d);
    await hooks["tool.call"]!(d.$, editQuery, async () => ({ result: {} }));
    await until(() => d.calls.blit.length >= 2);
    expect(d.calls.blit[0]).toMatchObject({ requestId: "lens", key: "lens-map", columns: 180, rows: 49 });
    const shown = textOf(await render(d));
    // At 180x49 one of the twelve is under a pixel: counted, not hidden.
    expect(shown).toContain("edited query.py · 12 files import it (from imports, not calls) · 1 not drawn");
    expect(shown).toContain("1 file read");
    expect(asked(d, "/blast-radius")).toBe(1);
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
    await hooks["tool.call"]!(d.$, { tool: "Grep", pattern: "x" }, async () => ({ result: { filenames: ["django/db/models/query.py"] } }));
    await hooks["tool.call"]!(d.$, editQuery, async () => ({ result: {} }));
    await until(() => asked(d, "/blast-radius") === 1);
    await wait(120);
    expect(d.calls.blit).toEqual([]);
    expect(textOf(await render(d))).toContain("1 file read · last search matched 1 file");
  });

  it("an edit with no local server asks nothing of the network", async () => {
    const d = fakeDollar();
    await hooks["session.start"]!(d.$, { cwd: "/work/app" }, async () => undefined);
    await settle();
    await hooks["command.run:lens"]!(d.$, { command: "lens" }, async () => ({}));
    await hooks["tool.call"]!(d.$, { tool: "Edit", file_path: "/work/app/a.py" }, async () => ({ result: {} }));
    expect(d.calls.http).toEqual([]);
  });

  it("says why when there is no map: no local server, or another surface", async () => {
    const lite = fakeDollar();
    await hooks["session.start"]!(lite.$, {}, async () => undefined);
    await settle();
    const tree = (await hooks["ui.render:Pane"]!(lite.$, pane(), async () => THEIRS)) as Tree;
    expect(textOf(tree)).toEqual(["index this repo for Lens: repowise init --no-prose --yes"]);
    const desktop = (await hooks["ui.render:Pane"]!(lite.$, pane("lens", "desktop"), async () => THEIRS)) as Tree;
    expect(textOf(desktop)).toEqual([MAP_COPY.desktop]);
  });

  it("before discovery lands, the pane says it is looking", async () => {
    const { $ } = fakeDollar();
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
    await hooks["command.run:lens"]!(d.$, { command: "lens" }, async () => ({}));
    await until(() => mapCalls === 1);
    await settle();
    expect(textOf(await render(d))).toEqual([MAP_COPY.failed]);
    await hooks["command.run:lens"]!(d.$, { command: "lens" }, async () => ({}));
    await until(() => mapCalls === 2);
  });

  it("a render that throws hands the site back", async () => {
    const d = fakeDollar();
    d.$.ui.resolve = () => {
      throw new Error("no table");
    };
    expect(await hooks["ui.render:Pane"]!(d.$, pane(), async () => THEIRS)).toBe(THEIRS);
    expect(d.calls.logs).toEqual(["lens: map render failed: Error: no table"]);
  });

  it("loaded mid-session: learns the cwd from the first call and observes from the next", async () => {
    const d = await started();
    await hooks["session.start"]!(d.$, {}, async () => undefined);
    await hooks["command.run:lens"]!(d.$, { command: "lens" }, async () => ({}));
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
    expect(d.calls.open).toEqual([{ id: "lens", title: "Lens", rows: 28 }]);
    await until(() => asked(d, "/health/map") === 1);
  });

  it("never touches a pane the person opened with /lens", async () => {
    const d = await start({ lens_pane_autoopen: true }, false);
    await hooks["command.run:lens"]!(d.$, { command: "lens" }, async () => ({}));
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
