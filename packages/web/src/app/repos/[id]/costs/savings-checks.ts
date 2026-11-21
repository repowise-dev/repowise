import type { CheckAgentView, CheckToolId } from "@repowise-dev/ui/savings";
import type { McpToolSurface } from "@/lib/api/types";
import type { SetupResponse } from "@/lib/api/setup";
import { repoSettingsHref, setupHref } from "@/components/settings/settings-groups";

/** Ids of the agents Setup reports as connected, in Setup's order. */
export function connectedAgentIds(setup: SetupResponse | undefined): string[] {
  return (setup?.agents ?? []).filter((agent) => agent.connected).map((agent) => agent.id);
}

/**
 * Connected agents with the tools their server lists, from Setup's `agents[]`
 * and the tools endpoint's per-agent resolution; nothing re-derived here.
 * Undefined while either is unknown, so the section never names an agent's
 * tools on a guess; empty when Setup knows no agent is connected.
 */
export function checkAgents(
  setup: SetupResponse | undefined,
  surface: McpToolSurface | undefined,
): CheckAgentView[] | undefined {
  if (!setup || !setup.agents_available) return undefined;
  const connected = (setup.agents ?? []).filter((agent) => agent.connected);
  if (connected.length === 0) return [];
  const clients = surface?.clients;
  // An agent the server could not resolve leaves the whole picture unknown,
  // rather than a table that silently drops it.
  if (!clients || connected.some((agent) => !clients[agent.id])) return undefined;
  return connected.map((agent) => {
    const resolved = clients[agent.id]!;
    return {
      id: agent.id,
      name: agent.display_name,
      profile: resolved.profile ?? null,
      tools: resolved.tools,
      lastCallAt: agent.last_call_at ?? null,
    };
  });
}

/** The existing pages an outcome row opens: Decisions, Do next (overview), Code Health. */
export function checkLinks(repoId: string): Partial<Record<CheckToolId, string>> {
  return {
    get_why: `/repos/${repoId}/decisions`,
    get_health: `/repos/${repoId}/overview`,
    get_risk: `/repos/${repoId}/code-health`,
    get_dead_code: `/repos/${repoId}/dead-code`,
  };
}

export function checksHrefs(repoId: string): { agentsHref: string; connectHref: string } {
  return { agentsHref: repoSettingsHref(repoId, "agents"), connectHref: setupHref(repoId) };
}
