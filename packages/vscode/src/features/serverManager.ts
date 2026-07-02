import * as vscode from "vscode";
import { spawn, type ChildProcess } from "node:child_process";
import { Commands, MIN_SERVER_VERSION } from "../constants";
import type { RepowiseContext } from "../core/context";
import { readLockfile } from "../core/lockfile";
import type { HealthResult } from "../core/api";

/**
 * How long to wait for a freshly spawned server to bind a port, write its
 * lockfile, and finish its synchronous startup (DB + embedder init) before we
 * declare the start a failure. The server does this work before accepting any
 * traffic, so the poll has to be patient.
 */
const START_TIMEOUT_MS = 60_000;
/** Initial poll interval; doubles up to the cap after each miss. */
const POLL_START_MS = 250;
const POLL_MAX_MS = 2_000;
/** Grace period before escalating a stop from a polite kill to a forced one. */
const KILL_GRACE_MS = 3_000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Compares two dotted numeric versions, ignoring any prerelease suffix
 * (`1.2.3-rc1` compares as `1.2.3`). Returns <0, 0, or >0. Deliberately tiny:
 * server versions are simple numeric triples, so a full semver dependency
 * would be overkill here.
 */
function compareVersions(a: string, b: string): number {
  const parse = (v: string): number[] =>
    (v.split("-")[0] ?? "")
      .split(".")
      .map((seg) => Number.parseInt(seg, 10) || 0);
  const pa = parse(a);
  const pb = parse(b);
  const len = Math.max(pa.length, pb.length);
  for (let i = 0; i < len; i++) {
    const da = pa[i] ?? 0;
    const db = pb[i] ?? 0;
    if (da !== db) return da < db ? -1 : 1;
  }
  return 0;
}

/**
 * Server lifecycle: discovering the running server from its lockfile, health
 * probing, and starting/stopping `repowise serve --no-ui`. The start/stop
 * commands are owned by this module. All discovery/probe work is deferred off
 * the activation hot path and driven reactively by lockfile-change events; the
 * module never polls while healthy.
 */
export function registerServerManager(ctx: RepowiseContext): vscode.Disposable {
  /** The server process we spawned, or null when we own none. */
  let owned: ChildProcess | null = null;
  /** True while a start attempt is in flight, to guard against double-spawn. */
  let starting = false;
  /** Show the version-too-old warning at most once per session. */
  let warnedVersionLow = false;
  /** Set on dispose so in-flight polls abandon their work. */
  let disposed = false;
  /** Subscription to lockfile-change events; recreated after a rescan. */
  let watcherSub: vscode.Disposable | null = null;

  const isConnected = (): boolean =>
    ctx.getExtensionState() === "ready" && ctx.api.getBaseUrl() !== null;

  /** Subscribe to lockfile-change events for the current repo root, once. */
  function ensureWatcher(): void {
    if (watcherSub) return;
    const watcher = ctx.events();
    if (!watcher) return;
    watcherSub = watcher.onDidChange((kind) => {
      // A server started or stopped out of band rewrites the lockfile.
      if (kind === "lockfileChanged") void discover({ rescan: false });
    });
  }

  /** Split spawned-process output into per-line log entries. */
  function logServeOutput(buf: Buffer, level: "debug" | "info"): void {
    const text = buf.toString().replace(/\r?\n$/, "");
    if (!text) return;
    for (const line of text.split(/\r?\n/)) {
      if (level === "debug") ctx.log.debug(`serve: ${line}`);
      else ctx.log.info(`serve: ${line}`);
    }
  }

  /**
   * Commits the shared API client to a verified-healthy server and settles the
   * editor into the ready/connected state, or flags a too-old server.
   */
  async function onConnected(
    repoRoot: string,
    url: string,
    health: HealthResult,
  ): Promise<void> {
    ctx.api.setBaseUrl(url);

    if (compareVersions(health.version, MIN_SERVER_VERSION) < 0) {
      // The server is up but predates the store shape this build can read.
      ctx.setExtensionState("server-down");
      ctx.setStatusBarState("version-low");
      if (!warnedVersionLow) {
        warnedVersionLow = true;
        void vscode.window.showWarningMessage(
          `The Repowise server (${health.version}) is older than this extension supports (${MIN_SERVER_VERSION}). Upgrade with: pip install --upgrade repowise`,
        );
      }
      return;
    }

    // A missing repo record is not fatal: staying connected still serves the
    // workspace, so log it and carry on. Set before the ready flip so state
    // listeners read a settled repo.
    const repo = await ctx.api.resolveRepo(repoRoot);
    if (!repo) {
      ctx.log.warn(`Could not resolve a repo id for ${repoRoot}; staying connected.`);
    }
    ctx.setRepo(repo);
    ctx.setExtensionState("ready");
    ctx.setStatusBarState("connected", { version: health.version, url });
  }

  /**
   * Deferred discovery: locate the server from its lockfile, probe it, and
   * settle into the matching state. Auto-starts only when the setting grants
   * standing consent. `rescan` re-detects workspace folders (and invalidates
   * the freshness watcher), so re-runs triggered by the watcher pass false.
   */
  async function discover(opts: { rescan: boolean }): Promise<void> {
    if (disposed || starting) return;

    if (!vscode.workspace.isTrusted) {
      // No filesystem or process work in an untrusted workspace.
      ctx.setStatusBarState("untrusted");
      return;
    }

    if (opts.rescan) {
      // Rescanning disposes the freshness watcher this root was bound to, so
      // drop the now-dead subscription and rebuild it below.
      watcherSub?.dispose();
      watcherSub = null;
      ctx.rescanWorkspace();
    }

    const ws = ctx.workspace;
    if (!ws.repoRoot || !ws.lockfilePath) {
      ctx.setExtensionState("no-index");
      ctx.setStatusBarState("no-index");
      return;
    }

    ensureWatcher();

    const lock = await readLockfile(ws.lockfilePath);
    if (lock) {
      const health = await ctx.api.checkHealth(lock.url);
      if (health) {
        await onConnected(ws.repoRoot, lock.url, health);
        return;
      }
    }

    // No lockfile, or a present-but-stale one whose server no longer answers.
    ctx.setExtensionState("server-down");
    ctx.setStatusBarState("server-down");

    // Standing consent only: "always" starts silently; "ask"/"never" wait for
    // the user to click the status bar or a welcome-view action.
    if (ctx.config.autoStart() === "always") {
      void startFlow({ silent: true });
    }
  }

  /**
   * Polls for the lockfile to appear and its server to answer /health, backing
   * off between attempts. Re-reads the lockfile every pass so a port the server
   * auto-bumped is picked up. Returns null on timeout, dispose, or abort.
   */
  async function pollForHealth(
    lockfilePath: string,
    aborted: () => boolean,
  ): Promise<{ url: string; health: HealthResult } | null> {
    const deadline = Date.now() + START_TIMEOUT_MS;
    let delay = POLL_START_MS;
    while (Date.now() < deadline) {
      if (disposed || aborted()) return null;
      const lock = await readLockfile(lockfilePath);
      if (lock) {
        const health = await ctx.api.checkHealth(lock.url);
        if (health) return { url: lock.url, health };
      }
      await sleep(delay);
      delay = Math.min(delay * 2, POLL_MAX_MS);
    }
    return null;
  }

  /**
   * Starts the local server. An explicit command invocation is consent by
   * itself; the auto path passes `silent` and suppresses toasts. Adopts an
   * already-running server rather than spawning a duplicate.
   */
  async function startFlow(opts: { silent: boolean }): Promise<void> {
    if (disposed || starting) return;
    // Claim the in-flight slot before the first await so two overlapping
    // invocations (a double-click, or command plus welcome view) cannot both
    // reach the spawn.
    starting = true;
    try {
      await startFlowLocked(opts);
    } finally {
      starting = false;
    }
  }

  async function startFlowLocked(opts: { silent: boolean }): Promise<void> {
    const ws = ctx.workspace;
    if (!ws.repoRoot || !ws.lockfilePath) {
      if (!opts.silent) {
        void vscode.window.showWarningMessage(
          "No Repowise index found in this workspace.",
        );
      }
      return;
    }

    if (isConnected()) {
      if (!opts.silent) {
        void vscode.window.showInformationMessage(
          "The Repowise server is already running.",
        );
      }
      return;
    }

    // A healthy server may already be up (started outside the editor, or by a
    // prior session). Adopt it instead of spawning a second one.
    const existing = await readLockfile(ws.lockfilePath);
    if (existing) {
      const health = await ctx.api.checkHealth(existing.url);
      if (health) {
        await onConnected(ws.repoRoot, existing.url, health);
        return;
      }
    }

    ctx.setStatusBarState("connecting");
    ctx.log.info("Starting the local Repowise server...");

    const args = ["serve", "--no-ui"];
    const port = ctx.config.serverPort();
    if (port !== null) args.push("--port", String(port));

    let child: ChildProcess;
    try {
      child = spawn(ctx.cli.executable, args, {
        cwd: ws.repoRoot,
        shell: false,
        windowsHide: true,
      });
    } catch (err) {
      await handleStartFailure(err, opts, null);
      return;
    }

    owned = child;
    let enoent = false;
    child.on("error", (err: Error) => {
      if ((err as NodeJS.ErrnoException).code === "ENOENT") enoent = true;
      ctx.log.error(`server process error: ${String(err)}`);
    });
    child.stdout?.on("data", (b: Buffer) => logServeOutput(b, "debug"));
    child.stderr?.on("data", (b: Buffer) => logServeOutput(b, "info"));
    child.on("exit", (code, signal) => {
      ctx.log.info(`server process exited (code ${code ?? "null"}, signal ${signal ?? "null"})`);
      if (owned === child) owned = null;
    });

    const result = await pollForHealth(ws.lockfilePath, () => enoent);

    if (result) {
      await onConnected(ws.repoRoot, result.url, result.health);
      return;
    }

    if (enoent) {
      // The CLI itself is missing; nothing was actually launched to clean up.
      await handleStartFailure(new Error("repowise CLI not found"), opts, null);
      return;
    }

    // Spawned but never became healthy: clean up the process we own so it does
    // not linger, then surface the failure.
    await handleStartFailure(
      new Error("server did not become healthy in time"),
      opts,
      child,
    );
  }

  /**
   * Common failure handling for a start attempt. A missing CLI points at Check
   * Setup and flags the extension as not-installed; a start timeout offers the
   * log. `toKill` is the process we own and should tear down (null when none
   * was launched).
   */
  async function handleStartFailure(
    err: unknown,
    opts: { silent: boolean },
    toKill: ChildProcess | null,
  ): Promise<void> {
    ctx.log.error(`failed to start server: ${String(err)}`);
    if (toKill) {
      killTree(toKill);
      if (owned === toKill) owned = null;
    }

    const isEnoent =
      err instanceof Error &&
      (err.message.includes("not found") ||
        (err as NodeJS.ErrnoException).code === "ENOENT");

    if (isEnoent) {
      ctx.setExtensionState("not-installed");
      ctx.setStatusBarState("server-down");
      if (!opts.silent) {
        const choice = await vscode.window.showErrorMessage(
          "Could not find the repowise CLI. Check your Repowise setup.",
          "Check Setup",
        );
        if (choice === "Check Setup") {
          void vscode.commands.executeCommand(Commands.checkSetup);
        }
      }
      return;
    }

    ctx.setExtensionState("server-down");
    ctx.setStatusBarState("server-down");
    if (!opts.silent) {
      const choice = await vscode.window.showErrorMessage(
        "The Repowise server did not start in time.",
        "Show Log",
      );
      if (choice === "Show Log") ctx.log.show();
    }
  }

  /**
   * Terminates a process we spawned, including its children. On Windows a
   * polite kill often leaves the CLI's child tree alive, so escalate to
   * `taskkill /T /F` after a grace period; elsewhere fall back from SIGTERM to
   * SIGKILL.
   */
  function killTree(child: ChildProcess): void {
    const pid = child.pid;
    const stillAlive = (): boolean =>
      child.exitCode === null && child.signalCode === null;

    if (process.platform === "win32") {
      // The CLI on PATH is a launcher exe whose real work happens in a child
      // process tree. Killing only the launcher leaves the server running, so
      // always take down the whole tree by pid; there is no graceful signal on
      // Windows to try first.
      if (pid === undefined) {
        child.kill();
        return;
      }
      try {
        spawn("taskkill", ["/pid", String(pid), "/T", "/F"], {
          shell: false,
          windowsHide: true,
        });
      } catch (err) {
        ctx.log.debug(`taskkill failed: ${String(err)}`);
        child.kill();
      }
      return;
    }

    child.kill("SIGTERM");
    setTimeout(() => {
      if (stillAlive()) child.kill("SIGKILL");
    }, KILL_GRACE_MS);
  }

  /** Stops only a server this extension started. */
  function stopServer(): void {
    if (!owned) {
      const message = isConnected()
        ? "Repowise is connected to a server it did not start, so it will not stop it."
        : "No Repowise server started by this extension is running.";
      void vscode.window.showInformationMessage(message);
      return;
    }
    const child = owned;
    owned = null;
    killTree(child);
    ctx.api.setBaseUrl(null);
    ctx.setExtensionState("server-down");
    ctx.setStatusBarState("server-down");
    ctx.log.info("Stopped the local Repowise server.");
  }

  const disposables: vscode.Disposable[] = [
    vscode.commands.registerCommand(Commands.startServer, () =>
      // An explicit invocation is its own consent, so never silent.
      void startFlow({ silent: false }),
    ),
    vscode.commands.registerCommand(Commands.stopServer, () => stopServer()),
    // Granting trust unlocks the deferred discovery we skipped while untrusted.
    vscode.workspace.onDidGrantWorkspaceTrust(() => void discover({ rescan: true })),
    // A folder set change (extension.ts already rescans) means a new primary
    // root: re-detect and rebind the watcher.
    vscode.workspace.onDidChangeWorkspaceFolders(() =>
      void discover({ rescan: true }),
    ),
  ];

  // First discovery runs off the activation hot path (this also lazily creates
  // the freshness watcher, which must never happen during activate()).
  const timer = setTimeout(() => void discover({ rescan: true }), 1_500);
  disposables.push({ dispose: () => clearTimeout(timer) });

  return {
    dispose(): void {
      disposed = true;
      watcherSub?.dispose();
      watcherSub = null;
      if (owned) {
        killTree(owned);
        owned = null;
      }
      for (const d of disposables) d.dispose();
    },
  };
}
