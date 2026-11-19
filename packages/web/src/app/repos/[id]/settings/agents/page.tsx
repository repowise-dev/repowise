import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { SettingsAdvanced } from "@repowise-dev/ui/settings";
import { getRepo } from "@/lib/api/repos";
import { RepoAgentRows } from "@/components/setup/setup-settings";
import { McpToolsSection } from "@/components/settings/mcp-tools-section";
import { SettingsBlock, SettingsGroup } from "@/components/settings/settings-groups";

interface Props {
  params: Promise<{ id: string }>;
}

/** Which agents read this repository, and which MCP tools they get. */
export default async function RepoAgentsSettingsPage({ params }: Props) {
  const { id } = await params;
  const ts = await getTranslations("settings");
  const tg = await getTranslations("settings.groups");
  let repo;
  try {
    repo = await getRepo(id);
  } catch {
    notFound();
  }

  return (
    <SettingsGroup title={tg("agents.title")} description={tg("agents.description")}>
      <RepoAgentRows repoId={id} repoName={repo.name} />
      <SettingsAdvanced label={ts("advanced")} hint={ts("advancedHints.agents")}>
        <SettingsBlock title={ts("mcpTools.title")} description={ts("mcpTools.description")}>
          <McpToolsSection repoId={id} />
        </SettingsBlock>
      </SettingsAdvanced>
    </SettingsGroup>
  );
}
