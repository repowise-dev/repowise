"use client";

import { OverviewSection } from "@repowise-dev/ui/overview";
import { CopyLine, SettingsRow, SettingsRows } from "@repowise-dev/ui/settings";
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
    <OverviewSection
      title={t("mcp.title")}
      description={t("mcp.description")}
    >
      <SettingsRows>
        <SettingsRow
          label={t("mcp.serverLabel")}
          hint={t("mcp.serverHint")}
        >
          <CopyLine value={MCP_CONFIG} />
        </SettingsRow>
      </SettingsRows>
    </OverviewSection>
  );
}
