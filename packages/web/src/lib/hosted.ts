/**
 * repowise.dev tips for the local dashboard: the links, the sentences, and
 * which one (if any) a page shows.
 *
 * Plain outbound links only: nothing here sends an event to repowise.dev.
 */

import type { HostedIdentity } from "@/lib/api/platform";

const HOSTED_URL = "https://repowise.dev/hosted";

/** `https://repowise.dev/hosted?src=local_web_<surface>#<moment>`. Only
 *  `src`: a link someone opens never carries an install id. */
export function hostedLink(surface: string, moment: string): string {
  const params = new URLSearchParams({ src: `local_web_${surface}` });
  return `${HOSTED_URL}?${params.toString()}#${moment}`;
}

export interface Nudge {
  text: string;
  /** Becomes `src=local_web_<surface>`. */
  surface: string;
  /** The section of the hosted page the link opens. */
  moment: string;
}

/** Publishing a public repo is free; anything about private repos or more
 *  repos says "free for 10 days, card required", never just "free". */
export const NUDGES = {
  mcp: {
    text: "Use this repo inside Claude.ai or ChatGPT. Local MCP only works in desktop tools.",
    surface: "mcp",
    moment: "mcp",
  },
  stale: {
    text: "Keep this up to date automatically on every push.",
    surface: "stale",
    moment: "sync",
  },
  docs: {
    text: "Want AI-written docs without an API key? repowise.dev includes the model.",
    surface: "docs",
    moment: "keys",
  },
} satisfies Record<string, Nudge>;

export type NudgeId = keyof typeof NUDGES;

/**
 * The one tip a page shows, or none. `candidates` are the tips whose moment
 * applies on this page, most specific first. Nothing shows while identity is
 * unknown, for a signed-in user, or when tips are off on the CLI or here.
 */
export function pickNudge(
  candidates: readonly (NudgeId | false | null | undefined)[],
  identity: HostedIdentity | null,
  tipsShown: boolean,
): NudgeId | null {
  if (!identity || identity.signed_in || !identity.hints_enabled || !tipsShown) return null;
  return candidates.find((c): c is NudgeId => Boolean(c)) ?? null;
}
