import { describe, expect, it } from "vitest";
import {
  callTool,
  isOwnReadOnlyCall,
  mcp,
  mcpServerName,
  mcpToolName,
  READ_ONLY_TOOLS,
  type Clock,
} from "../src/data/mcp";
import { TimeoutError } from "../src/data/transport";
import { fakeHost, mcpText } from "./fake-host";

/** A clock the test moves: each sleep advances it. */
function steppingClock(start = 0): Clock & { t: number } {
  const c = {
    t: start,
    now: () => c.t,
    sleep: async (ms: number) => {
      c.t += ms;
    },
  };
  return c;
}

describe("names", () => {
  it("spells this plugin's server and tool names", () => {
    expect(mcpServerName("repowise")).toBe("plugin:repowise:repowise");
    expect(mcpToolName("repowise", "get_context")).toBe("mcp__plugin_repowise_repowise__get_context");
  });
});

describe("callTool", () => {
  it("returns the parsed result", async () => {
    const h = fakeHost({ mcp: () => mcpText({ workspace: false, repos: [] }) });
    expect(await mcp.listRepos(h, { startedAt: 0 })).toEqual({ workspace: false, repos: [] });
  });

  it("passes typed arguments through", async () => {
    const seen: unknown[] = [];
    const h = fakeHost({
      mcp: (tool, args) => {
        seen.push([tool, args]);
        return mcpText({});
      },
    });
    const o = { startedAt: 0 };
    await mcp.getContext(h, { targets: ["a.py"] }, o);
    await mcp.getChangeRisk(h, {}, o);
    await mcp.getWhy(h, { query: "why" }, o);
    await mcp.getAnswer(h, { question: "how" }, o);
    expect(seen).toEqual([
      ["get_context", { targets: ["a.py"] }],
      ["get_change_risk", {}],
      ["get_why", { query: "why" }],
      ["get_answer", { question: "how" }],
    ]);
  });

  it("retries while the server connects, within the first seconds of the session", async () => {
    let n = 0;
    const h = fakeHost({
      mcp: (tool) => {
        if (++n < 4) throw new Error(`$.mcp.call: no connected MCP tool "${tool}"`);
        return mcpText("ready");
      },
    });
    expect(await callTool(h, "list_repos", {}, { startedAt: 0, clock: steppingClock() })).toBe("ready");
    expect(n).toBe(4);
  });

  it("gives up on a missing server once the connect window has passed", async () => {
    const h = fakeHost();
    await expect(callTool(h, "list_repos", {}, { startedAt: 0, clock: steppingClock() })).rejects.toThrow(/no connected/);
    expect(h.calls.mcp.length).toBe(11);
    await expect(callTool(fakeHost(), "list_repos", {}, { startedAt: 0, clock: steppingClock(6_000) })).rejects.toThrow();
  });

  it("does not retry other errors", async () => {
    const h = fakeHost({
      mcp: () => {
        throw new Error("denied");
      },
    });
    await expect(callTool(h, "get_why", {}, { startedAt: 0, clock: steppingClock() })).rejects.toThrow("denied");
    expect(h.calls.mcp.length).toBe(1);
  });

  it("times out", async () => {
    const h = fakeHost({ mcp: () => new Promise(() => {}) });
    await expect(callTool(h, "get_answer", {}, { startedAt: 0, timeoutMs: 5 })).rejects.toBeInstanceOf(TimeoutError);
  });

  it.each([
    [{ content: [{ type: "text", text: "boom" }], isError: true }, /boom/],
    [{ content: [], isError: true }, /reported an error/],
    [{ content: [{ type: "image" }], isError: false }, /no text/],
    [{ content: [{ type: "text", text: "not json" }], isError: false }, /not JSON/],
    [{ content: [{ type: "text", text: '{"other":1}' }], isError: false }, /no result/],
  ])("rejects a result it cannot read", async (raw, message) => {
    const h = fakeHost({ mcp: () => raw });
    await expect(callTool(h, "get_context", {}, { startedAt: 0 })).rejects.toThrow(message);
  });
});

describe("isOwnReadOnlyCall", () => {
  const own = (tool: string, id = "toolu_plugin_01") => ({ tool, tool_use_id: id });

  it("approves each allowlisted tool when Lens itself made the call", () => {
    for (const tool of READ_ONLY_TOOLS) {
      expect(isOwnReadOnlyCall(own(mcpToolName("repowise", tool)), "repowise", "repowise")).toBe(true);
    }
  });

  it("never approves Claude's call, even to the same tool", () => {
    expect(isOwnReadOnlyCall(own(mcpToolName("repowise", "get_context"), "toolu_01abc"), "engine", "repowise")).toBe(false);
  });

  it("never approves another plugin's call", () => {
    expect(isOwnReadOnlyCall(own(mcpToolName("repowise", "get_context")), "other-plugin", "repowise")).toBe(false);
  });

  it("never approves a tool outside the allowlist from its own plugin", () => {
    for (const tool of ["mcp__plugin_repowise_repowise__get_health", "mcp__plugin_repowise_repowise__get_dead_code", "Bash", "Edit"]) {
      expect(isOwnReadOnlyCall(own(tool), "repowise", "repowise")).toBe(false);
    }
  });

  it("never approves the same tool name on another server", () => {
    expect(isOwnReadOnlyCall(own("mcp__plugin_other_repowise__get_context"), "repowise", "repowise")).toBe(false);
    expect(isOwnReadOnlyCall(own("mcp__repowise__get_context"), "repowise", "repowise")).toBe(false);
  });
});
