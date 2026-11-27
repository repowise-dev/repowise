import { spawnSync } from "node:child_process";
import { readFileSync, unlinkSync } from "node:fs";

/**
 * Process control for the local server. Node-only on purpose (no workspace
 * packages), so the tests can load it outside the bundle.
 */

/**
 * True when a process with this pid exists (signal-0 probe, no signal sent).
 * EPERM means alive but owned by someone else; ESRCH (or anything else) means
 * gone.
 */
export function isPidAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return (err as NodeJS.ErrnoException).code === "EPERM";
  }
}

/**
 * Deletes the serve lockfile when its pid is gone. A killed server cannot
 * clean up after itself, so its lock outlives it; removing it before a start
 * means the new server's lock is the only one the start poll can read. A lock
 * whose pid is alive is left alone: that server may still be starting. Only
 * the pid matters here, so the rest of the file is not validated. Synchronous
 * so a stop during extension shutdown can finish it. Returns whether a file
 * was removed.
 */
export function removeStaleLockfile(
  path: string,
  alive: (pid: number) => boolean = isPidAlive,
): boolean {
  try {
    const parsed = JSON.parse(readFileSync(path, "utf8")) as { pid?: unknown };
    if (!Number.isInteger(parsed?.pid) || alive(parsed.pid as number)) return false;
    unlinkSync(path);
    return true;
  } catch {
    return false;
  }
}

/** Grace period before escalating a POSIX stop from SIGTERM to SIGKILL. */
const KILL_GRACE_MS = 3_000;

/**
 * Stops a process and everything it started. On Windows `taskkill /T /F`
 * takes the whole tree (the CLI on PATH is a launcher whose server runs in a
 * child, and `serve` may run a web UI child); it runs synchronously so it
 * completes even when called while the extension host shuts down. Elsewhere
 * SIGTERM lets `serve` shut down and remove its own lockfile, with SIGKILL
 * after a grace period for a server that ignores it.
 */
export function killProcessTree(pid: number, platform = process.platform): void {
  if (platform === "win32") {
    spawnSync("taskkill", ["/pid", String(pid), "/T", "/F"], {
      shell: false,
      windowsHide: true,
      timeout: 10_000,
    });
    return;
  }
  try {
    process.kill(pid, "SIGTERM");
  } catch {
    return; // Already gone.
  }
  setTimeout(() => {
    if (!isPidAlive(pid)) return;
    try {
      process.kill(pid, "SIGKILL");
    } catch {
      // Exited between the probe and the kill.
    }
  }, KILL_GRACE_MS);
}

/** Resolves true once `pid` is gone, false if it outlives `timeoutMs`. */
export async function waitForPidExit(pid: number, timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (isPidAlive(pid)) {
    if (Date.now() >= deadline) return false;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return true;
}

/** What stopping the server means given who started it. */
export type StopPlan =
  /** This window spawned it: kill it without asking. */
  | "own"
  /** Started elsewhere (a terminal, another window): confirm first. */
  | "external"
  /** Only a dead server's lockfile is left: remove it. */
  | "stale"
  /** Nothing to stop. */
  | "none";

export function planStop(
  ownsProcess: boolean,
  lock: { pid: number } | null,
  alive: (pid: number) => boolean = isPidAlive,
): StopPlan {
  if (ownsProcess) return "own";
  if (!lock) return "none";
  return alive(lock.pid) ? "external" : "stale";
}
