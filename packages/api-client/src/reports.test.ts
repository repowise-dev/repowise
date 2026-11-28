import { afterEach, describe, expect, it } from "vitest";

import { configureApiClient, createAdapterFetch, type ApiRequestInit } from "./client";
import {
  createFirstLookApi,
  firstLookMarkdownPath,
  getFirstLook,
  getFirstLookPrompt,
  markFirstLookItem,
  rewriteFirstLook,
} from "./reports";

function serve() {
  const sent: { url: string; method?: string | undefined; body?: string }[] = [];
  configureApiClient({
    baseUrl: "http://127.0.0.1:7337",
    fetch: createAdapterFetch(async (url, init) => {
      sent.push({
        url: url.replace("http://127.0.0.1:7337", ""),
        method: init.method,
        ...(init.body !== undefined ? { body: init.body } : {}),
      });
      return { status: 200, ok: true, headers: {}, text: "{}" };
    }),
  });
  return sent;
}

const ROOT = "/api/repos/r1/reports/first-look";

describe("repo report requests", () => {
  afterEach(() => configureApiClient({ baseUrl: "" }));

  it("reads the report and its prompts with the flavor and target", async () => {
    const sent = serve();
    await getFirstLook("r1", { flavor: "cursor" });
    await getFirstLookPrompt("r1", { section: "security" }, "generic");
    await getFirstLookPrompt("r1", null, "generic");
    expect(sent).toEqual([
      { url: `${ROOT}?flavor=cursor`, method: "GET" },
      { url: `${ROOT}/prompt?flavor=generic&section=security`, method: "GET" },
      { url: `${ROOT}/prompt?flavor=generic`, method: "GET" },
    ]);
  });

  it("writes a mark and a rewrite", async () => {
    const sent = serve();
    await markFirstLookItem("r1", "sec/1", { status: "done" });
    await rewriteFirstLook("r1", { flavor: "generic" });
    expect(sent).toEqual([
      { url: `${ROOT}/items/sec%2F1/mark`, method: "PUT", body: '{"status":"done"}' },
      { url: `${ROOT}/rewrite?flavor=generic`, method: "POST" },
    ]);
    expect(firstLookMarkdownPath("r1")).toBe(`${ROOT}/export.md`);
  });

  it("builds a host's client over its own request and routes", async () => {
    const calls: [string, ApiRequestInit | undefined][] = [];
    const api = createFirstLookApi(
      async <T,>(path: string, init?: ApiRequestInit) => {
        calls.push([path, init]);
        return {} as T;
      },
      (id) => `/snapshots/${id}/report`,
    );
    await api.getFirstLook("s1");
    await api.markFirstLookItem("s1", "a", { status: "ack", reason: "known" });
    expect(calls).toEqual([
      ["/snapshots/s1/report", undefined],
      [
        "/snapshots/s1/report/items/a/mark",
        { method: "PUT", body: '{"status":"ack","reason":"known"}' },
      ],
    ]);
    expect(api.firstLookMarkdownPath("s1")).toBe("/snapshots/s1/report/export.md");
  });
});
