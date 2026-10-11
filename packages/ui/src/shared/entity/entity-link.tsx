"use client";

import * as React from "react";
import { cn } from "../../lib/cn";
import type { EntityMeta, EntityRef } from "./types";
import { defaultEntityLabel, resolveEntityHref } from "./routes";
import { EntityHoverCard } from "./entity-hover-card";

interface EntityLinkProps extends EntityRef {
  /** Optional metadata for the hover card. Hover card is omitted when undefined. */
  meta?: EntityMeta;
  /** Visual variant. Defaults to "inline" for use in prose / table cells. */
  variant?: "inline" | "monospace" | "plain";
  /** Override the resolved href (useful when nested inside an existing router). */
  href?: string;
  /**
   * Optional override for navigation. When supplied, the link calls onSelect
   * instead of letting the browser navigate. Used by the ContextDrawer
   * to intercept entity links and open the drawer in-place.
   */
  onSelect?: (entity: EntityRef) => void;
  className?: string;
  children?: React.ReactNode;
  title?: string;
}

/**
 * Single entry point for rendering any addressable Repowise entity.
 *
 * - resolves a canonical href via {@link resolveEntityHref}
 * - wraps in an {@link EntityHoverCard} when `meta` is provided
 * - delegates the click to a caller-supplied `onSelect` when present
 *   (e.g. opening a ContextDrawer instead of navigating)
 *
 * Pure presentational: no router, no data fetching, no side effects.
 * Callers wire up navigation by either letting the anchor navigate
 * naturally or by intercepting `onSelect`.
 */
export const EntityLink = React.forwardRef<HTMLAnchorElement, EntityLinkProps>(
  function EntityLink(
    { kind, id, repoId, meta, variant = "inline", href, onSelect, className, children, title },
    ref,
  ) {
    const entity: EntityRef = repoId !== undefined ? { kind, id, repoId } : { kind, id };
    const resolvedHref = href ?? resolveEntityHref(entity);
    const label = children ?? defaultEntityLabel(entity);

    const handleClick = (event: React.MouseEvent<HTMLAnchorElement>) => {
      if (!onSelect) return;
      // Only intercept "plain" left-clicks — preserve middle-click, ctrl/cmd
      // modifiers, and target=_blank semantics so power users can still open
      // entities in a new tab.
      if (
        event.defaultPrevented ||
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey
      ) {
        return;
      }
      event.preventDefault();
      onSelect(entity);
    };

    const anchor = (
      <a
        ref={ref}
        href={resolvedHref}
        onClick={handleClick}
        title={title ?? id}
        data-entity-kind={kind}
        data-entity-id={id}
        className={cn(
          "transition-colors text-[var(--color-text-primary)] hover:text-[var(--color-accent-primary)]",
          variant === "inline" && "underline decoration-dotted underline-offset-2",
          variant === "monospace" &&
            "font-mono text-[12px] underline decoration-dotted underline-offset-2",
          variant === "plain" && "no-underline",
          className,
        )}
      >
        {label}
      </a>
    );

    if (!meta) return anchor;

    return (
      <EntityHoverCard entity={entity} meta={meta}>
        {anchor}
      </EntityHoverCard>
    );
  },
);
