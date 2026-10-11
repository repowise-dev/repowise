/**
 * What Lens may do, as closures over `$`. register.ts builds one from each
 * hook's own `$`; tests build fakes. Nothing outside register.ts sees `$`.
 */

import type { MinimalFetch } from "@repowise-dev/api-client";
import type { McpToolResult, ProcessRunResult } from "./mod-api";

export interface Host {
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
    /** Waits for the server to connect; false when it cannot (the CLI is not installed). */
    connect(): Promise<boolean>;
    /** The name `call` takes for the server, or null when it does not connect. */
    server(): Promise<string | null>;
    call(server: string, tool: string, args: Record<string, unknown>): Promise<McpToolResult>;
  };
}
