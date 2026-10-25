"use client";

import { CopyLine, SettingsRow } from "@repowise-dev/ui/settings";
import { useTranslations } from "next-intl";

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
  const t = useTranslations("settings");

  return (
      <SettingsRow label={t("mcp.serverLabel")} hint={t("mcp.serverHint")}>
        <CopyLine value={MCP_CONFIG} copyLabel={t("mcp.serverLabel")} />
      </SettingsRow>
  );
}
