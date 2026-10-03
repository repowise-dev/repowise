/**
 * What Lens may do, as closures over `$`. register.ts builds the one real
 * Host; tests build fakes. Nothing outside register.ts sees `$`.
 */

import type { MinimalFetch } from "@repowise-dev/api-client";
import type { McpToolResult, ProcessRunResult } from "./mod-api";

export interface Host {
  /** This plugin's name from plugin.json, as `next.origin.plugin` reports it. */
  pluginName: string;
  session: { cwd(): Promise<string> };
  fs: {
    /** Rejects when the file is missing. */
    read(path: string): Promise<string>;
    exists(path: string): Promise<boolean>;
  };
  process: {
    /** Read-only commands only (git queries, a pid probe). Rejects off the CLI. */
    run(argv: readonly string[], cwd: string): Promise<ProcessRunResult>;
  };
  http: MinimalFetch;
  /** This plugin's own repowise MCP server. */
  mcp: {
    call(tool: string, args: Record<string, unknown>): Promise<McpToolResult>;
    /** Waits for the server to connect; false when it cannot (the CLI is not installed). */
    connect(): Promise<boolean>;
  };
}
