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

export interface McpToolResult {
  content: Array<{ type: string; text?: string }>;
  isError: boolean;
}

export interface RenderEvent {
  props: { hasSurvey?: boolean; bodyColumns?: number };
}

/** A `Pane` render: the pane's id is `requestId`. */
export interface PaneRenderEvent {
  surface?: string;
  requestId: string;
  props: { bodyColumns: number; placement: "dock" | "inline"; scroll: { bodyRows: number } };
}

export type UiOpenResult = { isPlaced: true } | { isPlaced: false; reason: string };

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
  mcp: {
    connect(server: string): Promise<{ isConnected: boolean; server?: string }>;
    call(server: string, tool: string, args?: Record<string, unknown>): Promise<McpToolResult>;
  };
  prompt: {
    /** A visible prompt, run once the session is idle. */
    submit(input: { text: string }): Promise<unknown>;
  };
  ui: {
    invalidate(event: "ui.render"): void;
    /** `transcript`: a dim row of its own; `debug`: the debug log alone. */
    log(text: string, options: { to: "debug" | "transcript" }): void;
    resolve(e: { props: object }): Record<string, (props: Record<string, unknown>) => unknown>;
    /** `focus`: the pane takes the keyboard (only when the person asked for it). */
    open(pane: { id: string; title?: string; rows?: number; focus?: boolean }): Promise<UiOpenResult>;
    close(pane: { id: string }): Promise<void>;
    blit(args: { requestId: string; key: string; cells: string; columns: number; rows: number }): Promise<{ deny?: string }>;
  };
  command: {
    register(command: { name: string; description: string; argumentHint?: string; immediate?: true }): Promise<unknown>;
  };
  settings: { read(): Promise<Readonly<Record<string, unknown>>> };
  /** The `/config` menu's rows; Lens reads only `theme`. */
  config: { list(): Promise<ReadonlyArray<{ key: string; value: unknown }>> };
}

export interface SessionStartEvent {
  cwd?: string;
}

/**
 * The conversation ending. `clear` is a `/clear`: the process goes on under a
 * new session id and no `session.start` fires for it.
 */
export interface SessionEndEvent {
  reason?: "clear" | "resume" | "logout" | "prompt_input_exit" | "other";
  sessionId?: string;
}

/** A prompt's turn starting; `text` is "" for one started without a typed prompt. */
export interface TurnStartEvent {
  text?: string;
  turnId?: string;
}

export interface TurnCompleteEvent {
  agentId?: string;
  durationMs?: number;
  /** Claude's final text this turn. */
  answer?: string;
  reason?: "answer" | "aborted" | "refusal" | "error";
  isAborted?: boolean;
}

/** A tool call as `tool.call` sees it: the tool's own arguments ride beside these keys. */
export interface ToolCallEvent {
  tool: string;
  tool_use_id: string;
  agentId?: string;
  file_path?: unknown;
  /** The tool's other arguments. */
  [arg: string]: unknown;
}

export interface SpinnerEvent {
  props: { word: string; message: string | null; suffix: string; mode: string };
}

export interface ToolResultEvent {
  props: { tool_use_id: string; tool: string; output: unknown; isErrored: boolean };
}

export interface ToolUseEvent {
  props: { tool_use_id: string; tool: string; input: unknown; isRunning: boolean; isErrored: boolean };
}

export interface PostToolUseEvent {
  tool_name: string;
  tool_use_id: string;
  tool_input: unknown;
}

/** `/lens <args>`: everything after the name, as typed. */
export interface CommandRunEvent {
  args?: string;
}

/** A change of, or Enter in, an `Input` a render hook drew. */
export interface UiInputEvent {
  plugin: string;
  element: string;
  kind: "change" | "submit";
  value: string;
}

/** After the conversation was compacted; Lens reads nothing of it. */
export interface PostCompactEvent {
  trigger?: "manual" | "auto";
}

export type Hook<E> = ($: ModApi, e: E, next: (e: E) => Promise<unknown>) => Promise<unknown>;

/** The plugin's `userConfig` values, fixed for one activation. */
export type PluginOptions = Readonly<Record<string, string | number | boolean | readonly string[]>>;

export interface On {
  (event: "session.start", hook: Hook<SessionStartEvent>): unknown;
  (event: "session.end", hook: Hook<SessionEndEvent>): unknown;
  (event: "turn.complete", hook: Hook<TurnCompleteEvent>): unknown;
  (event: "turn.start", hook: Hook<TurnStartEvent>): unknown;
  (event: "ui.render", matcher: { component: "AbovePrompt" }, hook: Hook<RenderEvent>): unknown;
  (event: "tool.call", hook: Hook<ToolCallEvent>): unknown;
  (event: "ui.render", matcher: { component: "Spinner" }, hook: Hook<SpinnerEvent>): unknown;
  (event: "ui.render", matcher: { component: "ToolResult" }, hook: Hook<ToolResultEvent>): unknown;
  (event: "ui.render", matcher: { component: "ToolUse" }, hook: Hook<ToolUseEvent>): unknown;
  /** `next(e)` resolves to the settings hooks' folded result, `{ additionalContext: string[] }`. */
  (event: "classic.PostToolUse", hook: Hook<PostToolUseEvent>): unknown;
  (event: "ui.render", matcher: { component: "Pane" }, hook: Hook<PaneRenderEvent>): unknown;
  (event: "command.run", matcher: { command: string }, hook: Hook<CommandRunEvent>): unknown;
  (event: "ui.input", matcher: { plugin: string; element: string }, hook: Hook<UiInputEvent>): unknown;
  (event: "classic.PostCompact", hook: Hook<PostCompactEvent>): unknown;
}
