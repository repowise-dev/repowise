/**
 * Asks for the check tools, offered where an agent has not used one yet.
 *
 * `CHECK_PROMPTS` is word for word core's `agent_prompts/checks.py`
 * `CHECK_PROMPTS` (a parity test holds the two equal). Each names Repowise, so
 * the agent reaches for its tools.
 */

/** One ask per check tool, and `switch_profile` to give an agent the standard
 *  tool profile. `{agent}` is the agent's display name. */
export const CHECK_PROMPTS = {
  get_change_risk: "Before you commit, use Repowise to check how risky this change is and what it could break.",
  get_why: "Before you change a file, use Repowise to look up why it is the way it is and which decisions govern it.",
  get_risk: "Before you edit a file, use Repowise to check how risky it is: whether it is a hotspot and what usually changes with it.",
  get_health: "Use Repowise to check the health of the code you are about to change, and tell me what it finds.",
  get_dead_code: "Before you clean up, use Repowise to find which code in this area is unused.",
  switch_profile: "Give {agent} Repowise's standard MCP tool profile for this repository by running `repowise config set mcp.tools standard`, then tell me to restart {agent} so it loads the check tools.",
} as const;

/** The prompt that gives `agent` the standard tool profile. */
export function switchProfilePrompt(agent: string): string {
  return CHECK_PROMPTS.switch_profile.replaceAll("{agent}", agent);
}
