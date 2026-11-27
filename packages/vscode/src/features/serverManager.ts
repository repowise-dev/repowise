import * as vscode from "vscode";
import { spawn, type ChildProcess } from "node:child_process";
import { Commands, InternalCommands, MIN_SERVER_VERSION } from "../constants";
import type { RepowiseContext } from "../core/context";
import { isLoopbackUrl, type ServeLock } from "@repowise-dev/types/serve-lock";
import { readLockfile, readLockfileSync } from "../core/lockfile";
import {
  isPidAlive,
  killProcessTree,
  planStop,
  removeStaleLockfile,
  waitForPidExit,
} from "../core/serverProcess";
import { resolveAutoStart, serverMenuItems, type AutoStartMode } from "./serverActions";
import type { HealthResult } from "../core/api";
import { cliSpawn } from "../core/cliInvocation";

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
  /** When `owned` was spawned; the idle stop counts from here at the earliest. */
  let ownedSince = 0;
  /** True while a start attempt is in flight, to guard against double-spawn. */
  let starting = false;
  /** Show the version-too-old warning at most once per session. */
  let warnedVersionLow = false;
  /**
   * Set once the start prompt has shown or the user stopped the server:
   * discovery re-runs on every lockfile change (a stop rewrites it), and
   * neither the prompt nor "always" may start a server the user just stopped.
   * An explicit start clears it.
   */
  let autoStartSuppressed = false;
  /**
   * Bumped by every stop. A start or prompt that began before the stop sees
   * the change after its next await and abandons its work.
   */
  let lifecycle = 0;
  /** Lockfile of the server we spawned, fixed at spawn time. */
  let ownedLockfilePath: string | null = null;
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
          `The Repowise server (${health.version}) is older than this extension supports (${MIN_SERVER_VERSION}). Upgrade with: uv tool upgrade repowise (or pip install --upgrade repowise)`,
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

    // The pid gate is not redundant with the probe: a dead writer's port can
    // be re-bound by an unrelated server that answers /health convincingly.
    const lock = await readLocalLockfile(ws.lockfilePath);
    if (lock && isPidAlive(lock.pid)) {
      const health = await ctx.api.checkHealth(lock.url);
      if (health) {
        await onConnected(ws.repoRoot, lock.url, health);
        return;
      }
      ctx.log.debug(`Lockfile pid ${lock.pid} is alive but ${lock.url} did not answer /health.`);
    } else if (lock) {
      ctx.log.debug(`Ignoring stale lockfile (pid ${lock.pid} is gone).`);
    }

    // No lockfile, or a present-but-stale one whose server no longer answers.
    ctx.setExtensionState("server-down");
    ctx.setStatusBarState("server-down");

    // "always" starts silently; "ask" prompts once per session; "never" waits
    // for the status bar or a welcome-view action.
    const mode = ctx.config.autoStart();
    if (autoStartSuppressed || mode === "never") return;
    if (mode === "ask") autoStartSuppressed = true;
    const generation = lifecycle;
    const start = await resolveAutoStart(mode, {
      ask: (message, ...items) => vscode.window.showInformationMessage(message, ...items),
      persist: (value) => persistAutoStart(value),
    });
    // The prompt can sit open for a while: start only if still needed and
    // nothing was stopped meanwhile.
    if (start && !disposed && !isConnected() && generation === lifecycle) {
      void startFlow({ silent: mode === "always" });
    }
  }

  /**
   * Saves an Always / Never answer where the effective value comes from, so a
   * folder or workspace override does not keep asking.
   */
  function persistAutoStart(value: AutoStartMode): Thenable<void> {
    const config = vscode.workspace.getConfiguration("repowise");
    const seen = config.inspect<string>("server.autoStart");
    const target =
      seen?.workspaceFolderValue !== undefined
        ? vscode.ConfigurationTarget.WorkspaceFolder
        : seen?.workspaceValue !== undefined
          ? vscode.ConfigurationTarget.Workspace
          : vscode.ConfigurationTarget.Global;
    return config.update("server.autoStart", value, target);
  }

  /**
   * The lockfile, unless its url points off this machine: the file sits in the
   * repo, so a cloned repo can name any host. Such a lock is ignored and the
   * normal start/connect path runs.
   */
  async function readLocalLockfile(path: string): Promise<ServeLock | null> {
    const lock = await readLockfile(path);
    if (lock && !isLoopbackUrl(lock.url)) {
      ctx.log.warn("Ignoring serve.lock.json: its url is not a loopback address.");
      return null;
    }
    return lock;
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
      // Pid-gated so the poll cannot adopt a stale lockfile (and whatever
      // re-bound its port) in the window before our child overwrites it.
      const lock = await readLocalLockfile(lockfilePath);
      if (lock && isPidAlive(lock.pid)) {
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
    // An explicit start is consent again after a stop.
    if (!opts.silent) autoStartSuppressed = false;
    try {
      await startFlowLocked(opts);
    } finally {
      starting = false;
    }
  }

  async function startFlowLocked(opts: { silent: boolean }): Promise<void> {
    const generation = lifecycle;
    const cancelled = (): boolean => disposed || generation !== lifecycle;
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
    // prior session). Adopt it instead of spawning a second one. Pid-gated:
    // never adopt through a dead writer's lockfile.
    const existing = await readLocalLockfile(ws.lockfilePath);
    if (existing && isPidAlive(existing.pid)) {
      const health = await ctx.api.checkHealth(existing.url);
      if (cancelled()) return;
      if (health) {
        await onConnected(ws.repoRoot, existing.url, health);
        return;
      }
    }
    if (cancelled()) return;

    // A dead server's lock would otherwise sit there until ours overwrites it.
    if (removeStaleLockfile(ws.lockfilePath)) {
      ctx.log.info("Removed a stale serve.lock.json left by a server that is gone.");
    }

    ctx.setStatusBarState("connecting");
    ctx.log.info("Starting the local Repowise server...");

    const args = ["serve", "--no-ui"];
    const port = ctx.config.serverPort();
    if (port !== null) args.push("--port", String(port));

    let child: ChildProcess;
    try {
      const spec = cliSpawn(ctx.cli.executable, args, {
        cwd: ws.repoRoot,
        windowsHide: true,
      });
      child = spawn(spec.command, spec.args, spec.options);
    } catch (err) {
      await handleStartFailure(err, opts, null);
      return;
    }

    owned = child;
    ownedSince = Date.now();
    ownedLockfilePath = ws.lockfilePath;
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

    const result = await pollForHealth(ws.lockfilePath, () => enoent || cancelled());

    if (cancelled()) {
      // Stopped (or the window closed) while starting: the stop saw no server
      // yet, so take down the one we just spawned.
      if (owned === child) {
        owned = null;
        killOwned(child);
      }
      return;
    }

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
      killOwned(toKill);
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
   * Terminates the server this window spawned and its child tree. If the
   * launcher already exited and orphaned the server, the server is found
   * through the lockfile recorded at spawn time. The lockfile is removed once
   * its pid is gone. Synchronous on Windows so it completes during extension
   * shutdown. Returns the server pid the lockfile named, if any.
   */
  function killOwned(child: ChildProcess): number | null {
    const lockfilePath = ownedLockfilePath;
    ownedLockfilePath = null;
    const lock = lockfilePath ? readLockfileSync(lockfilePath) : null;
    if (child.pid !== undefined && child.exitCode === null && child.signalCode === null) {
      killProcessTree(child.pid);
    } else {
      child.kill();
      if (lock && isPidAlive(lock.pid)) killProcessTree(lock.pid);
    }
    if (lockfilePath) removeStaleLockfile(lockfilePath);
    return lock?.pid ?? null;
  }

  /** After a stop: disconnect and settle into server-down. */
  function settleStopped(): void {
    ctx.api.setBaseUrl(null);
    ctx.setExtensionState("server-down");
    ctx.setStatusBarState("server-down");
  }

  /**
   * Stops the server. One this window started stops without asking; one
   * started elsewhere (a terminal, another window) stops after a one-line
   * confirm; a dead server's leftover lockfile is removed. Returns whether no
   * server is running afterwards. `verb` words the confirm for a restart.
   */
  async function stopServer(verb: "Stop" | "Restart" = "Stop"): Promise<boolean> {
    // Stopping is an answer: the lockfile change it causes must not restart it.
    autoStartSuppressed = true;
    lifecycle++;
    const lockfilePath = ctx.workspace.lockfilePath;
    const lock = lockfilePath ? await readLocalLockfile(lockfilePath) : null;
    const plan = planStop(owned !== null, lock);

    if (plan === "own" && owned) {
      const child = owned;
      owned = null;
      const ownLock = ownedLockfilePath;
      const serverPid = killOwned(child);
      if (serverPid !== null && !(await waitForPidExit(serverPid, 5_000))) {
        void vscode.window.showErrorMessage(
          `Could not stop the Repowise server (pid ${serverPid}).`,
        );
        return false;
      }
      if (ownLock) removeStaleLockfile(ownLock);
      settleStopped();
      ctx.log.info("Stopped the local Repowise server.");
      return true;
    }

    if (plan === "external" && lock && lockfilePath) {
      const choice = await vscode.window.showWarningMessage(
        `This Repowise server (pid ${lock.pid}) was started outside the editor. ${verb} it?`,
        verb,
      );
      if (choice !== verb) return false;
      // The confirm can sit open: kill only if the same server still holds
      // the lockfile and answers, so a reused pid is never touched.
      const again = await readLocalLockfile(lockfilePath);
      const answers =
        again?.pid === lock.pid && isPidAlive(lock.pid)
          ? await ctx.api.checkHealth(lock.url)
          : null;
      if (!answers) {
        if (removeStaleLockfile(lockfilePath)) settleStopped();
        void vscode.window.showInformationMessage(
          "That Repowise server is no longer answering, so it was left alone.",
        );
        return false;
      }
      killProcessTree(lock.pid);
      if (!(await waitForPidExit(lock.pid, 5_000))) {
        void vscode.window.showErrorMessage(
          `Could not stop the Repowise server (pid ${lock.pid}).`,
        );
        return false;
      }
      removeStaleLockfile(lockfilePath);
      settleStopped();
      ctx.log.info(`Stopped the Repowise server started outside the editor (pid ${lock.pid}).`);
      return true;
    }

    if (plan === "stale" && lockfilePath) {
      removeStaleLockfile(lockfilePath);
      ctx.log.info("Removed a stale serve.lock.json; no server was running.");
    }
    settleStopped();
    if (verb === "Stop") {
      void vscode.window.showInformationMessage("No Repowise server is running.");
    }
    return true;
  }

  async function restartServer(): Promise<void> {
    if (starting) return;
    if (await stopServer("Restart")) await startFlow({ silent: false });
  }

  /** The status-bar menu: the server actions that apply right now. */
  async function showServerMenu(): Promise<void> {
    const running = isConnected();
    const lockfilePath = ctx.workspace.lockfilePath;
    const lock = running && lockfilePath ? await readLocalLockfile(lockfilePath) : null;
    const webUi = lock?.ui_port ? `http://localhost:${lock.ui_port}` : null;
    const picked = await vscode.window.showQuickPick(serverMenuItems(running, webUi !== null), {
      placeHolder: running ? "The Repowise server is running" : "The Repowise server is stopped",
    });
    switch (picked?.action) {
      case "start":
        return startFlow({ silent: false });
      case "stop":
        await stopServer();
        return;
      case "restart":
        return restartServer();
      case "webUi":
        if (webUi) await vscode.env.openExternal(vscode.Uri.parse(webUi));
        return;
      case "log":
        ctx.log.show();
        return;
    }
  }

  /**
   * Idle stop (`server.idleStopMinutes`): a once-a-minute local check, no
   * network, that stops a server this window started once the editor has made
   * no request to it for that long. No timer at the default 0.
   */
  let idleTimer: ReturnType<typeof setInterval> | undefined;
  function syncIdleTimer(): void {
    const minutes = ctx.config.idleStopMinutes();
    if (minutes > 0 && !idleTimer) {
      idleTimer = setInterval(() => {
        const limit = ctx.config.idleStopMinutes();
        if (limit <= 0 || !owned || !isConnected() || starting) return;
        const since = Date.now() - Math.max(ctx.api.lastRequestAt(), ownedSince);
        if (since >= limit * 60_000) {
          ctx.log.info(`Stopping the local Repowise server after ${limit} idle minutes.`);
          void stopServer();
        }
      }, 60_000);
    } else if (minutes <= 0 && idleTimer) {
      clearInterval(idleTimer);
      idleTimer = undefined;
    }
  }
  syncIdleTimer();

  /** In-flight guard and cooldown for connection-failure rechecks. */
  let rechecking = false;
  let lastRecheckAt = 0;

  /**
   * A data request failed at the network level while we believed we were
   * connected. Re-probe once (cooldown-limited): a healthy probe means a
   * transient blip; a dead one re-runs discovery, which settles into
   * server-down and restores the Start Server affordances.
   */
  async function recheckConnection(): Promise<void> {
    if (disposed || rechecking || !isConnected()) return;
    const now = Date.now();
    if (now - lastRecheckAt < 5_000) return;
    rechecking = true;
    lastRecheckAt = now;
    try {
      const url = ctx.api.getBaseUrl();
      if (!url) return;
      const health = await ctx.api.checkHealth(url);
      if (health || disposed) return;
      ctx.log.info("Server stopped answering; re-running discovery.");
      await discover({ rescan: false });
    } finally {
      rechecking = false;
    }
  }
  ctx.api.onConnectionFailure(() => void recheckConnection());

  const disposables: vscode.Disposable[] = [
    vscode.commands.registerCommand(Commands.startServer, () =>
      // An explicit invocation is its own consent, so never silent.
      void startFlow({ silent: false }),
    ),
    vscode.commands.registerCommand(Commands.stopServer, () => void stopServer()),
    vscode.commands.registerCommand(Commands.restartServer, () => void restartServer()),
    vscode.commands.registerCommand(InternalCommands.serverMenu, () => showServerMenu()),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("repowise.server.idleStopMinutes")) syncIdleTimer();
    }),
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
      if (idleTimer) clearInterval(idleTimer);
      // Closing the window that started the server stops it: no orphan keeps
      // holding memory after the editor is gone.
      if (owned) {
        const child = owned;
        owned = null;
        killOwned(child);
      }
      for (const d of disposables) d.dispose();
    },
  };
}
