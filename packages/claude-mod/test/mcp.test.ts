import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  callTool,
  fetchFileContext,
  LENS_TOOLS,
  mcpReady,
  resetMcp,
  warmMcp,
} from "../src/data/mcp";
import type { McpToolResult } from "../src/mod-api";
import { fakeHost, fixture, mcpResult } from "./fake-host";

/** Recorded through Lens in a real session on the requests index. */
const sessions = JSON.parse(fixture("mcp/get_context-sessions.json")) as McpToolResult;
const models = JSON.parse(fixture("mcp/get_context-models.json")) as McpToolResult;

beforeEach(() => resetMcp());
afterEach(() => vi.useRealTimers());

/** Resolves the server name the way a session does, before any call. */
async function warmed(host: ReturnType<typeof fakeHost>): Promise<typeof host> {
  warmMcp(host);
  await new Promise((resolve) => setTimeout(resolve, 0));
  return host;
}

describe("mcpReady", () => {
  it("waits for the server name, then says whether it resolved", async () => {
    expect(await mcpReady(fakeHost())).toBe(true);
    expect(await mcpReady(fakeHost())).toBe(true);
    resetMcp();
    expect(await mcpReady(fakeHost({ connected: false }))).toBe(false);
  });

  it("gives up on a server that never answers, after its timeout", async () => {
    const host = fakeHost();
    host.mcp.server = () => new Promise(() => undefined);
    expect(await mcpReady(host, 10)).toBe(false);
  });
});

describe("callTool", () => {
  it("calls the server under the name the engine resolved, and returns `result`", async () => {
    const host = await warmed(fakeHost({ server: "plugin_repowise_repowise", mcp: () => mcpResult({ ok: 1 }) }));
    expect(await callTool(host, "get_context", { targets: ["a.py"] })).toEqual({ ok: 1 });
    expect(host.calls.mcp).toEqual([{ server: "plugin_repowise_repowise", tool: "get_context", args: { targets: ["a.py"] } }]);
  });

  it("starts the call synchronously, inside the caller's hook", async () => {
    const host = await warmed(fakeHost({ mcp: () => new Promise<McpToolResult>(() => undefined) }));
    void callTool(host, "get_context", {}).catch(() => undefined);
    expect(host.calls.mcp).toHaveLength(1);
  });

  it("never starts a call before the name is resolved: it rejects and starts resolving", async () => {
    const host = fakeHost({ mcp: () => mcpResult({}) });
    await expect(callTool(host, "get_context", {})).rejects.toThrow("not resolved yet");
    expect(host.calls.mcp).toEqual([]);
    expect(host.calls.connect).toBe(1);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(await callTool(host, "get_context", {})).toEqual({});
  });

  it("resolves the server name once per session", async () => {
    const host = await warmed(fakeHost({ mcp: () => mcpResult({}) }));
    warmMcp(host);
    await callTool(host, "get_context", {});
    expect(host.calls.connect).toBe(1);
    resetMcp();
    await warmed(host);
    expect(host.calls.connect).toBe(2);
  });

  it("asks again after the server did not connect", async () => {
    const host = await warmed(fakeHost({ connected: false }));
    await expect(callTool(host, "get_context", {})).rejects.toThrow("not resolved yet");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(host.calls.connect).toBe(2);
    expect(host.calls.mcp).toEqual([]);
  });

  it("never calls a server that is not this plugin's, even when connect names it", async () => {
    const host = await warmed(fakeHost({ server: "repowise", mcp: () => mcpResult({}) }));
    await expect(callTool(host, "get_context", {})).rejects.toThrow("not resolved yet");
    expect(host.calls.mcp).toEqual([]);
  });

  it("reads a bare result the way a plain MCP client sees it", async () => {
    const host = await warmed(
      fakeHost({ mcp: () => ({ content: [{ type: "text", text: JSON.stringify({ targets: {} }) }], isError: false }) }),
    );
    expect(await callTool(host, "get_context", {})).toEqual({ targets: {} });
  });

  it("rejects a tool error with its text", async () => {
    const host = await warmed(fakeHost({ mcp: () => mcpResult("bad target", true) }));
    await expect(callTool(host, "get_context", {})).rejects.toThrow('get_context failed: "bad target"');
  });

  it("rejects a result with no text block", async () => {
    const host = await warmed(fakeHost({ mcp: () => ({ content: [], isError: false }) }));
    await expect(callTool(host, "get_context", {})).rejects.toThrow("get_context returned no text");
  });

  it("does not retry a failed call", async () => {
    const host = await warmed(
      fakeHost({
        mcp: () => {
          throw new Error('no connected MCP tool "get_context"');
        },
      }),
    );
    await expect(callTool(host, "get_context", {})).rejects.toThrow("no connected MCP tool");
    expect(host.calls.mcp).toHaveLength(1);
  });

  it("times out", async () => {
    const host = await warmed(fakeHost({ mcp: () => new Promise<McpToolResult>(() => undefined) }));
    vi.useFakeTimers();
    const call = callTool(host, "get_context", {}, { timeoutMs: 50 });
    const settled = expect(call).rejects.toThrow("get_context timed out after 50 ms");
    await vi.advanceTimersByTimeAsync(60);
    await settled;
  });
});

describe("fetchFileContext", () => {
  it("reads caller files and contributors from a recorded card", async () => {
    const host = await warmed(fakeHost({ mcp: () => sessions }));
    expect(await fetchFileContext(host, "src/requests/sessions.py")).toEqual({
      callerFiles: 3,
      contributors: 6,
      hotspot: false,
      recentOwner: { name: "Joren Hammudoglu", share: 0.5 },
    });
    expect(host.calls.mcp[0]?.args).toEqual({ targets: ["src/requests/sessions.py"], include: ["callers", "ownership"] });
  });

  it("counts every caller file when the list was capped", async () => {
    const host = await warmed(
      fakeHost({
        mcp: () => mcpResult({ targets: { "q.py": { callers: [{}, {}], callers_total: 41, ownership: { contributor_count: 3 } } } }),
      }),
    );
    expect(await fetchFileContext(host, "q.py")).toEqual({ callerFiles: 41, contributors: 3, hotspot: null, recentOwner: null });
  });

  it("keeps an unreported count unknown rather than zero", async () => {
    const host = await warmed(fakeHost({ mcp: () => mcpResult({ targets: { "q.py": { type: "file" } } }) }));
    expect(await fetchFileContext(host, "q.py")).toEqual({ callerFiles: null, contributors: null, hotspot: null, recentOwner: null });
  });

  it("is null for a file the index does not know", async () => {
    const host = await warmed(fakeHost({ mcp: () => mcpResult({ targets: { "x.py": { error: "Target not found: 'x.py'" } } }) }));
    expect(await fetchFileContext(host, "x.py")).toBeNull();
    resetMcp();
    expect(await fetchFileContext(await warmed(fakeHost({ mcp: () => models })), "elsewhere.py")).toBeNull();
  });
});

describe("LENS_TOOLS", () => {
  it("lists only the read-only tools a shipped feature calls", () => {
    expect(LENS_TOOLS).toEqual(["get_context", "get_change_risk", "get_why", "get_answer"]);
  });
});
