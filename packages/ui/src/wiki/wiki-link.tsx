/**
 * Inline anchor for a resolved wiki cross-reference.
 *
 * Rendered by the MDX preprocessor wherever a backticked path/symbol
 * was matched to another wiki page. Styled to look like a `<code>`
 * inline literal — preserves the dev-doc feel — but with a subtle
 * dotted underline so users can tell it's clickable.
 */

import type { ReactNode } from "react";

export interface WikiLinkProps {
  href: string;
  kind?: "file" | "symbol" | "page";
  children?: ReactNode;
}

export function WikiLink({ href, kind = "page", children }: WikiLinkProps) {
  return (
    <a
      href={href}
      data-wiki-link-kind={kind}
      className="rounded bg-[var(--color-bg-elevated)] px-1.5 py-0.5 text-xs font-mono text-[var(--color-accent-primary)] underline decoration-dotted underline-offset-2 hover:opacity-80 transition-opacity"
    >
      {children}
    </a>
  );
}
