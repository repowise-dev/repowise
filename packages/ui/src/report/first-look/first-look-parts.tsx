"use client";

import type { ElementType, ReactNode } from "react";
import type {
  FirstLookItem,
  FirstLookLocation,
  FirstLookRouteKey,
} from "@repowise-dev/types/first-look";
import { AiPromptButton } from "../../health/ai-prompt-button";
import { cn } from "../../lib/cn";
import { renderActionTitle as inline } from "../../overview/next-actions";
import { formatNumber } from "../../lib/format";

/** Injected link component (e.g. Next's Link); defaults to a plain anchor. */
export type LinkLike = ElementType<{ href: string; className?: string; children: ReactNode }>;

/** A shell's URL for a route key, or null where the shell has no such page. */
export type FirstLookHrefFor = (
  route: FirstLookRouteKey,
  params: Record<string, string | number>,
) => string | null;

/** The page a section's "open in app" link goes to, where a route key names one. */
export const SECTION_ROUTE: Partial<Record<string, FirstLookRouteKey>> = {
  security: "security",
  dead_code: "dead_code",
  docs: "docs_drift",
  ownership: "ownership",
};

/** The first 7 characters of a commit, the way the rest of the app prints one. */
export const shortSha = (sha: string | null | undefined): string => (sha ?? "").slice(0, 7);

export const MONO_CAPS = "font-mono text-[10px] uppercase tracking-[0.06em]";

/** A "do now" / basis / confidence / effort mark. */
function Mark({ children, tone }: { children: ReactNode; tone?: "now" }) {
  return (
    <span
      className={cn(
        MONO_CAPS,
        "whitespace-nowrap rounded border px-1.5 py-0.5",
        tone === "now"
          ? "border-[color-mix(in_srgb,var(--color-error)_40%,transparent)] text-[var(--color-error)]"
          : "border-[var(--color-border-default)] text-[var(--color-text-secondary)]",
      )}
    >
      {children}
    </span>
  );
}

const CHIP =
  "inline-flex max-w-full items-center gap-1.5 break-all rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-inset)] px-2 py-0.5 font-mono text-xs text-[var(--color-text-primary)]";

function Chip({
  loc,
  note,
  hrefFor,
  Link,
}: {
  loc: FirstLookLocation;
  note: string | null;
  hrefFor: FirstLookHrefFor;
  Link: LinkLike;
}) {
  const href = hrefFor("file", loc.line ? { path: loc.path, line: loc.line } : { path: loc.path });
  const body = (
    <>
      <span>
        {loc.path}
        {loc.line ? <span className="text-[var(--color-accent-primary)]">:{loc.line}</span> : null}
      </span>
      {note ? <span className="break-normal text-[var(--color-text-tertiary)]">{note}</span> : null}
    </>
  );
  return href ? (
    <Link href={href} className={cn(CHIP, "hover:border-[var(--color-border-hover)]")}>
      {body}
    </Link>
  ) : (
    <span className={CHIP}>{body}</span>
  );
}

/** Locations as chips, each naming its symbol. A secret's chip says the value is
 *  withheld; the item never holds it. */
function Chips({ item, hrefFor, Link }: { item: FirstLookItem; hrefFor: FirstLookHrefFor; Link: LinkLike }) {
  const secret = item.section === "security";
  const more = item.locations_total - item.locations.length;
  return (
    <div className="mt-2.5 flex flex-wrap gap-1.5">
      {item.locations.map((loc, n) => (
        <Chip
          // One file can hold several of an item's symbols, so the position keeps keys unique.
          key={`${n}:${loc.path}`}
          loc={loc}
          note={secret ? "value redacted" : loc.symbol}
          hrefFor={hrefFor}
          Link={Link}
        />
      ))}
      {more > 0 ? (
        <span className={cn(CHIP, "text-[var(--color-text-tertiary)]")}>+ {formatNumber(more)} more</span>
      ) : null}
    </div>
  );
}

export type ItemState = "fixed" | "changed" | null;

export interface FirstLookItemRowProps {
  item: FirstLookItem;
  text: string | undefined;
  state: ItemState;
  reportCommit: string | null;
  hrefFor: FirstLookHrefFor;
  Link: LinkLike;
  onPrompt: (item: FirstLookItem) => void;
}

/** One finding: its sentence, its chips, its marks and its two actions. */
export function FirstLookItemRow({
  item,
  text,
  state,
  reportCommit,
  hrefFor,
  Link,
  onPrompt,
}: FirstLookItemRowProps) {
  const open = hrefFor(item.link.route, item.link.params);
  const verify = typeof item.title_facts.verify === "string" ? item.title_facts.verify : null;
  return (
    <article
      id={item.id}
      data-state={state ?? undefined}
      className="scroll-mt-6 border-b border-[var(--color-border-default)] py-4 last:border-b-0"
    >
      <p
        className={cn(
          "font-medium [overflow-wrap:anywhere]",
          state === "fixed" &&
            "text-[var(--color-text-tertiary)] line-through decoration-[var(--color-text-tertiary)]",
          !text && "font-mono text-sm",
        )}
      >
        {inline(text ?? item.rule)}
      </p>
      {state ? (
        <p
          className={cn(
            MONO_CAPS,
            "mt-1",
            state === "fixed" ? "text-[var(--color-success)]" : "text-[var(--color-warning)]",
          )}
        >
          {state === "fixed" ? "Fixed" : "Changed"} since {shortSha(reportCommit)}
        </p>
      ) : null}
      <Chips item={item} hrefFor={hrefFor} Link={Link} />
      {verify ? (
        <p className="mt-2 font-mono text-xs text-[var(--color-text-tertiary)] [overflow-wrap:anywhere]">
          verify: {verify}
        </p>
      ) : null}
      <div className="mt-3 flex flex-wrap items-center gap-x-3.5 gap-y-2">
        <div className="flex flex-wrap gap-1.5">
          {item.severity === "act_now" ? <Mark tone="now">do now</Mark> : null}
          <Mark>{item.basis}</Mark>
          <Mark>{item.confidence} confidence</Mark>
          <Mark>effort {item.effort}</Mark>
        </div>
        <div className="flex flex-wrap gap-2 sm:ml-auto">
          {open ? (
            <Link
              href={open}
              className="inline-flex h-7 items-center rounded-md bg-[var(--color-action-soft)] px-2.5 text-xs font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-action-soft-hover)]"
            >
              Open in app
            </Link>
          ) : null}
          <AiPromptButton
            label="Fix with your agent"
            ariaLabel={`Fix with your agent: ${text ?? item.rule}`}
            onClick={() => onPrompt(item)}
            className="border-transparent bg-[var(--color-action-primary)] text-[var(--color-text-on-action)] hover:border-transparent hover:bg-[var(--color-action-primary-hover)]"
          />
        </div>
      </div>
    </article>
  );
}
