/**
 * The mod's entry, and the only file that touches `$`. Each hook builds a Host
 * from its own `$`, feeds events to the model, and redraws. Lens only
 * observes: every hook passes its event on unchanged.
 */

import { discover, readFreshness } from "./data/discovery";
import type { Host } from "./host";
import type { ModApi, On } from "./mod-api";
import { fromTurnComplete } from "./model/events";
import { initialSession, reduce, type SessionAction, type SessionState } from "./model/session";
import { bandView } from "./views/band";
import { materialize } from "./views/elements";

const PROCESS_TIMEOUT_MS = 5_000;
/** The server's key in the plugin's .mcp.json. */
const MCP_SERVER_KEY = "repowise";

let state: SessionState = initialSession;
/** Bumped per session, so a refresh started in an earlier one never lands in this one. */
let generation = 0;
let running = false;
/** A refresh was asked for while one ran: run once more after it. */
let dirty = false;
let latest: Bound | null = null;
/**
 * Once the server has connected the CLI is known to exist; a later connect
 * can fail while the session shuts its servers down, which is not "no CLI".
 */
let mcpConnected = false;

/** What a background refresh needs from a hook's `$`, as closures (the engine forbids keeping `$` itself). */
interface Bound {
  host: Host;
  redraw(): void;
}

// Built from the calling hook's own `$`. When MCP tool calls arrive, await
// them inside a hook: a call from a detached promise skips this plugin's own
// `tool.check`, so the engine refuses it.
function bind($: ModApi): Bound {
  const host: Host = {
    session: { cwd: () => $.session.cwd() },
    fs: {
      read: (path) => $.fs.read(path),
      exists: (path) => $.fs.exists(path),
    },
    process: {
      run: (argv, cwd) => $.process.run(argv, { cwd, timeoutMs: PROCESS_TIMEOUT_MS }),
    },
    http: (url, init) => {
      const request: { method?: string; headers?: Record<string, string>; body?: string } = {};
      if (init.method !== undefined) request.method = init.method;
      if (init.headers !== undefined) request.headers = init.headers;
      if (init.body !== undefined) request.body = init.body;
      return $.http.fetch(url, request);
    },
    mcp: {
      connect: async () => (mcpConnected ||= (await $.mcp.connect(MCP_SERVER_KEY)).isConnected),
    },
  };
  return { host, redraw: () => $.ui.invalidate("ui.render") };
}

function dispatch(b: Bound, action: SessionAction): void {
  const next = reduce(state, action);
  if (next === state) return;
  state = next;
  b.redraw();
}

async function refreshOnce(b: Bound, gen: number): Promise<void> {
  const h = b.host;
  const found = await discover(h);
  const indexed = found.mode === "full" || found.mode === "lite";
  const freshness = indexed && found.repoRoot !== null ? await readFreshness(h, found.repoRoot) : null;
  if (gen !== generation) return;
  const action: SessionAction = { type: "discovered", mode: found.mode, freshness };
  if (found.liteReason !== undefined) action.liteReason = found.liteReason;
  dispatch(b, action);
}

/** Re-reads mode and freshness in the background, one run at a time. */
function refresh(b: Bound): void {
  latest = b;
  if (running) {
    dirty = true;
    return;
  }
  running = true;
  dirty = false;
  refreshOnce(b, generation)
    .catch(() => {
      // Keep the last good state; the next turn tries again.
    })
    .finally(() => {
      running = false;
      if (dirty && latest !== null) refresh(latest);
    });
}

export function register(on: On): void {
  on("session.start", async ($, e, next) => {
    try {
      generation++;
      state = initialSession;
      refresh(bind($));
    } catch {
      // Lens stays quiet; the session is unaffected.
    }
    return next(e);
  });

  // Also the first refresh when the module loaded after the session started.
  on("turn.complete", async ($, e, next) => {
    try {
      const action = fromTurnComplete(e);
      if (action !== null) {
        const b = bind($);
        dispatch(b, action);
        refresh(b);
      }
    } catch {
      // Observing only.
    }
    return next(e);
  });

  on("ui.render", { component: "AbovePrompt" }, async ($, e, next) => {
    // The band is shared: a mod's tree replaces what the mods after it draw
    // (docs: plugins/mods/interface, "Band above the prompt"), so Lens puts
    // its rows above the tree `next(e)` returns instead of dropping it.
    const theirs = await next(e);
    try {
      const tree = bandView(state, {
        columns: e.props.bodyColumns ?? 80,
        hasSurvey: e.props.hasSurvey === true,
      });
      if (tree === null) return theirs;
      const elements = $.ui.resolve(e);
      const ours = materialize(tree, elements);
      const box = elements["Box"];
      if (theirs === null || theirs === undefined || box === undefined) return ours;
      return box({ flexDirection: "column", children: [ours, theirs] });
    } catch {
      return theirs;
    }
  });
}
