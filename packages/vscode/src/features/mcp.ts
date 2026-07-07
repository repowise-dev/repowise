import * as vscode from "vscode";
import * as path from "node:path";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { Commands } from "../constants";
import type { RepowiseContext } from "../core/context";

/**
 * Provider id contributed in package.json under `mcpServerDefinitionProviders`.
 * The registration id MUST equal the contributed id or VS Code ignores it.
 */
const PROVIDER_ID = "repowise.mcp";

/**
 * Description string written into every repowise MCP registration. This is a
 * verbatim copy of the CLI's `generate_mcp_config` description so the extension
 * and `repowise init` write byte-identical entries and never overwrite each
 * other on re-run. Do not reword it (the em dash is intentional parity).
 */
const MCP_DESCRIPTION =
  "repowise: codebase intelligence — docs, graph, git signals, dead code, decisions";

/** Absolute repo path with forward slashes, matching the CLI's arg convention. */
function forwardSlashes(p: string): string {
  return path.resolve(p).replace(/\\/g, "/");
}

/** The stdio args the CLI writes: `mcp <abs-repo-path> --transport stdio`. */
function mcpArgs(repoPath: string): string[] {
  return ["mcp", forwardSlashes(repoPath), "--transport", "stdio"];
}

/**
 * MCP integration: registers an MCP server definition provider so AI agents in
 * the editor can reach the Repowise tools (via `repowise mcp <repo-path>` over
 * stdio). Owns the workspace MCP configuration command.
 */
export function registerMcp(ctx: RepowiseContext): vscode.Disposable {
  // Fired when the resolved repo root may have changed, so VS Code re-queries
  // the provider. Kept cheap: one emitter, driven by workspace-folder changes.
  const didChange = new vscode.EventEmitter<void>();

  const provider: vscode.McpServerDefinitionProvider = {
    onDidChangeMcpServerDefinitions: didChange.event,
    // Called eagerly by VS Code. Must not prompt, spawn, or hit the network.
    provideMcpServerDefinitions: () => {
      const repoRoot = ctx.workspace.repoRoot;
      if (!repoRoot) return [];
      const command = ctx.config.cliPath() || "repowise";
      const def = new vscode.McpStdioServerDefinition(
        "Repowise",
        command,
        mcpArgs(repoRoot),
      );
      def.cwd = vscode.Uri.file(repoRoot);
      return [def];
    },
  };

  const disposables: vscode.Disposable[] = [
    vscode.lm.registerMcpServerDefinitionProvider(PROVIDER_ID, provider),
    didChange,
    vscode.workspace.onDidChangeWorkspaceFolders(() => didChange.fire()),
    vscode.commands.registerCommand(Commands.configureMcp, () =>
      configureMcp(ctx),
    ),
  ];

  return vscode.Disposable.from(...disposables);
}

/**
 * Writes or merges `<firstWorkspaceFolder>/.vscode/mcp.json` with the repowise
 * server entry, preserving foreign servers and any user-added keys (an `env`
 * block carrying provider keys survives re-registration). Idempotent. A file
 * that is not strict JSON (VS Code allows JSONC comments) is left untouched;
 * the user is shown the snippet to add by hand.
 */
async function configureMcp(ctx: RepowiseContext): Promise<void> {
  if (!vscode.workspace.isTrusted) {
    void vscode.window.showWarningMessage(
      "Repowise cannot write .vscode/mcp.json in a restricted workspace. " +
        "Trust this workspace, then run the command again.",
    );
    return;
  }

  const firstFolder = vscode.workspace.workspaceFolders?.[0];
  if (!firstFolder) {
    void vscode.window.showWarningMessage(
      "Open a folder before configuring the Repowise MCP server.",
    );
    return;
  }

  // The file lives in the open workspace folder (that is where VS Code reads
  // workspace MCP config). The server path points at the indexed repo root when
  // known; before indexing we fall back to the folder itself.
  const folderRoot = firstFolder.uri.fsPath;
  const repoRoot = ctx.workspace.repoRoot;
  const pathForArgs = repoRoot ?? folderRoot;

  // Byte-shape-identical to the CLI's `.vscode/mcp.json` entry: bare `repowise`
  // command (repo-shared config may be committed, so no absolute per-user path).
  const entry = {
    type: "stdio",
    command: "repowise",
    args: mcpArgs(pathForArgs),
    description: MCP_DESCRIPTION,
  };

  const configPath = path.join(folderRoot, ".vscode", "mcp.json");
  const configUri = vscode.Uri.file(configPath);

  let existing: Record<string, unknown> | null = null;
  let raw: string | null = null;
  try {
    raw = readFileSync(configPath, "utf8");
  } catch {
    raw = null; // File does not exist yet; we will create it.
  }

  if (raw !== null) {
    try {
      const parsed: unknown = JSON.parse(raw);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        throw new Error("not a JSON object");
      }
      existing = parsed as Record<string, unknown>;
    } catch {
      // Strict parse failed (likely JSONC comments). Never rewrite: show the
      // snippet so the user can merge it themselves.
      await showManualSnippet(entry);
      return;
    }
  }

  const merged: Record<string, unknown> = existing ? { ...existing } : {};
  const priorServersValue = merged["servers"];
  const priorServers: Record<string, unknown> =
    typeof priorServersValue === "object" &&
    priorServersValue !== null &&
    !Array.isArray(priorServersValue)
      ? { ...(priorServersValue as Record<string, unknown>) }
      : {};

  // Deep-merge into the repowise entry: generated fields overwrite, but any
  // user-added keys (e.g. env) are preserved. Mirrors the CLI merge semantics.
  const priorEntryValue = priorServers["repowise"];
  const priorEntry: Record<string, unknown> =
    typeof priorEntryValue === "object" &&
    priorEntryValue !== null &&
    !Array.isArray(priorEntryValue)
      ? (priorEntryValue as Record<string, unknown>)
      : {};
  priorServers["repowise"] = { ...priorEntry, ...entry };
  merged["servers"] = priorServers;

  try {
    mkdirSync(path.dirname(configPath), { recursive: true });
    writeFileSync(configPath, JSON.stringify(merged, null, 2) + "\n", "utf8");
  } catch (err) {
    ctx.log.error(`configureMcp write failed: ${String(err)}`);
    void vscode.window.showErrorMessage(
      "Could not write .vscode/mcp.json. See the Repowise log for details.",
    );
    return;
  }

  const note = repoRoot
    ? ""
    : " The repo is not indexed yet, so the server points at the workspace folder; run repowise init to index it.";
  const choice = await vscode.window.showInformationMessage(
    `Repowise MCP server configured in .vscode/mcp.json.${note}`,
    "Open File",
  );
  if (choice === "Open File") {
    const doc = await vscode.workspace.openTextDocument(configUri);
    await vscode.window.showTextDocument(doc);
  }
}

/** Shows the mcp.json snippet with a clipboard button for JSONC/parse cases. */
async function showManualSnippet(entry: Record<string, unknown>): Promise<void> {
  const snippet = JSON.stringify({ servers: { repowise: entry } }, null, 2);
  const choice = await vscode.window.showWarningMessage(
    ".vscode/mcp.json is not strict JSON (it may contain comments), so it was " +
      'left unchanged. Add a "repowise" server under "servers" manually.',
    "Copy snippet",
  );
  if (choice === "Copy snippet") {
    await vscode.env.clipboard.writeText(snippet);
  }
}
