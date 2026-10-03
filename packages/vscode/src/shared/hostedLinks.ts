/**
 * Links from the extension to the repowise.dev/hosted story. Dependency-free
 * (no 'vscode' import) so the rules are testable on their own.
 */

export const SITE_URL = "https://repowise.dev";

/** `https://repowise.dev/hosted?src=…#moment`. Only `src`: a link someone
 *  opens never carries an install or machine id. */
export function hostedStoryUrl(src: string, moment: string): string {
  const params = new URLSearchParams({ src });
  return `${SITE_URL}/hosted?${params.toString()}#${moment}`;
}
