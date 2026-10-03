/**
 * The slice of the Claude Code mods API that register.ts uses, typed locally.
 *
 * Claude Code writes the full declarations (`claude-code`) into a plugin dir
 * when it loads the mod, so they are not available where `claude` is not
 * installed, CI included. This file names only what Lens calls; it is checked
 * against the real surface by `claude plugin validate --strict` on the built
 * bundle. Extend it when register.ts starts calling something new.
 */

export interface McpContentBlock {
  type: string;
  text?: string;
}

export interface McpToolResult {
  content: McpContentBlock[];
  isError: boolean;
}

export interface ProcessRunResult {
  exitCode: number;
  stdout: string;
  stderr: string;
}

export interface HttpResponse {
  status: number;
  ok: boolean;
  headers: Record<string, string>;
  text: string;
}

/** An element as the surface's table builds it; opaque to Lens. */
export type ElementTree = unknown;
export type ElementFn = (props: Record<string, unknown>) => ElementTree;

export interface RenderEvent {
  component: string;
  surface: string;
  requestId?: string;
  props: { hasSurvey?: boolean; bodyColumns?: number; maxRows?: number };
}

export interface ModApi {
  plugin: { name: string; root: string };
  session: { cwd(): Promise<string> };
  fs: {
    read(path: string): Promise<string>;
    exists(path: string): Promise<boolean>;
  };
  process: {
    run(argv: readonly string[], init?: { cwd?: string; timeoutMs?: number }): Promise<ProcessRunResult>;
  };
  http: {
    fetch(
      url: string,
      init?: { method?: string; headers?: Record<string, string>; body?: string },
    ): Promise<HttpResponse>;
  };
  mcp: {
    call(server: string, tool: string, args?: Record<string, unknown>): Promise<McpToolResult>;
    connect(server: string): Promise<{ isConnected: boolean }>;
  };
  ui: {
    invalidate(event: "ui.render"): void;
    resolve(e: RenderEvent): Record<string, ElementFn>;
  };
}

/** `next` as a hook receives it: the rest of the chain, and who fired the event. */
export interface Next<E, R> {
  (e: E): Promise<R>;
  origin: { plugin: string; tier: string };
}

export interface ToolCheckEvent {
  tool: string;
  input: unknown;
  tool_use_id: string;
}

export type ToolCheckResult = { decision: "allow" | "ask" | "deny"; reason?: string };

export interface TurnCompleteEvent {
  turnId: string;
  agentId?: string;
  isAborted: boolean;
}

export type Hook<E, R> = ($: ModApi, e: E, next: Next<E, R>) => Promise<R>;

export interface On {
  (event: "session.start", hook: Hook<unknown, unknown>): unknown;
  (event: "turn.complete", hook: Hook<TurnCompleteEvent, unknown>): unknown;
  (event: "tool.check", hook: Hook<ToolCheckEvent, ToolCheckResult>): unknown;
  (event: "ui.render", matcher: { component: "AbovePrompt" }, hook: Hook<RenderEvent, unknown>): unknown;
}

