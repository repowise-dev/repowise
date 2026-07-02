import { readFile } from "node:fs/promises";

/**
 * Shape of `serve.lock.json`, written by the running server after it binds a
 * port. `url` is always a loopback address even when the server binds a
 * wildcard host, so it is safe to probe directly.
 */
export interface ServeLock {
  pid: number;
  host: string;
  port: number;
  url: string;
  ui_port: number | null;
  server_version: string;
  started_at: string;
}

function isServeLock(value: unknown): value is ServeLock {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.pid === "number" &&
    typeof v.host === "string" &&
    typeof v.port === "number" &&
    typeof v.url === "string" &&
    (typeof v.ui_port === "number" || v.ui_port === null) &&
    typeof v.server_version === "string" &&
    typeof v.started_at === "string"
  );
}

/**
 * Reads and validates the lockfile. Returns null when the file is missing,
 * unreadable, or malformed. A present-but-stale file (dead server) still parses
 * here; liveness is a separate health probe against `url`, because probing a
 * pid cross-platform from Node is unreliable.
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
