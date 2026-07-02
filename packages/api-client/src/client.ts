/**
 * Base fetch wrapper for the repowise REST API. Framework-free: the base URL
 * and auth token are not read from any env var here; a host app calls
 * `configureApiClient` once, before making requests, to supply them.
 */

import type { ApiError } from "./types";

/**
 * `token` may be a literal string, or a resolver called fresh on every
 * request (e.g. to read a value that can change without a page reload, like
 * a token stored in localStorage).
 */
export interface ApiClientConfig {
  baseUrl: string;
  token?: string | null | (() => string | null);
  fetch?: typeof fetch;
}

let config: ApiClientConfig = { baseUrl: "" };

/** Sets the base URL / token / fetch implementation used by every request. */
export function configureApiClient(next: ApiClientConfig): void {
  config = next;
  BASE_URL = next.baseUrl;
}

export function getApiClientConfig(): ApiClientConfig {
  return config;
}

// Live binding: reflects whatever `configureApiClient` last set. Kept as a
// direct export (rather than a getter function) so existing call sites that
// read `BASE_URL` as a value keep working unchanged.
export let BASE_URL = "";

function resolveToken(): string | null {
  const { token } = config;
  if (typeof token === "function") return token();
  return token ?? null;
}

function buildHeaders(extra?: Record<string, string>): Headers {
  const headers = new Headers({
    "Content-Type": "application/json",
    ...extra,
  });
  const token = resolveToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  return headers;
}

export class ApiClientError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(`API error ${status}: ${detail}`);
    this.name = "ApiClientError";
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const json = (await res.json()) as ApiError;
      detail = json.detail ?? detail;
    } catch {
      // response body is not JSON
    }
    throw new ApiClientError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export function doFetch(url: string, init: RequestInit): Promise<Response> {
  return (config.fetch ?? fetch)(url, init);
}

export async function apiGet<T>(
  path: string,
  params?: Record<string, string | number | boolean | undefined>,
  fetchOptions?: RequestInit,
): Promise<T> {
  const url = new URL(`${BASE_URL}${path}`, typeof window !== "undefined" ? window.location.href : "http://localhost");
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) {
        url.searchParams.set(k, String(v));
      }
    }
  }
  const res = await doFetch(url.toString(), {
    method: "GET",
    headers: buildHeaders(),
    ...fetchOptions,
  });
  return handleResponse<T>(res);
}

export async function apiPost<T>(
  path: string,
  body?: unknown,
  fetchOptions?: RequestInit,
  params?: Record<string, string | number | boolean | undefined>,
): Promise<T> {
  const url = new URL(`${BASE_URL}${path}`, typeof window !== "undefined" ? window.location.href : "http://localhost");
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) {
        url.searchParams.set(k, String(v));
      }
    }
  }
  const res = await doFetch(url.toString(), {
    method: "POST",
    headers: buildHeaders(),
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    ...fetchOptions,
  });
  return handleResponse<T>(res);
}

export async function apiPut<T>(
  path: string,
  body?: unknown,
  fetchOptions?: RequestInit,
): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const res = await doFetch(url, {
    method: "PUT",
    headers: buildHeaders(),
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    ...fetchOptions,
  });
  return handleResponse<T>(res);
}

export async function apiPatch<T>(
  path: string,
  body?: unknown,
  fetchOptions?: RequestInit,
): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const res = await doFetch(url, {
    method: "PATCH",
    headers: buildHeaders(),
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    ...fetchOptions,
  });
  return handleResponse<T>(res);
}

export async function apiDelete<T>(
  path: string,
  fetchOptions?: RequestInit,
): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const res = await doFetch(url, {
    method: "DELETE",
    headers: buildHeaders(),
    ...fetchOptions,
  });
  return handleResponse<T>(res);
}

export { buildHeaders };
