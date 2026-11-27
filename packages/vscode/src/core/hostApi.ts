import * as vscode from "vscode";

/**
 * Whether this host has the MCP server definition API. VS Code has it from
 * 1.101; forks installing from Open VSX (Cursor, Windsurf) can report a recent
 * engine version yet leave `vscode.lm` or its MCP members out.
 */
export function hasMcpProviderApi(api: {
  lm?: Partial<typeof vscode.lm>;
  McpStdioServerDefinition?: unknown;
} = vscode): boolean {
  return (
    typeof api.lm?.registerMcpServerDefinitionProvider === "function" &&
    typeof api.McpStdioServerDefinition === "function"
  );
}
