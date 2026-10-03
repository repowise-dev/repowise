import { afterEach, describe, expect, it } from "vitest";

import {
  ApiClientError,
  apiGet,
  apiPost,
  configureApiClient,
  createAdapterFetch,
  type MinimalRequestInit,
  type MinimalResponse,
} from "./client";

interface Call {
  url: string;
  init: MinimalRequestInit;
}

function fakeMinimal(response: MinimalResponse) {
  const calls: Call[] = [];
  const minimal = async (url: string, init: MinimalRequestInit) => {
    calls.push({ url, init });
    return response;
  };
  return { calls, minimal };
}

function respond(status: number, text: string): MinimalResponse {
  return { status, ok: status >= 200 && status < 300, headers: {}, text };
}

describe("createAdapterFetch", () => {
  afterEach(() => {
    // The client config is module-global; leave it as the next test expects.
    configureApiClient({ baseUrl: "" });
  });

  it("parses an ok JSON body and passes method, headers and body through", async () => {
    const { calls, minimal } = fakeMinimal(respond(200, '{"id":"r1","n":2}'));
    configureApiClient({
      baseUrl: "http://127.0.0.1:7337",
      token: "secret",
      fetch: createAdapterFetch(minimal),
    });

    const result = await apiPost<{ id: string; n: number }>("/api/repos", { name: "x" });

    expect(result).toEqual({ id: "r1", n: 2 });
    expect(calls).toHaveLength(1);
    const [call] = calls;
    expect(call?.url).toBe("http://127.0.0.1:7337/api/repos");
    expect(call?.init.method).toBe("POST");
    expect(call?.init.body).toBe('{"name":"x"}');
    expect(call?.init.headers).toEqual({
      "Content-Type": "application/json",
      Authorization: "Bearer secret",
    });
  });

  it("flattens a Headers instance into a plain object", async () => {
    const { calls, minimal } = fakeMinimal(respond(200, "{}"));
    await createAdapterFetch(minimal)("http://h", { headers: new Headers({ "X-A": "1" }) });
    expect(calls[0]?.init.headers).toEqual({ "x-a": "1" });
  });

  describe("without a Headers global", () => {
    const realHeaders = globalThis.Headers;
    afterEach(() => {
      globalThis.Headers = realHeaders;
    });

    it("sends apiGet and apiPost with plain-object headers", async () => {
      // @ts-expect-error simulating a runtime that has no Headers global
      delete globalThis.Headers;
      expect(typeof globalThis.Headers).toBe("undefined");

      const { calls, minimal } = fakeMinimal(respond(200, '{"ok":true}'));
      configureApiClient({
        baseUrl: "http://h",
        token: () => "tok",
        fetch: createAdapterFetch(minimal),
      });

      await expect(apiGet("/api/a", { q: "x" })).resolves.toEqual({ ok: true });
      await expect(apiPost("/api/b", { n: 1 })).resolves.toEqual({ ok: true });

      const expectedHeaders = {
        "Content-Type": "application/json",
        Authorization: "Bearer tok",
      };
      expect(calls).toEqual([
        { url: "http://h/api/a?q=x", init: { method: "GET", headers: expectedHeaders } },
        {
          url: "http://h/api/b",
          init: { method: "POST", headers: expectedHeaders, body: '{"n":1}' },
        },
      ]);
    });

    it("omits Authorization when no token resolves", async () => {
      // @ts-expect-error simulating a runtime that has no Headers global
      delete globalThis.Headers;

      const { calls, minimal } = fakeMinimal(respond(200, "{}"));
      configureApiClient({ baseUrl: "http://h", fetch: createAdapterFetch(minimal) });

      await apiGet("/api/a");
      expect(calls[0]?.init.headers).toEqual({ "Content-Type": "application/json" });
    });
  });

  it("surfaces an error status through ApiClientError with the JSON detail", async () => {
    const { minimal } = fakeMinimal(respond(404, '{"detail":"Repo not found"}'));
    configureApiClient({ baseUrl: "http://h", fetch: createAdapterFetch(minimal) });

    const err = await apiGet("/api/repos/missing").catch((e: unknown) => e);

    expect(err).toBeInstanceOf(ApiClientError);
    expect((err as ApiClientError).status).toBe(404);
    expect((err as ApiClientError).detail).toBe("Repo not found");
  });

  it("falls back to an empty detail when an error body is not JSON", async () => {
    const { minimal } = fakeMinimal(respond(502, "<html>Bad Gateway</html>"));
    configureApiClient({ baseUrl: "http://h", fetch: createAdapterFetch(minimal) });

    const err = await apiGet("/api/x").catch((e: unknown) => e);

    expect(err).toBeInstanceOf(ApiClientError);
    expect((err as ApiClientError).status).toBe(502);
    expect((err as ApiClientError).detail).toBe("");
  });

  it("resolves an empty 204 body to undefined", async () => {
    const { minimal } = fakeMinimal(respond(204, ""));
    configureApiClient({ baseUrl: "http://h", fetch: createAdapterFetch(minimal) });

    await expect(apiPost("/api/x")).resolves.toBeUndefined();
  });

  it("rejects an ok response whose body is not JSON", async () => {
    const { minimal } = fakeMinimal(respond(200, "not json"));
    configureApiClient({ baseUrl: "http://h", fetch: createAdapterFetch(minimal) });

    await expect(apiGet("/api/x")).rejects.toBeInstanceOf(SyntaxError);
  });

  it("accepts URL input, record headers and exposes text()", async () => {
    const { calls, minimal } = fakeMinimal(respond(200, "graph TD"));
    const adapted = createAdapterFetch(minimal);
    const controller = new AbortController();

    const res = await adapted(new URL("http://h/api/c4"), {
      headers: { "X-Test": "1" },
      signal: controller.signal,
    });

    expect(await res.text()).toBe("graph TD");
    expect(res.statusText).toBe("");
    expect(calls[0]?.url).toBe("http://h/api/c4");
    expect(calls[0]?.init).toEqual({ headers: { "X-Test": "1" }, signal: controller.signal });
  });

  it("refuses a non-string body rather than sending it mangled", async () => {
    const { minimal } = fakeMinimal(respond(200, "{}"));
    const adapted = createAdapterFetch(minimal);

    await expect(adapted("http://h", { method: "POST", body: new Uint8Array([1]) })).rejects.toBeInstanceOf(
      TypeError,
    );
  });
});
