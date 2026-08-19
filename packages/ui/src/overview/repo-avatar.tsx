import * as React from "react";
import { githubAvatarUrl, githubOwnerFromRemote } from "../lib/github";

// Re-exported because `overview/index.ts` and the existing tests import it
// from here. The implementation moved to `lib/github` so the owner avatar can
// share it instead of growing a second copy of the same parsing.
export { githubOwnerFromRemote };

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
          src={githubAvatarUrl(owner, size * 2)}
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
