import * as React from "react";
import { cn } from "../lib/cn";
import { githubAvatarUrl, githubLoginFromEmail } from "../lib/github";

/**
 * Contributor mark: a real GitHub avatar where we can prove one, initials
 * everywhere else.
 *
 * Three decisions worth keeping:
 *
 * 1. **Not a client component.** There is no state and no handler here, so the
 *    directive only forced every parent that renders a list of these to ship
 *    them to the browser. Dropping it lets the owner rows render on the server.
 *
 * 2. **Initials sit *underneath* the image**, the same trick as `RepoAvatar`.
 *    The obvious implementation swaps in a fallback from `onError`, which needs
 *    `useState` and therefore re-introduces the client boundary above. Instead
 *    an `alt=""` image that 404s paints nothing and the layer beneath shows
 *    through. No JavaScript, same outcome.
 *
 * 3. **One accent, not eight hues.** This used to pick from a rose / amber /
 *    emerald / sky / … palette hashed off the email, which spent eight colours
 *    on a distinction that carries no meaning — two contributors being
 *    different colours tells you nothing about either. It also hard-coded the
 *    `-300` text ramp, tuned for dark mode, and rendered it unchanged on the
 *    cream light background.
 */

function initials(name: string, email: string | null): string {
  const source = (name || email || "?").trim();
  if (!source) return "?";
  const parts = source.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    const a = parts[0]?.[0] ?? "";
    const b = parts[1]?.[0] ?? "";
    return (a + b).toUpperCase() || "?";
  }
  return source.slice(0, 2).toUpperCase();
}

const SIZE_PX = { sm: 24, md: 32, lg: 48 } as const;

export interface OwnerAvatarProps {
  name: string;
  email?: string | null;
  size?: keyof typeof SIZE_PX;
  className?: string;
}

export function OwnerAvatar({ name, email, size = "md", className }: OwnerAvatarProps) {
  const px = SIZE_PX[size];
  const login = githubLoginFromEmail(email);
  const label = name || email || "contributor";

  return (
    <span
      className={cn(
        "relative inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full",
        "border border-[var(--color-border-default)] bg-[var(--color-accent-muted)]",
        className,
      )}
      style={{ width: px, height: px }}
      aria-label={label}
      title={label}
    >
      <span
        className="font-semibold leading-none tracking-wide text-[var(--color-accent-primary)]"
        style={{ fontSize: Math.max(9, Math.floor(px / 2.9)) }}
      >
        {initials(name, email ?? null)}
      </span>
      {login && (
        <img
          // 2x for retina. Requested only when a noreply address proved a
          // login, so a repo with no GitHub-hosted history touches no network.
          src={githubAvatarUrl(login, px * 2)}
          alt=""
          width={px}
          height={px}
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          className="absolute inset-0 h-full w-full object-cover"
        />
      )}
    </span>
  );
}
