/**
 * First questions to ask a newly connected agent, built from index facts.
 *
 * `FIRST_PROMPTS` is word for word core's `agent_prompts/first_prompts.py`
 * `FIRST_PROMPTS` (a parity test holds the two equal). Each names Repowise, so
 * the agent reaches for its tools, and names the repo or one of its files, so
 * the first answer is about the user's own code.
 */

/** Templates by name. `{repo}` is the repository, `{path}` a file in it,
 *  `{title}` a recorded decision's title, `{repos}` a workspace's repos. */
export const FIRST_PROMPTS = {
  tour: "Use Repowise to give me a tour of {repo}: what it does, its main modules, and where I should start reading.",
  hotspot: "Before I change {path}, use Repowise to tell me what depends on it, who knows it best, and what tends to break with it.",
  worst: "Use Repowise to explain why {path} is the least healthy file in {repo}, and how to improve it safely.",
  risky: "Use Repowise to find the riskiest files to change in {repo}, and tell me why.",
  decision: "Use Repowise to explain the decision \"{title}\" in {repo}: why it was made and which code follows it.",
  unused: "Use Repowise to find code in {repo} that nothing calls any more and is safe to delete.",
  workspace: "Use Repowise to show me how {repos} depend on each other: the contracts between them and which changes ripple.",
} as const;

/** Index facts the prompts can name; each is optional. */
export interface FirstPromptFacts {
  /** The busiest file. */
  hotspot?: string | null;
  /** The least healthy file. */
  worst?: string | null;
  /** A recorded decision's title. */
  decision?: string | null;
}

function fill(template: string, values: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (whole, key: string) => values[key] ?? whole);
}

/** Three prompts for `repo`: a tour, a risk question and a why question. A
 *  missing fact falls back to a question any indexed repo can answer. */
export function firstPrompts(repo: string, facts: FirstPromptFacts = {}): string[] {
  const { hotspot, worst, decision } = facts;
  const risk = hotspot
    ? fill(FIRST_PROMPTS.hotspot, { path: hotspot })
    : worst
      ? fill(FIRST_PROMPTS.worst, { path: worst, repo })
      : fill(FIRST_PROMPTS.risky, { repo });
  const why = decision
    ? fill(FIRST_PROMPTS.decision, { title: decision, repo })
    : fill(FIRST_PROMPTS.unused, { repo });
  return [fill(FIRST_PROMPTS.tour, { repo }), risk, why];
}

/** A workspace prompt names its repos up to this many; past it, it counts them. */
const NAMED_REPOS = 3;

/** The cross-repo question for a workspace of `repos`; one repo gets its tour.
 *  Mirrors core's `workspace_prompt`. */
export function workspacePrompt(repos: string[]): string {
  if (repos.length === 1) return fill(FIRST_PROMPTS.tour, { repo: repos[0]! });
  const names =
    repos.length > NAMED_REPOS
      ? `the ${repos.length} repos in this workspace`
      : `${repos.slice(0, -1).join(", ")} and ${repos[repos.length - 1]}`;
  return fill(FIRST_PROMPTS.workspace, { repos: names });
}
