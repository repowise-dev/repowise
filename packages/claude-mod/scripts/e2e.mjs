// End-to-end check of Lens's data layer against a real local server. Local
// only: it needs the repowise CLI on PATH and an indexed repo.
//
//   npm run test:e2e -w @repowise-dev/claude-mod -- <indexed-repo> [options]
//
//   --file <rel>   the file whose importers are checked (default: the largest
//                  drawn file with importers, among the 15 largest)
//   --reindex      re-index the temp copy first (`repowise init`, no prose)
//   --map-only     skip savings and the change review
//   --json <path>  also write the result as JSON
//
// It copies the repo to a temp dir outside any workspace (a shared git clone
// plus a copy of `.repowise`), points the copy's index at the copy, starts
// `repowise serve --no-ui` there, and drives Lens's own modules (bundled from
// src with esbuild) through a Host whose http is real fetch and whose fs and
// process are real Node. Every count Lens shows is checked against the
// server's own answer. The server, the MCP child and the copy are always
// removed, pass or fail.

import { execFile, spawn, spawnSync } from "node:child_process";
import { appendFileSync, cpSync, createWriteStream, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const pkg = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const WIN = process.platform === "win32";
const WIDTHS = [60, 100, 180];
const DOCK_ROWS = 40;
const CANDIDATES = 15;
const SERVER_START_MS = 180_000;

// ---------------------------------------------------------------- arguments

const argv = process.argv.slice(2);
const flag = (name) => argv.includes(name);
const option = (name) => {
  const i = argv.indexOf(name);
  return i >= 0 ? argv[i + 1] : undefined;
};
const source = argv.find((a, i) => !a.startsWith("--") && !["--file", "--json"].includes(argv[i - 1]));
if (!source) {
  console.error("usage: e2e.mjs <indexed-repo> [--file <rel>] [--reindex] [--map-only] [--json <path>]");
  process.exit(2);
}
const src = resolve(source);
if (!existsSync(join(src, ".repowise", "wiki.db"))) {
  console.error(`${src} has no .repowise/wiki.db`);
  process.exit(2);
}

// ---------------------------------------------------------------- results

const checks = [];
const timings = {};
const facts = { repo: basename(src) };

function check(name, ok, detail = "") {
  checks.push({ name, ok: Boolean(ok), detail });
  console.log(`${ok ? "ok  " : "FAIL"} ${name}${detail ? `: ${detail}` : ""}`);
}

async function timed(label, work) {
  const start = performance.now();
  try {
    return await work();
  } finally {
    timings[label] = Math.round(performance.now() - start);
  }
}

const norm = (p) => (WIN ? p.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase() : p.replace(/\/+$/, ""));

// ---------------------------------------------------------------- processes

function run(cmd, args, cwd, timeoutMs = 600_000) {
  const r = spawnSync(cmd, args, { cwd, encoding: "utf8", timeout: timeoutMs, env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
  if (r.status !== 0) throw new Error(`${cmd} ${args.join(" ")} failed (${r.status}): ${(r.stderr || r.stdout || "").slice(-800)}`);
  return r.stdout;
}

/** Stops a child and everything it started: the Windows CLI is a launcher over python. */
function killTree(pid) {
  if (!pid) return;
  if (WIN) spawnSync("taskkill", ["/PID", String(pid), "/T", "/F"], { stdio: "ignore" });
  else {
    try {
      process.kill(pid, "SIGTERM");
    } catch {
      // already gone
    }
  }
}

function freePort() {
  return new Promise((ok, fail) => {
    const s = createServer();
    s.once("error", fail);
    s.listen(0, "127.0.0.1", () => {
      const { port } = s.address();
      s.close(() => ok(port));
    });
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------- the copy

/** A shared clone at the source's HEAD (objects borrowed, nothing written back) plus its index. */
function copyRepo(dest) {
  if (existsSync(join(src, ".git"))) {
    const head = run("git", ["-C", src, "rev-parse", "HEAD"], src).trim();
    run("git", ["clone", "--shared", "--no-checkout", "-q", src, dest], dirname(dest));
    run("git", ["-C", dest, "checkout", "-q", "--detach", head], dest);
    cpSync(join(src, ".repowise"), join(dest, ".repowise"), {
      recursive: true,
      filter: (p) => !p.endsWith("serve.lock.json") && !p.endsWith(".update.lock"),
    });
  } else {
    cpSync(src, dest, { recursive: true, filter: (p) => !p.endsWith("serve.lock.json") });
  }
}

/** Points the copy's index row at the copy, so the server lists it under the copy's path. */
function patchLocalPath(dest) {
  const db = new DatabaseSync(join(dest, ".repowise", "wiki.db"));
  try {
    const rows = db.prepare("SELECT id, local_path FROM repositories").all();
    const own = rows.filter((r) => rows.length === 1 || norm(r.local_path ?? "") === norm(src) || norm(r.local_path ?? "") === norm(dest));
    if (own.length !== 1) throw new Error(`expected one repositories row for ${src}, found ${own.length} of ${rows.length}`);
    db.prepare("UPDATE repositories SET local_path = ? WHERE id = ?").run(dest, own[0].id);
    return own[0].id;
  } finally {
    db.close();
  }
}

function indexVersion(dir) {
  try {
    return JSON.parse(readFileSync(join(dir, ".repowise", "state.json"), "utf8")).written_by_version ?? "unstamped";
  } catch {
    return "unknown";
  }
}

// ---------------------------------------------------------------- the server

async function startServer(dest, logPath) {
  const port = await freePort();
  const log = createWriteStream(logPath);
  const child = spawn("repowise", ["serve", "--no-ui", "--host", "127.0.0.1", "--port", String(port)], {
    cwd: dest,
    env: { ...process.env, PYTHONIOENCODING: "utf-8" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  child.stdout.pipe(log);
  child.stderr.pipe(log);
  let exited = null;
  child.once("exit", (code) => (exited = code));
  const url = `http://127.0.0.1:${port}`;
  const deadline = Date.now() + SERVER_START_MS;
  while (Date.now() < deadline) {
    if (exited !== null) throw new Error(`repowise serve exited (${exited}); see ${logPath}`);
    const up = await fetch(`${url}/health`).then((r) => r.ok).catch(() => false);
    if (up && existsSync(join(dest, ".repowise", "serve.lock.json"))) return { child, url };
    await sleep(500);
  }
  throw new Error(`repowise serve did not answer /health in ${SERVER_START_MS / 1000} s; see ${logPath}`);
}

async function getJson(url, init) {
  const r = await fetch(url, init);
  if (!r.ok) throw new Error(`${init?.method ?? "GET"} ${url}: ${r.status} ${(await r.text()).slice(0, 300)}`);
  return r.json();
}

// ---------------------------------------------------------------- MCP over stdio

/** A minimal MCP stdio client for `repowise mcp`: newline-delimited JSON-RPC. */
function mcpClient(dest) {
  let child = null;
  let ready = null;
  let nextId = 1;
  const pending = new Map();
  const send = (msg) => child.stdin.write(`${JSON.stringify(msg)}\n`);
  const request = (method, params) =>
    new Promise((ok, fail) => {
      const id = nextId++;
      pending.set(id, { ok, fail });
      send({ jsonrpc: "2.0", id, method, params });
    });
  function start() {
    child = spawn("repowise", ["mcp", dest, "--no-workspace"], {
      cwd: dest,
      env: { ...process.env, PYTHONIOENCODING: "utf-8" },
      stdio: ["pipe", "pipe", "ignore"],
    });
    let buf = "";
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      buf += chunk;
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line.startsWith("{")) continue;
        const msg = JSON.parse(line);
        const waiter = pending.get(msg.id);
        if (!waiter) continue;
        pending.delete(msg.id);
        if (msg.error) waiter.fail(new Error(msg.error.message));
        else waiter.ok(msg.result);
      }
    });
    child.once("exit", (code) => {
      for (const w of pending.values()) w.fail(new Error(`repowise mcp exited (${code})`));
      pending.clear();
    });
    return request("initialize", {
      protocolVersion: "2025-06-18",
      capabilities: {},
      clientInfo: { name: "lens-e2e", version: "0" },
    }).then(() => send({ jsonrpc: "2.0", method: "notifications/initialized" }));
  }
  const connect = () => (ready ??= start().then(() => true));
  return {
    connect,
    pid: () => child?.pid,
    call: async (tool, args) => {
      await connect();
      const r = await request("tools/call", { name: tool, arguments: args });
      return { content: r.content ?? [], isError: r.isError === true };
    },
  };
}

// ---------------------------------------------------------------- Lens, bundled

async function loadLens() {
  const out = await build({
    stdin: {
      contents: [
        'import "./test/setup";',
        'export { discover, readFreshness, isWindowsPath } from "./src/data/discovery";',
        'export { callTool, warmMcp } from "./src/data/mcp";',
        'export { LensMap } from "./src/map-controller";',
        'export { layoutMap } from "./src/views/map";',
        'export { NO_LIT, resolveLit } from "./src/views/overlay";',
        'export { NO_STORY, withImporters } from "./src/model/story";',
        'export { legendRows } from "./src/views/mapPane";',
        'export { fromToolCall, absolutePath, savingsTotals, savingsSince } from "./src/model/events";',
        'export { reduce, initialSession } from "./src/model/session";',
        'export { testsToRun } from "./src/model/review";',
        'export { bandView, bandRows } from "./src/views/band";',
        'export { savingsLine } from "./src/views/copy";',
        'export { reviewText } from "./src/views/review";',
        'export { getSavings } from "@repowise-dev/api-client/costs";',
      ].join("\n"),
      resolveDir: pkg,
      loader: "ts",
    },
    bundle: true,
    format: "esm",
    platform: "node",
    mainFields: ["module", "main"],
    write: false,
    logLevel: "warning",
  });
  return import(`data:text/javascript;base64,${Buffer.from(out.outputFiles[0].text).toString("base64")}`);
}

function nodeHost(cwd, mcp) {
  return {
    session: { cwd: async () => cwd },
    fs: {
      read: async (p) => readFileSync(p, "utf8"),
      exists: async (p) => existsSync(p),
    },
    process: {
      run: (args, runCwd) =>
        new Promise((ok) => {
          execFile(args[0], args.slice(1), { cwd: runCwd, encoding: "utf8", timeout: 10_000 }, (err, stdout, stderr) =>
            ok({ exitCode: err ? (typeof err.code === "number" ? err.code : 1) : 0, stdout, stderr }),
          );
        }),
    },
    http: async (url, init) => {
      const r = await fetch(url, { method: init.method, headers: init.headers, body: init.body });
      const headers = {};
      r.headers.forEach((v, k) => (headers[k] = v));
      return { status: r.status, ok: r.ok, headers, text: await r.text() };
    },
    mcp: {
      connect: () => mcp.connect(),
      server: () => mcp.connect().then(() => "plugin:repowise:repowise"),
      call: (_server, tool, args) => mcp.call(tool, args),
    },
  };
}

// ---------------------------------------------------------------- tree helpers

function textsOf(node, out = []) {
  if (node.type === "Text") out.push(node.children.join(""));
  else if (node.type === "Box") for (const c of node.children) textsOf(c, out);
  return out;
}

function rasterOf(node) {
  if (node.type === "Raster") return node;
  if (node.type === "Box") for (const c of node.children) {
    const r = rasterOf(c);
    if (r) return r;
  }
  return null;
}

const num = (s) => Number(s.replace(/,/g, ""));

/**
 * The map's scope line as numbers: total files, the largest the server sent
 * (when capped), and the files drawn (null when drawn as folders).
 */
function scopeOf(all) {
  const patterns = [
    [/([\d,]+) largest of ([\d,]+) files; drawn as folders at this size/, (m) => ({ shown: num(m[1]), total: num(m[2]), drawn: null })],
    [/([\d,]+) of the ([\d,]+) largest \(of ([\d,]+) files\) drawn at this size/, (m) => ({ drawn: num(m[1]), shown: num(m[2]), total: num(m[3]) })],
    [/([\d,]+) largest of ([\d,]+) files drawn/, (m) => ({ drawn: num(m[1]), shown: num(m[1]), total: num(m[2]) })],
    [/([\d,]+) files?, drawn as folders at this size/, (m) => ({ drawn: null, shown: num(m[1]), total: num(m[1]) })],
    [/([\d,]+) of ([\d,]+) files? drawn at this size/, (m) => ({ drawn: num(m[1]), shown: num(m[2]), total: num(m[2]) })],
    [/([\d,]+) files?, all drawn/, (m) => ({ drawn: num(m[1]), shown: num(m[1]), total: num(m[1]) })],
  ];
  for (const [re, read] of patterns) {
    const m = all.match(re);
    if (m) return read(m);
  }
  return null;
}

/** Distinct files owning at least one pixel, painted independently of the layout's own `drawn`. */
function pixelOwners(layout) {
  const grid = new Int32Array(layout.width * layout.height).fill(-1);
  let overlaps = 0;
  layout.files.forEach((f, i) => {
    for (let y = Math.max(0, f.py0); y < Math.min(layout.height, f.py1); y++)
      for (let x = Math.max(0, f.px0); x < Math.min(layout.width, f.px1); x++) {
        if (grid[y * layout.width + x] !== -1) overlaps++;
        grid[y * layout.width + x] = i;
      }
  });
  const owners = new Set();
  for (const v of grid) if (v !== -1) owners.add(v);
  return { owners: owners.size, overlaps };
}

// ---------------------------------------------------------------- the run

/** A temp dir outside any workspace: under a workspace root, `repowise serve` serves the workspace's DB, not the copy's. */
function tempWork() {
  const work = mkdtempSync(join(tmpdir(), "lens-e2e-"));
  const inside = [];
  for (let d = work; d !== dirname(d); d = dirname(d)) if (existsSync(join(d, ".repowise-workspace.yaml"))) inside.push(d);
  if (inside.length === 0) return work;
  rmSync(work, { recursive: true, force: true });
  throw new Error(`temp dir ${work} sits inside the workspace at ${inside[0]}; set TMP elsewhere`);
}

/** The copy, re-indexed when asked, pointed at itself and served; `live.server` is set as soon as it runs. */
async function prepare(dest, work, live) {
  await timed("copy", async () => copyRepo(dest));
  facts.indexVersion = indexVersion(src);
  if (flag("--reindex")) {
    await timed("reindex", async () =>
      run("repowise", ["init", ".", "--no-prose", "-y", "--no-editor-setup", "--no-hook", "--embedder", "mock", "--no-workspace"], dest, 1_800_000),
    );
    facts.indexVersion = `${facts.indexVersion} -> ${indexVersion(dest)} (temp copy)`;
  }
  const repoId = patchLocalPath(dest);
  live.server = await timed("server start", () => startServer(dest, join(work, "serve.log")));
  return repoId;
}

/** Discovery: the lock, the pid, /health, and this repo in the server's list; null unless full. */
async function discoverRepo(L, host, repoId, dest) {
  const found = await timed("discovery", () => L.discover(host));
  facts.mode = found.mode;
  check("discovery mode is full", found.mode === "full", `${found.mode}${found.liteReason ? ` (${found.liteReason})` : ""}`);
  check("discovered repo id matches the local_path row", found.repo?.id === repoId, `${found.repo?.id} vs ${repoId}`);
  check("discovered root is the copy", norm(found.repoRoot ?? "") === norm(dest), String(found.repoRoot));
  if (found.mode !== "full" || found.repo === undefined) return null;
  await timed("freshness", () => L.readFreshness(host, found.repoRoot));
  return found;
}

/** The map as the pane draws it, with a turn the run sets once it edits a file. */
function mapSession(L, found, api) {
  const root = found.repoRoot;
  const repo = { id: found.repo.id, root, updatedAt: found.repo.updatedAt, caseInsensitive: L.isWindowsPath(root) };
  const debug = [];
  const io = { redraw: () => {}, debug: (m) => debug.push(m), blit: async () => ({}), openPane: async () => ({ isPlaced: true }), closePane: async () => {} };
  const s = { L, api, root, repo, debug, io, turn: { lit: L.NO_LIT, story: L.NO_STORY } };
  s.map = new L.LensMap(false, undefined, false, () => s.turn);
  s.map.setReducedMotion(true);
  s.map.setRepo(io, repo);
  s.pane = (columns) => s.map.paneTree(io, { surface: "terminal", notice: "", bodyColumns: columns, placement: "dock", bodyRows: DOCK_ROWS, theme: "dark" });
  s.until = async (ready, label, ms = 60_000) => {
    const deadline = Date.now() + ms;
    while (!ready()) {
      if (Date.now() > deadline) throw new Error(`${label} not ready in ${ms} ms (${debug.join("; ")})`);
      await sleep(20);
    }
  };
  return s;
}

/** The health map drawn, and the feed as the server sends it. */
async function loadMap(s) {
  await timed("health map", async () => {
    await s.map.request(s.io);
    await s.until(() => rasterOf(s.pane(180)) !== null || s.debug.some((d) => d.startsWith("health map failed")), "health map", 60_000);
  });
  const feed = await getJson(`${s.api}/api/repos/${s.repo.id}/health/map?cap=4000`);
  facts.files = feed.repository_total;
  check("health map drew", rasterOf(s.pane(180)) !== null, s.debug.join("; "));
  check("feed shown equals its files", feed.shown === feed.files.length, `${feed.shown} vs ${feed.files.length}`);
  return feed;
}

const layoutFor = (s, feed, raster) =>
  s.L.layoutMap(feed.files, { columns: raster.props.columns, rows: raster.props.rows, caseInsensitive: s.repo.caseInsensitive });

/** The scope line against the layout: folders exactly when dense, else the drawn count; the total; the cap. */
function checkScope({ columns, scope, layout, owners, feed }) {
  check(`${columns}: scope says folders exactly when dense`, (scope.drawn === null) === layout.dense, `dense ${layout.dense}, scope drawn ${scope.drawn}`);
  if (scope.drawn !== null) check(`${columns}: scope drawn equals pixel owners`, scope.drawn === owners, `scope ${scope.drawn}, owners ${owners}`);
  check(`${columns}: scope total equals feed repository_total`, scope.total === feed.repository_total, `scope ${scope.total}, feed ${feed.repository_total}`);
  if (feed.omitted.files > 0) check(`${columns}: capped scope names the largest the feed sent`, scope.shown === feed.files.length, `scope ${scope.shown}, feed ${feed.files.length}`);
}

/** One pane width: the raster's size, pixel ownership, rows that fit, and the scope line. */
async function checkWidth(s, feed, columns) {
  const tree = s.pane(columns);
  const raster = rasterOf(tree);
  if (!raster) return check(`map at ${columns} columns`, false, textsOf(tree).join(" | "));
  const layout = await timed(`layout ${columns}`, async () => layoutFor(s, feed, raster));
  const { owners, overlaps } = pixelOwners(layout);
  const cellsBytes = Buffer.from(raster.props.cells, "base64").length;
  check(`${columns}: raster is ${raster.props.columns}x${raster.props.rows} cells`, cellsBytes === raster.props.columns * raster.props.rows * 12, `${cellsBytes} bytes`);
  check(`${columns}: no pixel owned twice`, overlaps === 0, `${overlaps} overlaps`);
  check(`${columns}: layout drawn equals pixel owners`, layout.drawn.length === owners, `${layout.drawn.length} vs ${owners}`);
  const lines = textsOf(tree);
  check(`${columns}: legend rows fit`, lines.every((l) => l.length <= columns), lines.filter((l) => l.length > columns).join(" | "));
  const scope = scopeOf(lines.join("\n"));
  facts.drawn[columns] = scope === null ? "none" : `${scope.drawn ?? `${owners} as folders`}/${scope.total}`;
  check(`${columns}: scope line read`, scope !== null, lines.slice(-3).join(" | "));
  if (scope !== null) checkScope({ columns, scope, layout, owners, feed });
}

/** The server's direct importers of a file. */
async function direct(s, rel) {
  const r = await getJson(`${s.api}/api/repos/${s.repo.id}/blast-radius`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ changed_files: [rel], max_depth: 1 }),
  });
  return [...new Set(r.transitive_affected.map((t) => t.path))].filter((p) => p !== rel);
}

const isTest = (p) => /(^|\/)(tests?|__tests__|spec)\/|[._-](test|spec)\.[a-z]+$|(^|\/)test_[^/]*$/i.test(p);

/** The largest drawn files, source before tests (a test file rarely has importers). */
function candidates(s, feed) {
  const layout = s.L.layoutMap(feed.files, { columns: 180, rows: DOCK_ROWS - s.L.legendRows(180), caseInsensitive: s.repo.caseInsensitive });
  const order = (a, b) => isTest(a.path) - isTest(b.path) || b.nloc - a.nloc || (a.path < b.path ? -1 : 1);
  return layout.drawn
    .map((i) => layout.files[i])
    .sort(order)
    .slice(0, CANDIDATES)
    .map((f) => f.path);
}

/**
 * The file to edit: `--file`, else the first of the largest few that has
 * importers (else the largest). The blast radius answers for all its changed
 * files at once, so each candidate is asked on its own, all together.
 */
async function chooseFile(s, feed) {
  const named = option("--file");
  if (named) return { chosen: named, expected: await direct(s, named) };
  const paths = candidates(s, feed);
  const lists = await Promise.all(paths.map((p) => direct(s, p)));
  const at = Math.max(0, lists.findIndex((l) => l.length > 0));
  return { chosen: paths[at], expected: lists[at] };
}

/** Claude edits the chosen file: the map's turn lights it, and its importers are asked for. */
async function editChosen(s, chosen) {
  const { L, root, repo } = s;
  const raw = WIN ? `${root}\\${chosen.replace(/\//g, "\\")}` : `${root}/${chosen}`;
  const ctx = { cwd: root, isWindows: repo.caseInsensitive };
  const action = L.fromToolCall({ tool: "Edit", tool_use_id: "toolu_e2e_edit", file_path: raw }, {}, ctx);
  const name = chosen.slice(chosen.lastIndexOf("/") + 1);
  s.turn = {
    lit: { ...L.NO_LIT, reads: [chosen], edits: [chosen], edit: chosen, current: chosen },
    story: { searched: [], opened: [{ name, namedBy: null }], edited: [{ name, lines: null }] },
  };
  await timed("blast radius", async () => {
    s.map.observe(s.io, action, L.absolutePath(raw, ctx));
    await s.until(() => s.map.trail.callers?.status !== "loading", "importers", 60_000);
  });
  return s.map.trail.callers;
}

/** At one width: the REACHES count, every importer on the map marked, and only those. */
function checkImportersAt(s, feed, expected, columns) {
  const tree = s.pane(columns);
  const raster = rasterOf(tree);
  if (!raster) return;
  const layout = layoutFor(s, feed, raster);
  const resolved = s.L.resolveLit(layout, s.L.withImporters(s.turn.lit, (p) => s.map.importersOf(p)).lit);
  // The story strip's REACHES row: `12 importers: a.py (opened), b.py, +9`.
  const line = textsOf(tree).find((l) => /^[\d,]+ importers?: /.test(l)) ?? "";
  const said = line.match(/^([\d,]+) importers?: /);
  const n = said ? num(said[1]) : expected.length === 0 ? 0 : NaN;
  check(`${columns}: REACHES importer count equals the API`, n === expected.length, `"${line}", api ${expected.length}`);
  // Every importer the feed has (so the map draws it) is marked; a tiny file still lights a cell.
  const key = (p) => (layout.caseInsensitive ? p.toLowerCase() : p);
  const onMap = expected.filter((p) => layout.index.has(key(p)));
  check(`${columns}: every importer on the map is marked`, resolved.importers.length === onMap.length, `${resolved.importers.length} marked, ${onMap.length} on the map`);
  const drawnPaths = resolved.importers.map((i) => layout.files[i].path);
  check(`${columns}: every marked importer is in the API list`, drawnPaths.every((p) => expected.includes(p)));
  facts.importers[`drawn${columns}`] = resolved.importers.length;
}

/** Importers of the edited file against the server's direct list, then at each width. */
async function checkImporters(s, feed, chosen, expected) {
  facts.importers = { file: chosen, api: expected.length };
  const callers = await editChosen(s, chosen);
  check("importers loaded", callers?.status === "ready", `${callers?.status} ${s.debug.join("; ")}`);
  facts.importers.lens = callers?.status === "ready" ? callers.paths.length : null;
  if (callers?.status !== "ready") return;
  const got = [...callers.paths].sort();
  const want = [...expected].sort();
  check("importer paths equal the blast-radius direct list", JSON.stringify(got) === JSON.stringify(want), `${got.length} vs ${want.length}`);
  for (const columns of WIDTHS) checkImportersAt(s, feed, expected, columns);
}

/** Savings: Lens's baseline then a later read, and the raw ledger beside it. */
async function checkSavings(s, state) {
  const { L, api, repo } = s;
  const first = await timed("savings", () => L.getSavings(repo.id));
  const rawLedger = await getJson(`${api}/api/repos/${repo.id}/savings`);
  const totals = L.savingsTotals(first);
  check("savings tokens equal the ledger", totals.tokens === rawLedger.saved_input_tokens, `${totals.tokens} vs ${rawLedger.saved_input_tokens}`);
  check("savings dollars equal the ledger", totals.usd === rawLedger.priced_input_savings_usd, `${totals.usd} vs ${rawLedger.priced_input_savings_usd}`);
  const delta = L.savingsSince(L.savingsTotals(await L.getSavings(repo.id)), totals);
  const next = delta === null ? state : L.reduce(state, { type: "savings", delta });
  const row = next.savings === null ? null : L.savingsLine(next.savings, 180);
  const rows = L.bandRows(next, 180);
  const quiet = delta === null || delta.tokens === 0;
  check(
    "savings row absent or equal to the ledger delta",
    quiet ? next.savings === null && !rows.some((r) => r.includes("saved")) : rows.includes(row),
    delta === null ? "ledger shrank" : `${delta.tokens} tokens since the baseline`,
  );
  facts.savingsTokens = rawLedger.saved_input_tokens;
  return next;
}

/** The review card after an edit: renders, counts the one changed file, and its test total is the tool's. */
function checkEditedReview(L, risk, state) {
  const next = L.reduce(state, { type: "reviewed", risk });
  const card = L.reviewText(next.review.outcome);
  check("edited tree: review card renders", typeof card === "string" && card.length > 0, card?.split("\n")[0] ?? "null");
  check("edited tree: card counts the one changed file", typeof card === "string" && card.includes("1 changed file"), card ?? "null");
  const tests = L.testsToRun(risk);
  if (tests !== null && risk.impacted_tests?.total !== undefined) check("review test total equals the tool's", tests.total === risk.impacted_tests.total, `${tests.total}`);
  facts.review = { ms: timings["change risk"], overLensTimeout: timings["change risk"] > 20_000, percentile: risk.risk_percentile ?? null };
  return next;
}

/**
 * The change review through the MCP layer, headless over stdio: first the
 * clean copy (nothing to say), then after a one-line edit to the chosen file
 * (the copy only).
 */
async function checkReview(s, chosen, state) {
  const { L, host, dest } = s;
  L.warmMcp(host);
  await timed("mcp connect", () => host.mcp.server());
  await sleep(0);
  const review = () => L.callTool(host, "get_change_risk", {}, { timeoutMs: 120_000 });
  const clean = await timed("change risk (clean)", review);
  const quiet = L.reduce(state, { type: "reviewed", risk: clean });
  const quietBand = L.bandView(quiet, { columns: 180, hasSurvey: false });
  const noRow = quietBand === null || !textsOf(quietBand).some((l) => l.startsWith("review"));
  check("clean tree: no review card and no review row", L.reviewText(quiet.review.outcome) === null && noRow, L.reviewText(quiet.review.outcome) ?? "card null");
  appendFileSync(join(dest, ...chosen.split("/")), "\n");
  return checkEditedReview(L, await timed("change risk", review), state);
}

/** The band at each width, as the session would hold it. */
function checkBands(L, state) {
  for (const columns of WIDTHS) {
    try {
      const band = L.bandView(state, { columns, hasSurvey: false });
      const lines = band === null ? [] : textsOf(band);
      check(`${columns}: band rows fit`, lines.every((l) => l.length <= columns), lines.join(" | "));
    } catch (err) {
      check(`${columns}: band renders`, false, String(err));
    }
  }
}

/** Savings and the review, unless `--map-only`; a review that fails is a failed check, not a failed run. */
async function checkSession(s, chosen) {
  let state = s.L.reduce(s.L.initialSession, { type: "discovered", mode: "full", freshness: null, repoRoot: s.root });
  if (flag("--map-only")) return state;
  state = await checkSavings(s, state);
  try {
    return await checkReview(s, chosen, state);
  } catch (err) {
    check("change review ran", false, String(err));
    return state;
  }
}

/** Every phase in order, on a copy served from `live.server`. */
async function runAll(dest, work, mcp, live) {
  const repoId = await prepare(dest, work, live);
  const L = await loadLens();
  const host = nodeHost(dest, mcp);
  const found = await discoverRepo(L, host, repoId, dest);
  if (found === null) return;
  // The same object the map's turn source reads, so a turn set later reaches it.
  const s = Object.assign(mapSession(L, found, live.server.url), { host, dest });
  const feed = await loadMap(s);
  facts.drawn = {};
  for (const columns of WIDTHS) await checkWidth(s, feed, columns);
  const { chosen, expected } = await chooseFile(s, feed);
  await checkImporters(s, feed, chosen, expected);
  checkBands(L, await checkSession(s, chosen));
  s.map.dispose();
}

/** The server, the MCP child and the copy are always removed, pass or fail. */
async function cleanup(mcp, server, dest, work) {
  killTree(mcp.pid());
  if (server) {
    killTree(server.child.pid);
    // The lock names the python process itself; stop it too if the launcher left it.
    try {
      killTree(JSON.parse(readFileSync(join(dest, ".repowise", "serve.lock.json"), "utf8")).pid);
    } catch {
      // removed by the server on exit
    }
  }
  for (let i = 0; i < 10 && existsSync(work); i++) {
    try {
      rmSync(work, { recursive: true, force: true, maxRetries: 3 });
    } catch {
      await sleep(1000);
    }
  }
  if (existsSync(work)) console.error(`could not remove ${work}`);
}

async function main() {
  const work = tempWork();
  const dest = join(work, basename(src));
  const live = { server: null };
  const mcp = mcpClient(dest);
  try {
    await runAll(dest, work, mcp, live);
  } catch (err) {
    check("run completed", false, err instanceof Error ? err.message : String(err));
  } finally {
    await cleanup(mcp, live.server, dest, work);
  }
}

await main();
const failed = checks.filter((c) => !c.ok);
const result = { ...facts, timings, checks: checks.length, failed };
console.log(JSON.stringify(result));
const out = option("--json");
if (out) writeFileSync(out, JSON.stringify(result, null, 2));
process.exitCode = failed.length === 0 ? 0 : 1;
