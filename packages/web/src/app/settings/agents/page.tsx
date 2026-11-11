import { getTranslations } from "next-intl/server";
import { SettingsRow, SettingsRows } from "@repowise-dev/ui/settings";
import { listRepos } from "@/lib/api/repos";
import { HostedNudgeSlot } from "@/components/hosted/hosted-nudge-slot";
import { McpSection } from "@/components/settings/mcp-section";
import { SettingsGroup } from "@/components/settings/settings-groups";
import { HostedAgentsRow, McpToolsRepoLinks } from "@/components/setup/setup-settings";

/**
 * The editor config every agent needs, then where each repository's tools
 * are chosen. Tools are stored per repository, so they are set there.
 */
export default async function AgentsSettingsPage() {
  const ts = await getTranslations("settings");
  const tg = await getTranslations("settings.groups");
  const repos = await listRepos().catch(() => []);
  return (
    <SettingsGroup title={tg("agents.title")} description={tg("agents.description")}>
      <HostedNudgeSlot candidates={["mcp"]} />
      <SettingsRows>
        <McpSection />
        <SettingsRow label={ts("mcpTools.perRepoLabel")} hint={ts("mcpTools.perRepoHint")}>
          <McpToolsRepoLinks repos={repos} />
        </SettingsRow>
        <HostedAgentsRow />
      </SettingsRows>
    </SettingsGroup>
  );
}
