/**
 * Repowise for Pi — the Claude Code hook behaviours, as a Pi extension.
 *
 * Reproduces the five hook behaviours in `plugins/claude-code/hooks/hooks.json`
 * through Pi's event API, calling the same helper CLIs:
 *
 * | Claude Code hook                  | Pi event            | Helper           |
 * | --------------------------------- | ------------------- | ---------------- |
 * | SessionStart (startup/resume/clear)| before_agent_start* | repowise-augment |
 * | PreToolUse (Bash)                 | tool_call           | repowise-rewrite |
 * | PostToolUse (read/edit/grep/...)  | tool_result         | repowise-augment |
 * | PostToolUse (Bash + `git commit`) | tool_result         | repowise-augment |
 * | PostToolUseFailure                | tool_result (error) | repowise-augment |
 *
 * \* `agent_start` cannot inject context — its handlers have no return
 * contract — so session-start injection runs in `before_agent_start`,
 * which can return a message, gated to the first user run of the session
 * (the `session_start` handler resets the gate, so resume/clear re-inject
 * exactly like Claude Code's `startup|resume|clear` matcher).
 *
 * The helpers speak the same `hookSpecificOutput` JSON the Claude Code
 * hooks read. The extension parses that shape and translates it into
 * Pi's: `additionalContext` becomes an appended text block, and
 * `updatedToolOutput` becomes the new result content (a replacement drops
 * the structured content, per Pi's `tool_result` contract).
 *
 * Graceful degradation: a missing helper (not on PATH) disables that
 * surface for the session — the turn never breaks, the tool runs
 * unchanged, and no error reaches the transcript. A helper that exits
 * non-zero or times out is the same as one that said nothing, except at
 * session start: an answered helper — even with no context — marks the
 * session so it is not re-spawned every turn, while a failed one is
 * retried on the next turn. A missing `cwd` never marks a helper
 * missing: only a binary that cannot be found on PATH does.
 *
 * Lens UI: NOT ported. `lens.js` is built on the Claude Code SDK UI
 * surface and Pi's equivalent is its TUI component API — a port is its
 * own issue. The seven `lens_*` toggles below are still reachable from
 * Pi configuration (a `repowise` block in `settings.json`, or the
 * extension's own `repowise.json`), parsed and validated on load, and
 * reserved for that port; today they gate nothing in Pi, and the README
 * says so rather than pretending otherwise.
 *
 * No live Pi session was available when this shipped: the extension is
 * type-checked against the real `@earendil-works/pi-coding-agent`
 * package and exercised through the Python helpers' mocked tests, but
 * the end-to-end behaviour (injection, distillation, decision capture,
 * failure rescue) is confirmed by observation only — see README.
 */

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

import {
  isToolCallEventType,
  type ExtensionAPI,
  type ExtensionContext,
} from "@earendil-works/pi-coding-agent";

/** Tool names the extension routes to `repowise-augment`. */
const READ_TOOLS = new Set(["read"]);
const EDIT_TOOLS = new Set(["edit", "write"]);
const SEARCH_TOOLS = new Set(["grep", "find"]);
const SHELL_TOOLS = new Set(["bash", "powershell"]);

const AUGMENT_TIMEOUT_MS = 10_000;
const REWRITE_TIMEOUT_MS = 5_000;

const AUGMENT_CLI = "repowise-augment";
const REWRITE_CLI = "repowise-rewrite";

/** The seven `lens_*` toggles, with the Claude Code plugin's defaults. */
const LENS_DEFAULTS = {
  lens_margin: true,
  lens_squeeze: true,
  lens_review: true,
  lens_flow: true,
  lens_pane_autoopen: false,
  lens_map_health: false,
} as const;

type LensSettings = { [K in keyof typeof LENS_DEFAULTS]: boolean };

type HookSpecificOutput = {
  additionalContext?: unknown;
  updatedToolOutput?: unknown;
};

type TextBlock = { type: "text"; text: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function textOf(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  const parts: string[] = [];
  for (const block of content) {
    if (isRecord(block) && typeof block.text === "string" && block.text) {
      parts.push(block.text);
    }
  }
  return parts.join("\n");
}

function parseHookOutput(raw: string): HookSpecificOutput | null {
  let data: unknown;
  try {
    data = JSON.parse(raw) as unknown;
  } catch {
    return null;
  }
  if (!isRecord(data)) return null;
  const specific = data.hookSpecificOutput;
  return isRecord(specific) ? (specific as HookSpecificOutput) : null;
}

/**
 * Outcome of one helper invocation. `answered` means the helper ran and
 * exited 0 — even if it said nothing. A spawn error, a non-zero exit, or
 * a timeout is `failed`, and the caller retries it on the next turn
 * rather than treating silence as an answer.
 */
type HelperResult =
  | { answered: true; output: HookSpecificOutput | null }
  | { answered: false };

/**
 * Run a helper CLI with a JSON payload on stdin, returning its parsed
 * `hookSpecificOutput`. An `answered` result with a null output means the
 * helper ran but said nothing — the caller treats that as "run the tool
 * unchanged", never as an error.
 */
async function runHelper(
  cli: string,
  args: string[],
  payload: Record<string, unknown>,
  timeoutMs: number,
  cwd: string,
  missing: Set<string>,
): Promise<HelperResult> {
  if (missing.has(cli)) return { answered: false };
  return new Promise<HelperResult>((resolve) => {
    let done = false;
    const finish = (value: HelperResult): void => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve(value);
    };
    const child = spawn(cli, args, { cwd, windowsHide: true });
    const timer = setTimeout(() => {
      child.kill();
      finish({ answered: false });
    }, timeoutMs);
    let out = "";
    child.stdout.on("data", (chunk: Buffer) => {
      out += chunk.toString("utf-8");
    });
    child.on("error", (error: NodeJS.ErrnoException) => {
      // Node reports ENOENT for a missing binary AND for a missing cwd.
      // Only a missing binary disables the helper: a bad cwd must not
      // poison the session, so the set is touched only when the
      // directory actually exists.
      if (error.code === "ENOENT" && existsSync(cwd)) missing.add(cli);
      finish({ answered: false });
    });
    child.on("close", (code) => {
      if (code !== 0) {
        finish({ answered: false });
        return;
      }
      finish({ answered: true, output: out.trim() ? parseHookOutput(out) : null });
    });
    child.stdin.on("error", () => finish({ answered: false }));
    child.stdin.write(JSON.stringify(payload));
    child.stdin.end();
  });
}

/**
 * The `lens_*` toggles, from Pi configuration.
 *
 * Precedence per key: the `repowise` block in `settings.json`
 * (`pi.getSettings()`), then the extension's own settings file
 * (`~/.pi/agent/repowise.json`, or `.pi/repowise.json` in a trusted
 * project), then the Claude Code plugin's defaults. A non-boolean value
 * is ignored for that key — the default stands, and the mistake is
 * logged where Pi's own diagnostics go rather than into the transcript.
 */
async function loadLensSettings(
  pi: ExtensionAPI,
  ctx: ExtensionContext,
): Promise<LensSettings> {
  const fromFile: Record<string, unknown> = {};
  const readJsonFile = async (path: string): Promise<void> => {
    try {
      const parsed: unknown = JSON.parse(await readFile(path, "utf-8"));
      if (isRecord(parsed)) Object.assign(fromFile, parsed);
    } catch {
      // Absent or unparsable: the other sources still stand.
    }
  };
  const agentDir =
    process.env.PI_CODING_AGENT_DIR?.replace(/^~/, homedir()) ??
    join(homedir(), ".pi", "agent");
  await readJsonFile(join(agentDir, "repowise.json"));
  if (ctx.isProjectTrusted()) {
    await readJsonFile(join(ctx.cwd, ".pi", "repowise.json"));
  }

  let fromSettings: Record<string, unknown> = {};
  try {
    const settings = pi.getSettings() as Record<string, unknown>;
    if (isRecord(settings.repowise)) fromSettings = settings.repowise;
  } catch {
    // No settings API on this Pi build: the file sources still stand.
  }

  const merged: Record<string, boolean> = {};
  for (const [key, fallback] of Object.entries(LENS_DEFAULTS)) {
    const value = fromSettings[key] ?? fromFile[key] ?? fallback;
    if (typeof value !== "boolean") {
      console.warn(`[repowise] lens setting ${key} must be a boolean; using ${fallback}`);
      merged[key] = fallback;
    } else {
      merged[key] = value;
    }
  }
  return merged as LensSettings;
}

function isRepowiseMcpTool(toolName: string): boolean {
  return toolName.startsWith("mcp__") && toolName.toLowerCase().includes("repowise");
}

export default function (pi: ExtensionAPI): void {
  /** Session id the context block was already injected for, else null. */
  let injectedSessionId: string | null = null;
  /** Helpers proven absent this session — never spawned again. */
  const missingHelpers = new Set<string>();
  /** Lens toggles, loaded once per session (see `loadLensSettings`). */
  let lensSettings: LensSettings | null = null;

  pi.on("session_start", async (_event, ctx) => {
    // A new load (or a resume) is a new SessionStart: re-inject on the
    // next user run, and re-probe for helpers the user may have installed
    // since the last session.
    injectedSessionId = null;
    missingHelpers.clear();
    lensSettings = await loadLensSettings(pi, ctx);
  });

  pi.on("before_agent_start", async (event, ctx) => {
    if (event.messageType !== "user") return;
    const sessionId = ctx.sessionManager.getSessionId();
    if (injectedSessionId === sessionId) return;
    const result = await runHelper(
      AUGMENT_CLI,
      ["--client", "pi"],
      { hook_event_name: "SessionStart", cwd: ctx.cwd, session_id: sessionId },
      AUGMENT_TIMEOUT_MS,
      ctx.cwd,
      missingHelpers,
    );
    // A failed helper is retried next turn; an answered one — even with
    // no context — marks the session, so an empty answer does not
    // re-spawn the helper on every user prompt.
    if (!result.answered) return;
    injectedSessionId = sessionId;
    const context = result.output?.additionalContext;
    if (typeof context !== "string" || !context) return;
    return {
      message: {
        customType: "repowise-context",
        content: context,
        display: false,
      },
    };
  });

  pi.on("tool_call", async (event, ctx) => {
    if (!isToolCallEventType("bash", event) && !isToolCallEventType("powershell", event)) {
      return;
    }
    const command = event.input.command;
    if (typeof command !== "string" || !command.trim()) return;
    const result = await runHelper(
      REWRITE_CLI,
      ["--agent", "pi"],
      {
        hook_event_name: "PreToolUse",
        tool_name: event.toolName,
        tool_input: { command },
        cwd: ctx.cwd,
        session_id: ctx.sessionManager.getSessionId(),
      },
      REWRITE_TIMEOUT_MS,
      ctx.cwd,
      missingHelpers,
    );
    const output = result.answered ? result.output : null;
    const updated = (output as { updatedInput?: unknown } | null)?.updatedInput;
    if (!isRecord(updated)) return;
    const rewritten = updated.command;
    if (typeof rewritten === "string" && rewritten && rewritten !== command) {
      // Pi has no ask-with-mutation: only `allow` rewrites reach here —
      // the helper already passed `ask` decisions through untouched.
      event.input.command = rewritten;
    }
  });

  pi.on("tool_result", async (event, ctx) => {
    const toolName = event.toolName;
    const routed =
      READ_TOOLS.has(toolName) ||
      EDIT_TOOLS.has(toolName) ||
      SEARCH_TOOLS.has(toolName) ||
      SHELL_TOOLS.has(toolName) ||
      isRepowiseMcpTool(toolName);
    if (!routed) return;

    const content = event.content;
    const payload: Record<string, unknown> = {
      hook_event_name: event.isError ? "PostToolUseFailure" : "PostToolUse",
      tool_name: toolName,
      tool_input: event.input,
      tool_response: { content },
      cwd: ctx.cwd,
      session_id: ctx.sessionManager.getSessionId(),
      is_error: event.isError,
    };
    if (event.isError) payload.error = textOf(content);
    const result = await runHelper(
      AUGMENT_CLI,
      ["--client", "pi"],
      payload,
      AUGMENT_TIMEOUT_MS,
      ctx.cwd,
      missingHelpers,
    );
    const output = result.answered ? result.output : null;
    if (!output) return;

    const current = Array.isArray(content) ? [...content] : [];
    let changed = false;
    let contextBlock: TextBlock | null = null;
    if (typeof output.additionalContext === "string" && output.additionalContext) {
      contextBlock = { type: "text", text: output.additionalContext };
      current.push(contextBlock);
      changed = true;
    }
    if (output.updatedToolOutput !== undefined) {
      const replacement =
        typeof output.updatedToolOutput === "string"
          ? output.updatedToolOutput
          : JSON.stringify(output.updatedToolOutput);
      // Replacing the content drops the structured content, per Pi's
      // `tool_result` contract — the replacement is the whole result now.
      // The context note is a separate block, so it is kept ahead of the
      // replacement: in Claude Code the two fields are not alternatives,
      // and the Read surface can legitimately want both (a stale-read
      // flag alongside a served skeleton). Only the note is kept — the
      // original content stays dropped, or a skeleton/digest replacement
      // would ship the full output it exists to condense.
      return {
        content: [
          ...(contextBlock ? [contextBlock] : []),
          { type: "text", text: replacement } satisfies TextBlock,
        ],
      };
    }
    if (changed) return { content: current };
    return undefined;
  });

  pi.registerCommand("repowise", {
    description: "Repowise status: helpers, session context, Lens toggles",
    handler: async (_args, ctx) => {
      const augment = missingHelpers.has(AUGMENT_CLI) ? "missing" : "available";
      const rewrite = missingHelpers.has(REWRITE_CLI) ? "missing" : "available";
      const injected =
        injectedSessionId === ctx.sessionManager.getSessionId()
          ? "injected this session"
          : "not injected yet";
      const lens = lensSettings
        ? Object.entries(lensSettings)
            .map(([key, value]) => `${key}=${value}`)
            .join(", ")
        : "not loaded yet";
      ctx.ui.notify(
        `Repowise: augment ${augment}, rewrite ${rewrite}; context ${injected}. Lens (Claude Code-only): ${lens}.`,
        "info",
      );
    },
  });
}
