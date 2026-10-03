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

/** Validates parsed lockfile JSON. Pure, so it runs without file or process access. */
export function isServeLock(value: unknown): value is ServeLock {
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
