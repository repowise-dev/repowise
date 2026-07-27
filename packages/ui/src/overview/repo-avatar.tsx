import * as React from "react";

/**
 * Parses the owner (GitHub user or org) out of a git remote URL.
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

export interface RepoAvatarProps {
  /** Repo name, for the initials fallback. */
  name: string;
  /** Git remote, if the repo has one. Only GitHub remotes resolve to a real
   *  avatar; everything else renders initials and touches no network. */
  remoteUrl?: string | null | undefined;
  size?: number;
  className?: string;
}

/**
 * Repo mark for the identity header.
 *
 * A server component on purpose. The obvious implementation swaps in a
 * fallback from an `onError` handler, but that needs `useState` and therefore
 * a client boundary at the very top of the page — the one place where a
 * hydration boundary costs the most, on a page that must stream fast and be
 * indexable. So the initials sit *underneath* the image instead: if the avatar
 * 404s, an `alt=""` image paints nothing and the layer beneath shows through.
 * No JavaScript, no boundary, same outcome.
 *
 * Also conservative about the network: a local install should not call
 * github.com just because a page rendered. The request only happens when the
 * repo actually has a GitHub remote recorded.
 */
export function RepoAvatar({ name, remoteUrl, size = 40, className }: RepoAvatarProps) {
  const owner = githubOwnerFromRemote(remoteUrl);
  const initials = name.replace(/[^a-zA-Z0-9]/g, "").slice(0, 2).toUpperCase() || "?";

  return (
    <span
      aria-hidden
      className={`relative inline-flex shrink-0 items-center justify-center overflow-hidden rounded-xl border border-[var(--color-border-default)] bg-[var(--color-accent-muted)] ${className ?? ""}`}
      style={{ width: size, height: size }}
    >
      <span
        className="font-semibold leading-none text-[var(--color-accent-primary)]"
        style={{ fontSize: Math.max(11, Math.floor(size / 2.8)) }}
      >
        {initials}
      </span>
      {owner && (
        <img
          src={`https://avatars.githubusercontent.com/${encodeURIComponent(owner)}?size=${size * 2}`}
          alt=""
          width={size}
          height={size}
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          className="absolute inset-0 h-full w-full object-cover"
        />
      )}
    </span>
  );
}
