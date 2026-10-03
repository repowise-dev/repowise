/**
 * The slice of the Claude Code mods API that register.ts uses, typed locally.
 *
 * Claude Code writes the full declarations (`claude-code`) into a plugin dir
 * when it loads the mod, so they do not exist where `claude` is not installed.
 * This file names only what Lens calls. It is checked against the real
 * surface locally, by `npm run test:mod` and `claude plugin validate --strict`
 * on the built bundle; CI runs neither. Extend it with each new call.
 */

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

export interface RenderEvent {
  props: { hasSurvey?: boolean; bodyColumns?: number };
}

export interface ModApi {
  session: { cwd(): Promise<string> };
  fs: {
    read(path: string): Promise<string>;
    exists(path: string): Promise<boolean>;
  };
  process: {
    run(argv: readonly string[], init: { cwd: string; timeoutMs: number }): Promise<ProcessRunResult>;
  };
  http: {
    fetch(url: string, init: { method?: string; headers?: Record<string, string>; body?: string }): Promise<HttpResponse>;
  };
  mcp: { connect(server: string): Promise<{ isConnected: boolean }> };
  ui: {
    invalidate(event: "ui.render"): void;
    log(text: string, options: { to: "debug" }): void;
    resolve(e: RenderEvent): Record<string, (props: Record<string, unknown>) => unknown>;
  };
}

export interface TurnCompleteEvent {
  agentId?: string;
}

export type Hook<E> = ($: ModApi, e: E, next: (e: E) => Promise<unknown>) => Promise<unknown>;

export interface On {
  (event: "session.start", hook: Hook<unknown>): unknown;
  (event: "turn.complete", hook: Hook<TurnCompleteEvent>): unknown;
  (event: "ui.render", matcher: { component: "AbovePrompt" }, hook: Hook<RenderEvent>): unknown;
}
