/**
 * Resolving GitHub identities out of the things git actually records.
 *
 * Both helpers are deliberately conservative: they return null for anything
 * they cannot prove, because the caller's fallback (initials) is a perfectly
 * good default rather than an error state. Guessing wrong is worse than not
 * guessing — a wrong avatar attributes someone else's face to a commit.
 */

/** `https://avatars.githubusercontent.com/...` for a login, at a given px size. */
export function githubAvatarUrl(login: string, size: number): string {
  return `https://avatars.githubusercontent.com/${encodeURIComponent(login)}?size=${size}`;
}

/**
 * Parses the owner (user or org) out of a git remote URL.
 *
 * Handles the three forms a remote actually takes: HTTPS, SSH scp-style, and
 * `ssh://`. Returns null for anything that is not GitHub, which is the signal
 * to render initials instead of reaching for an avatar that does not exist.
 */
export function githubOwnerFromRemote(remote: string | null | undefined): string | null {
  if (!remote) return null;
  const trimmed = remote.trim().replace(/\.git$/, "");
  const patterns = [
    /^https?:\/\/(?:[^@]+@)?github\.com\/([^/]+)\/[^/]+$/i,
    /^git@github\.com:([^/]+)\/[^/]+$/i,
    /^ssh:\/\/git@github\.com\/([^/]+)\/[^/]+$/i,
  ];
  for (const re of patterns) {
    const m = trimmed.match(re);
    if (m?.[1]) return m[1];
  }
  return null;
}

/**
 * Parses a GitHub login out of a commit author email, for the two noreply
 * forms GitHub issues:
 *
 *     12345678+octocat@users.noreply.github.com   (current)
 *     octocat@users.noreply.github.com            (pre-2017)
 *
 * Only noreply addresses, on purpose. The alternative for a real address is
 * Gravatar, which means hashing a contributor's email and sending it to a
 * third party from the viewer's browser: on a private repo that hands the
 * contributor roster to someone who was never asked, and an MD5 of a known
 * corporate address is trivially reversible. A noreply address already
 * encodes a public username, so resolving it leaks nothing that was not
 * already public.
 *
 * Coverage is therefore partial by design — good on open-source history,
 * near zero on a repo where everyone commits from their work address. That is
 * fine: initials are the default state, not a failure.
 */
export function githubLoginFromEmail(email: string | null | undefined): string | null {
  if (!email) return null;
  const m = email
    .trim()
    .toLowerCase()
    .match(/^(?:\d+\+)?([a-z\d](?:[a-z\d]|-(?=[a-z\d])){0,38})@users\.noreply\.github\.com$/);
  return m?.[1] ?? null;
}
