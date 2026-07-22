"use client";

import * as React from "react";
import { ArrowRight, GitMerge } from "lucide-react";
import { Card } from "../ui/card";
import type { CouplingEdge } from "@repowise-dev/types/coupling";

/** Injected link component (e.g. Next's Link); defaults to a plain anchor. */
type LinkLike = React.ElementType<{
  href: string;
  className?: string;
  children: React.ReactNode;
}>;

export interface CouplingMiniCardProps {
  /** Top couplings (already ranked strongest-first, small N). */
  edges: CouplingEdge[];
  /** Link to the full change-coupling view. */
  href: string;
  /** Link component (defaults to a plain anchor). */
  LinkComponent?: LinkLike;
}

function basename(path: string): string {
  return path.split("/").pop() ?? path;
}

/**
 * Overview front-door for the change-coupling view. The full diagram lives as
 * an Architecture tab (off the sidebar), so this card gives it a landing-page
 * presence and previews the strongest few file pairs that tend to change
 * together — enough to make the hidden page discoverable and worth a click.
 *
 * Fully presentational: the host fetches the top edges and passes them in.
 */
export function CouplingMiniCard({ edges, href, LinkComponent }: CouplingMiniCardProps) {
  const Link: LinkLike = LinkComponent ?? "a";
  const top = edges.slice(0, 3);
  const maxStrength = Math.max(...top.map((e) => e.strength), 1);

  return (
    <Card className="overflow-hidden">
      <Link
        href={href}
        className="group block h-full p-4 transition-colors hover:bg-[var(--color-bg-elevated)]"
      >
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-2 text-sm font-semibold text-[var(--color-text-primary)]">
            <GitMerge className="h-4 w-4 text-[var(--color-accent-primary)]" />
            Change coupling
          </span>
          <ArrowRight className="h-4 w-4 text-[var(--color-text-tertiary)] transition-transform group-hover:translate-x-0.5" />
        </div>

        {top.length > 0 ? (
          <ul className="mt-3 space-y-2">
            {top.map((e) => (
              <li key={`${e.source}|${e.target}`} className="space-y-1">
                <div
                  className="truncate font-mono text-xs text-[var(--color-text-secondary)]"
                  title={`${e.source} ↔ ${e.target}`}
                >
                  {basename(e.source)}
                  <span className="text-[var(--color-text-tertiary)]"> ↔ </span>
                  {basename(e.target)}
                </div>
                <div className="h-1 w-full overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
                  <div
                    className="h-full rounded-full bg-[var(--color-accent-primary)]"
                    style={{ width: `${Math.max(8, Math.round((e.strength / maxStrength) * 100))}%` }}
                  />
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-3 text-sm text-[var(--color-text-secondary)]">
            Files that tend to change together, drawn from git history. A hint at
            hidden relationships the imports don't show.
          </p>
        )}

        <p className="mt-3 text-xs font-medium text-[var(--color-accent-primary)]">
          {top.length > 0 ? "See the coupling map" : "Explore coupling"}
        </p>
      </Link>
    </Card>
  );
}
