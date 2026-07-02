import * as path from "node:path";
import { realpathSync } from "node:fs";
import { configureApiClient } from "@repowise-dev/api-client";
import { listRepos } from "@repowise-dev/api-client/repos";
import type { RepoResponse } from "@repowise-dev/api-client/types";
import type { Logger } from "./log";

/** Result of a successful `/health` probe. */
export interface HealthResult {
  status: string;
  db: string;
  version: string;
}

/**
 * Typed access to the local server. The base URL is discovered at runtime and
 * changes when the server starts, stops, or moves ports, so it is mutable here;
 * `configureApiClient` is re-pointed on every change.
 */
export interface RepowiseApi {
  /** Repoint the shared API client. Pass null when no server is known. */
  setBaseUrl(url: string | null): void;
  getBaseUrl(): string | null;
  /**
   * Probes `<baseUrl>/health` with a short timeout. Returns null on any
   * failure (unreachable, timeout, non-2xx), which the caller treats as
   * "server down / lock stale". Uses an explicit base URL so it can verify a
   * lockfile before committing the shared client to it.
   */
  checkHealth(baseUrl: string, timeoutMs?: number): Promise<HealthResult | null>;
  /**
   * Maps a workspace folder to its indexed repo by matching `local_path`
   * against the server's repo list. There is no path-lookup endpoint. Returns
   * null when no repo matches or the server is unreachable. The full repo
   * object is returned because features need more than the id: the indexed
   * `head_commit` (cache tag, staleness) and `default_branch` (risk base).
   */
  resolveRepo(repoRoot: string): Promise<RepoResponse | null>;
}

const DEFAULT_HEALTH_TIMEOUT_MS = 800;

/** Normalizes a filesystem path for cross-platform comparison. */
function canonicalPath(p: string): string {
  let resolved = path.resolve(p);
  try {
    resolved = realpathSync.native(resolved);
  } catch {
    // Path may not exist on disk (server-reported path); fall back to resolve.
  }
  // Windows paths are case-insensitive; normalize separators and case.
  const normalized = resolved.split(path.sep).join("/").replace(/\/+$/, "");
  return process.platform === "win32" ? normalized.toLowerCase() : normalized;
}

export function createApi(log: Logger): RepowiseApi {
  let baseUrl: string | null = null;

  function apply(): void {
    // The API client keeps global module state; keep it in sync with baseUrl.
    configureApiClient({ baseUrl: baseUrl ?? "" });
  }

  return {
    setBaseUrl(url: string | null): void {
      baseUrl = url;
      apply();
    },
    getBaseUrl: () => baseUrl,

    async checkHealth(
      url: string,
      timeoutMs = DEFAULT_HEALTH_TIMEOUT_MS,
    ): Promise<HealthResult | null> {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const res = await fetch(new URL("/health", url), {
          signal: controller.signal,
        });
        if (!res.ok) return null;
        return (await res.json()) as HealthResult;
      } catch (err) {
        log.debug(`health probe failed for ${url}: ${String(err)}`);
        return null;
      } finally {
        clearTimeout(timer);
      }
    },

    async resolveRepo(repoRoot: string): Promise<RepoResponse | null> {
      if (!baseUrl) return null;
      try {
        const target = canonicalPath(repoRoot);
        const repos = await listRepos();
        const match = repos.find(
          (repo) => repo.local_path && canonicalPath(repo.local_path) === target,
        );
        return match ?? null;
      } catch (err) {
        log.debug(`resolveRepo failed: ${String(err)}`);
        return null;
      }
    },
  };
}
