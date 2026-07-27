"use client";

import { OverviewSection } from "@repowise-dev/ui/overview";
import { CopyLine, SettingsRow, SettingsRows } from "@repowise-dev/ui/settings";

const MCP_CONFIG = JSON.stringify(
  {
    mcpServers: {
      repowise: {
        command: "repowise",
        args: ["mcp", "/path/to/your/repo", "--transport", "stdio"],
      },
    },
  },
  null,
  2,
);

export function McpSection() {
  return (
    <OverviewSection
      title="Editor config"
      description="Paste this into Claude Code, Cursor or Cline to let the agent query this codebase. Replace the path with the repo you want served, and index it first so there is something to answer from."
    >
      <SettingsRows>
        <SettingsRow
          label="MCP server"
          hint="Goes in your editor's MCP config file."
        >
          <CopyLine value={MCP_CONFIG} />
        </SettingsRow>
      </SettingsRows>
    </OverviewSection>
  );
}
