/**
 * The mod's entry, and the only file that touches `$`. It binds `$` once into
 * a Host, registers every hook, feeds events to the model, and redraws.
 *
 * Lens never decides for Claude: the one `tool.check` hook approves only Lens's
 * own read-only lookups and hands every other call to the engine untouched.
 */

import { discover, readFreshness } from "./data/discovery";
import { isOwnReadOnlyCall, MCP_SERVER_KEY, mcpServerName } from "./data/mcp";
import type { Host } from "./host";
import type { ModApi, On } from "./mod-api";
import { fromTurnComplete } from "./model/events";
import { initialSession, reduce, type SessionAction, type SessionState } from "./model/session";
import { bandView } from "./views/band";
import { materialize } from "./views/elements";

const PROCESS_TIMEOUT_MS = 5_000;

let state: SessionState = initialSession;
let host: Host | null = null;
let refreshing: Promise<void> | null = null;

function makeHost($: ModApi): Host {
  // Once the server has connected the CLI is known to exist; a later connect
  // can fail while the session shuts its servers down, which is not "no CLI".
  let connected = false;
  return {
    pluginName: $.plugin.name,
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
      call: (tool, args) => $.mcp.call(mcpServerName($.plugin.name), tool, args),
      connect: async () => (connected ||= (await $.mcp.connect(MCP_SERVER_KEY)).isConnected),
    },
  };
}

function dispatch($: ModApi, action: SessionAction): void {
  const next = reduce(state, action);
  if (next === state) return;
  state = next;
  $.ui.invalidate("ui.render");
}

/** Re-reads mode and freshness in the background; overlapping requests fold into the one running. */
function refresh($: ModApi, h: Host): void {
  if (refreshing) return;
  refreshing = (async () => {
    try {
      const found = await discover(h);
      const indexed = found.repoRoot !== null && (found.mode === "full" || found.mode === "lite");
      const freshness = indexed && found.repoRoot !== null ? await readFreshness(h, found.repoRoot) : null;
      const action: SessionAction = { type: "discovered", mode: found.mode, freshness };
      if (found.liteReason !== undefined) action.liteReason = found.liteReason;
      dispatch($, action);
    } catch {
      // Keep the last good state; the next turn tries again.
    } finally {
      refreshing = null;
    }
  })();
}

export function register(on: On): void {
  on("session.start", async ($, e, next) => {
    try {
      state = initialSession;
      host = makeHost($);
      refresh($, host);
    } catch {
      // Lens stays quiet; the session is unaffected.
    }
    return next(e);
  });

  on("turn.complete", async ($, e, next) => {
    try {
      const action = fromTurnComplete(e);
      if (action !== null) {
        dispatch($, action);
        if (host !== null) refresh($, host);
      }
    } catch {
      // Observing only.
    }
    return next(e);
  });

  on("tool.check", async ($, e, next) => {
    try {
      if (isOwnReadOnlyCall(e, next.origin.plugin, $.plugin.name)) {
        return { decision: "allow", reason: "Repowise Lens: its own read-only lookup" };
      }
    } catch {
      // Fall through to the engine's own verdict.
    }
    return next(e);
  });

  on("ui.render", { component: "AbovePrompt" }, async ($, e, next) => {
    try {
      const tree = bandView(state, {
        columns: e.props.bodyColumns ?? 80,
        hasSurvey: e.props.hasSurvey === true,
      });
      if (tree !== null) return materialize(tree, $.ui.resolve(e));
    } catch {
      // Draw nothing rather than a broken band.
    }
    return next(e);
  });
}
