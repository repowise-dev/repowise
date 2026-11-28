import { readFileSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { isServeLock, type ServeLock } from "@repowise-dev/types/serve-lock";

export { isPidAlive } from "./serverProcess";

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

/** `readLockfile` for paths that cannot await (extension shutdown). */
export function readLockfileSync(path: string): ServeLock | null {
  try {
    const parsed: unknown = JSON.parse(readFileSync(path, "utf8"));
    return isServeLock(parsed) ? parsed : null;
  } catch {
    return null;
  }
}
