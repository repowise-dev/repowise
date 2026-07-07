/**
 * Configures the shared @repowise-dev/api-client package with this app's
 * base URL and auth token resolution, then re-exports what the rest of the
 * app still imports from "@/lib/api/client" (ApiClientError).
 *
 * Runs as a side effect the first time any "@/lib/api/*" module is imported
 * (every domain module below imports this file first), which matches the
 * timing of the module-level singleton this replaced.
 *
 * Base URL: NEXT_PUBLIC_REPOWISE_API_URL (default: empty string, meaning
 * requests go to the same origin, proxied by the Next.js rewrite).
 *
 * API key: read from localStorage in the browser (set by the settings page)
 * and from REPOWISE_API_KEY / NEXT_PUBLIC_REPOWISE_API_KEY on the server.
 */

import { configureApiClient } from "@repowise-dev/api-client";

// Client-side: empty string → relative requests proxied via Next.js rewrites.
// Server-side: use REPOWISE_API_URL (the backend) since server `fetch` bypasses rewrites.
const BASE_URL =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_REPOWISE_API_URL ?? "")
    : (process.env.REPOWISE_API_URL || process.env.NEXT_PUBLIC_REPOWISE_API_URL || "http://localhost:7337");

function getApiKey(): string | null {
  // In browser: check localStorage (set by settings page)
  if (typeof window !== "undefined") {
    return localStorage.getItem("repowise_api_key") ?? null;
  }
  // In server components: use env var
  return process.env.REPOWISE_API_KEY ?? process.env.NEXT_PUBLIC_REPOWISE_API_KEY ?? null;
}

configureApiClient({ baseUrl: BASE_URL, token: getApiKey });

export { ApiClientError } from "@repowise-dev/api-client";
