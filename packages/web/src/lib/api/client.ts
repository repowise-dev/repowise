/**
 * Base fetch wrapper for the WikiCode REST API.
 *
 * Reads the API base URL from NEXT_PUBLIC_WIKICODE_API_URL (default: empty string,
 * meaning requests go to the same origin — the Next.js rewrite proxies them).
 *
 * API key is read from NEXT_PUBLIC_WIKICODE_API_KEY. For production use, the key
 * should be stored in an httpOnly cookie set by the settings page.
 */

import type { ApiError } from "./types";

const BASE_URL = process.env.NEXT_PUBLIC_WIKICODE_API_URL ?? "";

function getApiKey(): string | null {
  // In browser: check localStorage (set by settings page)
  if (typeof window !== "undefined") {
    return localStorage.getItem("wikicode_api_key") ?? null;
  }
  // In server components: use env var
  return process.env.WIKICODE_API_KEY ?? process.env.NEXT_PUBLIC_WIKICODE_API_KEY ?? null;
}

function buildHeaders(extra?: Record<string, string>): Headers {
  const headers = new Headers({
    "Content-Type": "application/json",
    ...extra,
  });
  const key = getApiKey();
  if (key) {
    headers.set("Authorization", `Bearer ${key}`);
  }
  return headers;
}

class ApiClientError extends Error {
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
  const res = await fetch(url.toString(), {
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
): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const res = await fetch(url, {
    method: "POST",
    headers: buildHeaders(),
    body: body !== undefined ? JSON.stringify(body) : undefined,
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
  const res = await fetch(url, {
    method: "PATCH",
    headers: buildHeaders(),
    body: body !== undefined ? JSON.stringify(body) : undefined,
    ...fetchOptions,
  });
  return handleResponse<T>(res);
}

export async function apiDelete<T>(
  path: string,
  fetchOptions?: RequestInit,
): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const res = await fetch(url, {
    method: "DELETE",
    headers: buildHeaders(),
    ...fetchOptions,
  });
  return handleResponse<T>(res);
}

export { ApiClientError, BASE_URL, buildHeaders };
