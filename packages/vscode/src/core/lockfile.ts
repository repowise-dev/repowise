import { readFile } from "node:fs/promises";
import { isServeLock, type ServeLock } from "@repowise-dev/types/serve-lock";

/**
 * Reads and validates the lockfile. Returns null when the file is missing,
 * unreadable, or malformed. A present-but-stale file (dead server) still
 * parses here; callers must gate on `isPidAlive` before trusting it, then
 * health-probe `url`. Both checks matter: the pid filters out a dead writer
 * whose port an unrelated server re-bound (it would answer /health
 * convincingly), and the probe confirms the surviving pid still serves.
 */
export async function readLockfile(path: string): Promise<ServeLock | null> {
  try {
    const raw = await readFile(path, "utf8");
    const parsed: unknown = JSON.parse(raw);
    return isServeLock(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

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
