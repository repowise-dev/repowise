import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { middleware } from "./middleware";

/**
 * The same-origin proxy is the only layer that can attach a server-held key:
 * the browser's own fetch cannot see REPOWISE_API_KEY. A deployed container
 * already carries the key as an env var, so `/api/*` requests should reach
 * the upstream API already authenticated instead of asking every user to
 * paste the key into localStorage.
 *
 * Next.js applies the `request.headers` option via internal
 * `x-middleware-request-<name>` headers plus an `x-middleware-override-headers`
 * list, so these tests assert on those carriers (what actually reaches the
 * upstream request), not on the response body.
 */

const API_KEY = "test-key-2319-not-a-real-secret";
const API_URL = "http://api.internal:7337";

function request(headers: Record<string, string> = {}): NextRequest {
  return new NextRequest("http://localhost:3000/api/pages?limit=5", {
    headers,
  });
}

/** Header names Next will carry onto the upstream request. */
function overriddenHeaderNames(response: Response): string[] {
  const list = response.headers.get("x-middleware-override-headers");
  return list ? list.split(",").map((name) => name.trim()) : [];
}

beforeEach(() => {
  vi.stubEnv("REPOWISE_API_URL", API_URL);
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("web proxy API key", () => {
  it("attaches the environment key when the browser sends no Authorization", () => {
    vi.stubEnv("REPOWISE_API_KEY", API_KEY);

    const response = middleware(request());

    expect(response.headers.get("x-middleware-rewrite")).toBe(
      "http://api.internal:7337/api/pages?limit=5"
    );
    expect(response.headers.get("x-middleware-request-authorization")).toBe(
      `Bearer ${API_KEY}`
    );
    expect(overriddenHeaderNames(response)).toContain("authorization");
  });

  it("forwards the rest of the headers alongside the injected one", () => {
    vi.stubEnv("REPOWISE_API_KEY", API_KEY);

    const response = middleware(
      request({ accept: "application/json", cookie: "session=abc" })
    );

    // The override replaces the whole header set upstream, so anything the
    // browser sent must ride along or the request arrives stripped.
    expect(overriddenHeaderNames(response)).toEqual(
      expect.arrayContaining(["authorization", "accept", "cookie"])
    );
    expect(response.headers.get("x-middleware-request-accept")).toBe(
      "application/json"
    );
    expect(response.headers.get("x-middleware-request-cookie")).toBe(
      "session=abc"
    );
  });

  it("keeps an Authorization header the browser supplied", () => {
    vi.stubEnv("REPOWISE_API_KEY", API_KEY);

    const response = middleware(
      request({ authorization: "Bearer browser-users-own-key" })
    );

    expect(response.headers.get("x-middleware-rewrite")).toBe(
      "http://api.internal:7337/api/pages?limit=5"
    );
    // No override set means Next forwards the incoming header untouched.
    expect(response.headers.get("x-middleware-request-authorization")).toBeNull();
    expect(overriddenHeaderNames(response)).not.toContain("authorization");
  });

  it("proxies unchanged when the environment key is unset", () => {
    const response = middleware(request());

    expect(response.headers.get("x-middleware-rewrite")).toBe(
      "http://api.internal:7337/api/pages?limit=5"
    );
    expect(response.headers.get("x-middleware-request-authorization")).toBeNull();
    expect(response.headers.get("x-middleware-override-headers")).toBeNull();
  });

  it("does not put the key on the response the client receives", () => {
    vi.stubEnv("REPOWISE_API_KEY", API_KEY);

    const response = middleware(request());
    const clientFacing = [...response.headers.entries()].filter(
      ([name]) =>
        !name.startsWith("x-middleware-request-") &&
        name !== "x-middleware-override-headers"
    );

    for (const [name, value] of clientFacing) {
      expect(`${name}: ${value}`).not.toContain(API_KEY);
    }
  });
});
