import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { MinimalRequestInit, MinimalResponse } from "@repowise-dev/api-client";
import type { Host } from "../src/host";
import type { McpToolResult, ProcessRunResult } from "../src/mod-api";

export function fixture(name: string): string {
  return readFileSync(resolve(__dirname, "fixtures", name), "utf8");
}

/**
 * A golden line shared with the Python tests (tests/fixtures/lens): they
 * assert the real formatters write it, these assert Lens reads it.
 */
export function golden(name: string): string {
  return readFileSync(resolve(__dirname, "../../../tests/fixtures/lens", name), "utf8");
}

type HttpRoute = (url: string, init: MinimalRequestInit) => MinimalResponse | Promise<MinimalResponse>;

export interface FakeHostOptions {
  cwd?: string;
  /** Files by path; `/` and `\` are the same separator. */
  files?: Record<string, string>;
  run?: ((argv: readonly string[], cwd: string) => ProcessRunResult | Promise<ProcessRunResult>) | undefined;
  http?: HttpRoute;
  /** Whether the MCP server connects; default true. */
  connected?: boolean;
  /** The name the server connects under; default `plugin:repowise:repowise`. */
  server?: string;
  /** Answers MCP tool calls; default: rejects. */
  mcp?: (tool: string, args: Record<string, unknown>, server: string) => McpToolResult | Promise<McpToolResult>;
}

const norm = (p: string) => p.replace(/\\/g, "/");

export interface FakeHost extends Host {
  calls: { run: string[][]; http: string[]; connect: number; mcp: Array<{ server: string; tool: string; args: Record<string, unknown> }> };
}

export function fakeHost(o: FakeHostOptions = {}): FakeHost {
  const files = new Map(Object.entries(o.files ?? {}).map(([k, v]) => [norm(k), v]));
  const calls: FakeHost["calls"] = { run: [], http: [], connect: 0, mcp: [] };
  return {
    calls,
    session: { cwd: async () => o.cwd ?? "C:\\work\\requests" },
    fs: {
      read: async (path) => {
        const hit = files.get(norm(path));
        if (hit === undefined) throw new Error(`ENOENT ${path}`);
        return hit;
      },
      exists: async (path) => files.has(norm(path)),
    },
    process: {
      run: async (argv, cwd) => {
        calls.run.push([...argv]);
        if (!o.run) throw new Error("process API unavailable");
        return o.run(argv, cwd);
      },
    },
    http: async (url, init) => {
      calls.http.push(url);
      if (!o.http) throw new Error("connection refused");
      return o.http(url, init);
    },
    mcp: {
      connect: async () => {
        calls.connect++;
        return o.connected ?? true;
      },
      server: async () => {
        calls.connect++;
        return (o.connected ?? true) ? (o.server ?? "plugin:repowise:repowise") : null;
      },
      call: async (server, tool, args) => {
        calls.mcp.push({ server, tool, args });
        if (!o.mcp) throw new Error("no connected MCP tool");
        return o.mcp(tool, args, server);
      },
    },
  };
}

export const ok = (stdout: string): ProcessRunResult => ({ exitCode: 0, stdout, stderr: "" });
export const failed: ProcessRunResult = { exitCode: 1, stdout: "", stderr: "fatal" };

export function json(status: number, body: unknown): MinimalResponse {
  return { status, ok: status >= 200 && status < 300, headers: {}, text: JSON.stringify(body) };
}

/** An MCP result carrying `result` the way the repowise server wraps it. */
export function mcpResult(result: unknown, isError = false): McpToolResult {
  return { content: [{ type: "text", text: JSON.stringify(isError ? result : { result }) }], isError };
}
