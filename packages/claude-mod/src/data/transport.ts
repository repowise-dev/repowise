import { configureApiClient, createAdapterFetch } from "@repowise-dev/api-client";
import type { Host } from "../host";

/** Points the shared api-client at a loopback server, over the host's HTTP. No token: C6 forbids reading one. */
export function connectApiClient(host: Host, baseUrl: string): void {
  configureApiClient({ baseUrl, fetch: createAdapterFetch(host.http) });
}

export class TimeoutError extends Error {
  constructor(label: string, ms: number) {
    super(`${label} timed out after ${ms} ms`);
    this.name = "TimeoutError";
  }
}

/** Rejects with TimeoutError when `work` has not settled in `ms`; the work itself is not cancelled. */
export function withTimeout<T>(work: Promise<T>, ms: number, label: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new TimeoutError(label, ms)), ms);
  });
  return Promise.race([work, timeout]).finally(() => clearTimeout(timer));
}
