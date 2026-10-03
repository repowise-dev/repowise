/**
 * Links from the extension to the repowise.dev/hosted story. Dependency-free
 * (no 'vscode' import) so the rules are testable on their own.
 */

export const SITE_URL = "https://repowise.dev";

const TRUTHY = new Set(["1", "true", "yes", "on"]);

/** `https://repowise.dev/hosted?src=…[&aid=…]#moment`. */
export function hostedStoryUrl(src: string, moment: string, aid?: string | null): string {
  const params = new URLSearchParams({ src });
  if (aid) params.set("aid", aid);
  return `${SITE_URL}/hosted?${params.toString()}#${moment}`;
}

/**
 * The repowise CLI's anonymous install id from `~/.repowise/platform.json`,
 * or null whenever the CLI's own telemetry is off (the same switches the CLI
 * honours) or there is no valid id. Never invents one.
 */
export function readCliAnonId(
  state: unknown,
  env: Record<string, string | undefined>,
): string | null {
  const off = (name: string) => TRUTHY.has((env[name] ?? "").trim().toLowerCase());
  if (off("DO_NOT_TRACK") || off("REPOWISE_TELEMETRY_DISABLED")) return null;
  if (!state || typeof state !== "object") return null;
  const record = state as Record<string, unknown>;
  if (record.telemetry_enabled === false) return null;
  const anon = record.anon_id;
  // The site ignores anything outside this shape, so don't send it.
  return typeof anon === "string" && /^[0-9a-f-]{8,64}$/.test(anon) ? anon : null;
}
