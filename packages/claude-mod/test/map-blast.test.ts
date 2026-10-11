// A wide edit through register(): Claude reads and edits django/conf/__init__.py,
// whose depth-1 blast radius (recorded from a re-indexed Django copy, paths only)
// names 248 importers. Every pane drawn after it must be a Raster the engine
// accepts, at a docked 110 and 180 columns, and a pane must never go blank.
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Hook, ModApi, On } from "../src/mod-api";
import { decode } from "./django";
import { fixture, flatten, textOf, type Tree } from "./fake-host";

type Hooks = Record<string, Hook<any>>;

async function load(): Promise<Hooks> {
  vi.resetModules();
  const { register } = await import("../src/register");
  const hooks: Hooks = {};
  const on = ((event: string, a: unknown, b?: unknown) => {
    const m = b === undefined ? null : (a as { component?: string; command?: string; element?: string });
    hooks[m === null ? event : `${event}:${m.component ?? m.command ?? m.element}`] = (b ?? a) as Hook<any>;
  }) as On;
  register(on);
  return hooks;
}

const ROOT = "C:\\work\\django";
const SHA = "e78991410b78391bc07a4b5010b273dfec814ff6";
const CONF = `${ROOT}\\django\\conf\\__init__.py`;
const json = (body: unknown) => ({ status: 200, ok: true, headers: {}, text: JSON.stringify(body) });
const FILES: Record<string, string> = {
  "C:/work/django/.repowise/state.json": JSON.stringify({ last_sync_commit: SHA }),
  "C:/work/django/.repowise/serve.lock.json": fixture("locks/valid.json"),
};

function fullDollar() {
  const calls = { http: [] as string[], logs: [] as string[] };
  const $: ModApi = {
    session: { cwd: async () => ROOT },
    fs: { exists: async (p) => p.replace(/\\/g, "/") in FILES, read: async (p) => FILES[p.replace(/\\/g, "/")] ?? Promise.reject(new Error("ENOENT")) },
    process: {
      run: async (argv) => {
        if (argv[0] === "tasklist") return { exitCode: 0, stdout: '"python.exe","4242","Console"\r\n', stderr: "" };
        return { exitCode: 0, stdout: argv.includes("HEAD") ? `${SHA}\n` : "true\n", stderr: "" };
      },
    },
    http: {
      fetch: async (url) => {
        calls.http.push(url);
        if (url.endsWith("/health")) return json({ status: "healthy" });
        if (url.endsWith("/api/repos")) return json([{ id: "dj", name: "django", local_path: "c:/work/django/", updated_at: null }]);
        if (url.includes("/health/map")) return { status: 200, ok: true, headers: {}, text: fixture("django-health-map.json") };
        if (url.endsWith("/blast-radius")) return { status: 200, ok: true, headers: {}, text: fixture("django-blast-radius-conf-depth1.json") };
        return { status: 404, ok: false, headers: {}, text: "{}" };
      },
    },
    mcp: { connect: async () => ({ isConnected: false }), call: async () => Promise.reject(new Error("no MCP")) },
    prompt: { submit: async () => ({}) },
    ui: {
      invalidate: () => undefined,
      log: (text) => {
        calls.logs.push(text);
      },
      resolve: () => Object.fromEntries(["Box", "Text", "Button", "Input", "Markdown", "Raster", "Svg"].map((el) => [el, (props: Record<string, unknown>) => ({ el, props })])),
      open: async () => ({ isPlaced: true }),
      close: async () => undefined,
      blit: async () => ({}),
    },
    command: { register: async () => ({}) },
    config: { list: async () => [{ key: "theme", value: "dark" }] },
    settings: { read: async () => ({ prefersReducedMotion: true }) },
  };
  return { $, calls };
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(check: () => boolean): Promise<void> {
  for (let i = 0; i < 300 && !check(); i++) await wait(5);
  expect(check()).toBe(true);
}

/** One width-1 BMP code point: printable, not a surrogate, not East Asian wide. */
const ONE_CELL = /^[^\u0000-\u001f\u007f-\u009f\ud800-\udfff\u1100-\u115f\u2e80-\ua4cf\uac00-\ud7a3\uf900-\ufaff\ufe30-\ufe4f\uff00-\uff60\uffe0-\uffe6]$/;

/** What the engine checks of a Raster: its size, its cells' count, each glyph one cell, at most 1,024 color pairs. */
function engineProblems(raster: { props: Record<string, unknown> }): string[] {
  const { columns, rows, cells } = raster.props as { columns: number; rows: number; cells: string };
  const words = decode(cells);
  const problems: string[] = [];
  if (columns > 512 || rows > 256) problems.push(`size ${columns}x${rows}`);
  if (words.length !== columns * rows * 3) problems.push(`${words.length} words for ${columns}x${rows}`);
  const pairs = new Set<string>();
  for (let i = 0; i < words.length; i += 3) {
    const ch = String.fromCodePoint(words[i]!);
    if (words[i]! > 0xffff || !ONE_CELL.test(ch)) problems.push(`cell ${i / 3}: U+${words[i]!.toString(16)}`);
    pairs.add(`${words[i + 1]}:${words[i + 2]}`);
  }
  if (pairs.size > 1024) problems.push(`${pairs.size} color pairs`);
  return problems.slice(0, 5);
}

let hooks: Hooks;
beforeEach(async () => {
  hooks = await load();
});

describe("a wide edit: django/conf/__init__.py and its 248 importers", () => {
  for (const [columns, rows] of [
    [110, 24],
    [180, 48],
  ] as const) {
    it(`draws an engine-valid map at ${columns} columns, ${rows} rows, before and after the importers land`, async () => {
      const d = fullDollar();
      await hooks["session.start"]!(d.$, { cwd: ROOT }, async () => undefined);
      await until(() => d.calls.http.some((u) => u.endsWith("/api/repos")));
      await wait(10);
      await hooks["command.run:lens"]!(d.$, { command: "lens", args: "map" }, async () => ({}));
      await until(() => d.calls.http.some((u) => u.includes("/health/map")));
      const PANE = { surface: "terminal", requestId: "lens", props: { bodyColumns: columns, placement: "dock", scroll: { bodyRows: rows } } };
      const render = async () => (await hooks["ui.render:Pane"]!(d.$, PANE, async () => ({ el: "theirs" }))) as Tree;
      const raster = async () => {
        let tree = await render();
        for (let i = 0; i < 100 && !flatten(tree).some((n) => n.el === "Raster"); i++) {
          await wait(5);
          tree = await render();
        }
        return { tree, raster: flatten(tree).find((n) => n.el === "Raster") };
      };
      await hooks["tool.call"]!(d.$, { tool: "Read", tool_use_id: "toolu_r", file_path: CONF }, async () => ({ result: {} }));
      expect(engineProblems((await raster()).raster!)).toEqual([]);
      await hooks["tool.call"]!(d.$, { tool: "Edit", tool_use_id: "toolu_e", file_path: CONF, old_string: "a", new_string: "# x\na" }, async () => ({ result: {} }));
      await until(() => d.calls.http.some((u) => u.endsWith("/blast-radius")));
      await wait(20);
      const after = await raster();
      expect(after.raster).toBeDefined();
      expect(engineProblems(after.raster!)).toEqual([]);
      expect(textOf(after.tree).some((t) => /^248 importers: /.test(t))).toBe(true);
      expect(d.calls.logs.filter((l) => /render failed|draw|map key/.test(l))).toEqual([]);
    });
  }
});

/** register() with one module swapped for a version whose `name` throws. */
async function loadThrowing(module: string, name: string): Promise<Hooks> {
  vi.resetModules();
  vi.doMock(module, async (real) => ({
    ...(await real<Record<string, unknown>>()),
    [name]: () => {
      throw new Error("boom");
    },
  }));
  const { register } = await import("../src/register");
  const own: Hooks = {};
  register(((event: string, a: unknown, b?: unknown) => {
    const m = b === undefined ? null : (a as { component?: string; command?: string; element?: string });
    own[m === null ? event : `${event}:${m.component ?? m.command ?? m.element}`] = (b ?? a) as Hook<any>;
  }) as On);
  vi.doUnmock(module);
  return own;
}

const DOCKED = { surface: "terminal", requestId: "lens", props: { bodyColumns: 110, placement: "dock", scroll: { bodyRows: 24 } } };
const FAILED = "Lens could not draw this tab; details in the debug log";

describe("a tab body that throws", () => {
  async function shown(own: Hooks, tab: string) {
    const d = fullDollar();
    await own["session.start"]!(d.$, { cwd: ROOT }, async () => undefined);
    await until(() => d.calls.http.some((u) => u.endsWith("/api/repos")));
    await wait(10);
    await own["command.run:lens"]!(d.$, { command: "lens", args: tab }, async () => ({}));
    await wait(20);
    const tree = (await own["ui.render:Pane"]!(d.$, DOCKED, async () => ({ el: "theirs" }))) as Tree;
    return { d, tree };
  }

  it("Recap: keeps the tab bar, says it could not draw, and logs why", async () => {
    const { d, tree } = await shown(await loadThrowing("../src/views/pane", "recapView"), "recap");
    expect(tree).not.toEqual({ el: "theirs" });
    expect(flatten(tree).some((n) => n.el === "Button")).toBe(true);
    expect(textOf(tree)).toContain(FAILED);
    expect(d.calls.logs.some((l) => /recap tab render failed: Error: boom/.test(l))).toBe(true);
  });

  it("Map: the same, for a map that throws while it draws", async () => {
    const { d, tree } = await shown(await loadThrowing("../src/views/mapPane", "mapPaneView"), "map");
    expect(flatten(tree).some((n) => n.el === "Button")).toBe(true);
    expect(textOf(tree)).toContain(FAILED);
    expect(d.calls.logs.some((l) => /map tab render failed: Error: boom/.test(l))).toBe(true);
  });

  it("a map key that throws is logged and the pane keeps drawing", async () => {
    const own = await loadThrowing("../src/model/inspect", "step");
    const { d, tree } = await shown(own, "map");
    await own["tool.call"]!(d.$, { tool: "Read", tool_use_id: "toolu_r", file_path: CONF }, async () => ({ result: {} }));
    const next = flatten((await own["ui.render:Pane"]!(d.$, DOCKED, async () => ({ el: "theirs" }))) as Tree).find((n) => n.props?.key === "lens-map-next");
    expect(flatten(tree).some((n) => n.el === "Raster")).toBe(true);
    (next!.props.onPress as () => void)();
    expect(d.calls.logs.some((l) => /map key failed: Error: boom/.test(l))).toBe(true);
    const after = (await own["ui.render:Pane"]!(d.$, DOCKED, async () => ({ el: "theirs" }))) as Tree;
    expect(flatten(after).some((n) => n.el === "Raster")).toBe(true);
  });
});
