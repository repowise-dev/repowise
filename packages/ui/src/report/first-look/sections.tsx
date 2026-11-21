"use client";

import type { ElementType, ReactNode } from "react";
import {
  Activity,
  ArrowUpRight,
  FileText,
  FlaskConical,
  Layers,
  Scale,
  Shield,
  Trash2,
  Users,
  Zap,
  type LucideIcon,
} from "lucide-react";
import {
  FIRST_LOOK_SECTION_CAP,
  type FirstLookLivingItem,
  type FirstLookMark,
  type FirstLookRouteKey,
} from "@repowise-dev/types/first-look";
import { cn } from "../../lib/cn";
import { formatNumber } from "../../lib/format";
import { renderActionTitle as inline } from "../../overview/next-actions";
import { CONTROL, ItemPromptButton, MarkMenu, SectionPromptButton } from "./controls";
import type { FirstLookCopy } from "./copy";
import { factNumber, isClosed, pillOf, sectionLayout, stripValue, type LivingSection } from "./living";

/** Injected link component (e.g. Next's Link); defaults to a plain anchor. */
export type LinkLike = ElementType<{ href: string; className?: string; children: ReactNode }>;

/** A shell's URL for a route key, or null where the shell has no such page. */
export type FirstLookHrefFor = (
  route: FirstLookRouteKey,
  params: Record<string, string | number>,
) => string | null;

export const SECTION_ICON: Record<string, LucideIcon> = {
  security: Shield,
  performance: Zap,
  health: Activity,
  tests: FlaskConical,
  dead_code: Trash2,
  docs: FileText,
  decisions: Scale,
  hygiene: Layers,
  ownership: Users,
};

export const sectionIcon = (key: string): LucideIcon => SECTION_ICON[key] ?? Layers;

/** The page a section's "more in the app" link opens, where a route key names one. */
const SECTION_ROUTE: Partial<Record<string, FirstLookRouteKey>> = {
  security: "security",
  health: "health_file",
  dead_code: "dead_code",
  docs: "docs_drift",
  ownership: "ownership",
};

/** Rows a compact table shows before "more in the app". */
const TABLE_ROWS = { functions: 6, dead_code: 5, documents: 4 } as const;

export interface SectionHandlers {
  copy: FirstLookCopy;
  hrefFor: FirstLookHrefFor;
  Link: LinkLike;
  text: (item: FirstLookLivingItem) => string;
  onItemPrompt: (item: FirstLookLivingItem) => void;
  onSectionPrompt: (key: string) => void;
  onMark?: ((item: FirstLookLivingItem, mark: FirstLookMark, reason: string | null) => void) | undefined;
}

const DOT: Record<string, string> = {
  act_now: "bg-[var(--color-error)]",
  plan: "bg-[var(--color-warning)]",
  improve: "bg-[var(--color-success)]",
};

const PILL: Record<string, string> = {
  done: "text-[var(--color-success)] border-[color-mix(in_srgb,var(--color-success)_40%,transparent)]",
  resolved: "text-[var(--color-success)] border-[color-mix(in_srgb,var(--color-success)_40%,transparent)]",
  acknowledged: "text-[var(--color-warning)] border-[color-mix(in_srgb,var(--color-warning)_40%,transparent)]",
  new: "text-[var(--color-accent-primary)] border-[color-mix(in_srgb,var(--color-accent-primary)_40%,transparent)]",
};

export function StatusPill({ item, copy }: { item: FirstLookLivingItem; copy: FirstLookCopy }) {
  const pill = pillOf(item);
  if (!pill) return null;
  return (
    <span
      data-status={pill}
      title={item.status_reason ?? undefined}
      className={cn(
        "whitespace-nowrap rounded-full border border-[var(--color-border-default)] px-2 text-[11.5px] text-[var(--color-text-secondary)]",
        PILL[pill],
      )}
    >
      {copy.status[pill]}
      {/* The reason is the tooltip; a table row has no fine print to carry it, so say it here too. */}
      {item.status_reason ? <span className="sr-only">: {item.status_reason}</span> : null}
    </span>
  );
}

function Chip({ href, Link, children }: { href: string | null; Link: LinkLike; children: ReactNode }) {
  const cls =
    "rounded-[5px] bg-[var(--color-bg-inset)] px-[7px] py-0.5 font-mono text-xs text-[var(--color-text-primary)] [overflow-wrap:anywhere] hover:outline hover:outline-1 hover:outline-[var(--color-border-default)]";
  return href ? (
    <Link href={href} className={cls}>
      {children}
    </Link>
  ) : (
    <span className={cls}>{children}</span>
  );
}

function fineLine(item: FirstLookLivingItem, copy: FirstLookCopy): string {
  const parts: string[] = [];
  if (item.severity === "act_now") parts.push(copy.severity.act_now);
  if (item.section === "security") parts.push(copy.valuesRedacted);
  else parts.push(copy.confidence(item.confidence));
  parts.push(copy.effort(item.effort));
  if (item.status_reason) parts.push(`"${item.status_reason}"`);
  return parts.join(" · ");
}

/** One finding as a list row: its sentence, where it is, and the quiet controls. */
export function ItemRow({ item, h }: { item: FirstLookLivingItem; h: SectionHandlers }) {
  const closed = isClosed(item);
  const text = h.text(item);
  const more = item.locations_total - item.locations.length;
  // A symbol tells chips apart only where one file holds several of the item's places.
  const paths = item.locations.map((l) => l.path);
  const shared = new Set(paths.filter((p, i) => paths.indexOf(p) !== i));
  return (
    <li
      id={item.id}
      data-status={item.status}
      className="group relative grid scroll-mt-20 grid-cols-[14px_minmax(0,1fr)] gap-x-2.5 gap-y-1 border-b border-[var(--color-border-default)] py-3 last:border-b-0"
    >
      <span
        aria-hidden
        className={cn("mt-[7px] size-2 rounded-full", closed ? "bg-[var(--color-border-default)]" : DOT[item.severity])}
      />
      <span className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1 pr-0 md:pr-20">
        <span
          className={cn(
            "font-medium",
            closed && "text-[var(--color-text-secondary)] line-through decoration-[var(--color-text-tertiary)]",
          )}
        >
          {inline(text)}
        </span>
        <StatusPill item={item} copy={h.copy} />
      </span>
      {item.locations.length ? (
        <div className="col-start-2 flex flex-wrap gap-1.5">
          {item.locations.map((loc, n) => (
            <Chip
              key={`${n}:${loc.path}`}
              Link={h.Link}
              href={h.hrefFor("file", loc.line ? { path: loc.path, line: loc.line } : { path: loc.path })}
            >
              {loc.path}
              {loc.line ? (
                <>
                  :<b className="font-medium text-[var(--color-accent-primary)]">{loc.line}</b>
                </>
              ) : null}
              {loc.symbol && shared.has(loc.path) ? (
                <span className="text-[var(--color-text-tertiary)]"> {loc.symbol}</span>
              ) : null}
            </Chip>
          ))}
          {more > 0 ? <Chip href={null} Link={h.Link}>+ {formatNumber(more)}</Chip> : null}
        </div>
      ) : null}
      <span className="col-start-2 text-xs text-[var(--color-text-tertiary)]">{fineLine(item, h.copy)}</span>
      {item.status !== "resolved" ? (
        <span
          className={cn(
            CONTROL,
            // Hidden until pointed at only where there is a pointer that can hover.
            "col-start-2 flex gap-0.5 md:absolute md:right-0 md:top-2 md:transition-opacity motion-reduce:transition-none [@media(hover:hover)]:md:opacity-0 md:group-focus-within:opacity-100 md:group-hover:opacity-100",
          )}
        >
          {h.onMark ? <MarkMenu copy={h.copy} label={text} onMark={(m, r) => h.onMark?.(item, m, r)} /> : null}
          <ItemPromptButton copy={h.copy} label={text} onCopy={() => h.onItemPrompt(item)} />
        </span>
      ) : null}
    </li>
  );
}

const TH =
  "whitespace-nowrap border-b border-[var(--color-border-default)] pb-1.5 pr-2.5 pt-2 text-left text-[11.5px] font-medium uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]";
const TD = "border-b border-[var(--color-border-default)] py-2 pr-2.5 align-baseline [tr:last-child_&]:border-b-0";
const NUM = "text-right font-mono text-[12.5px] tabular-nums";

const num = (v: number | null) => (v == null ? "—" : formatNumber(v));

function PathCell({ item, h, label }: { item: FirstLookLivingItem; h: SectionHandlers; label?: ReactNode }) {
  const loc = item.locations[0];
  const href = h.hrefFor(item.link.route, item.link.params);
  const path = loc ? `${loc.path}${loc.line ? `:${loc.line}` : ""}` : item.rule;
  const sub = (
    <span className="block font-mono text-[11.5px] text-[var(--color-text-secondary)] [overflow-wrap:anywhere]">{path}</span>
  );
  return (
    <td className={TD}>
      <span className="flex flex-wrap items-baseline gap-x-2">
        {label ? (href ? <h.Link href={href} className="hover:underline">{label}</h.Link> : label) : null}
        <StatusPill item={item} copy={h.copy} />
      </span>
      {label ? sub : href ? <h.Link href={href} className="hover:underline">{sub}</h.Link> : sub}
    </td>
  );
}

/** The code health table's number columns, in order. */
const FUNCTION_COLUMNS = [
  ["ccn", "ccn"],
  ["nloc", "lines"],
  ["importers", "importers"],
  ["changes_90d", "changes"],
] as const;

/** The columns at least one row has a number for; an all-empty column is left out. */
function functionColumns(items: FirstLookLivingItem[]) {
  return FUNCTION_COLUMNS.filter(([fact]) => items.some((i) => factNumber(i, fact) != null));
}

function FunctionTable({
  items,
  columns,
  h,
}: {
  items: FirstLookLivingItem[];
  columns: ReturnType<typeof functionColumns>;
  h: SectionHandlers;
}) {
  const t = h.copy.table;
  return (
    <div className="mt-1.5 overflow-x-auto">
      <table className="w-full border-collapse text-[13.5px]">
        <thead>
          <tr>
            <th className={TH}>{t.function}</th>
            {columns.map(([fact, label]) => (
              <th key={fact} className={cn(TH, "text-right")}>
                {t[label]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const symbol = item.locations[0]?.symbol ?? (item.title_facts.symbol as string | undefined);
            return (
              <tr key={item.id} id={item.id} data-status={item.status} className="scroll-mt-20">
                <PathCell item={item} h={h} label={<code className="font-mono text-[0.92em]">{symbol ?? item.rule}</code>} />
                {columns.map(([fact]) => (
                  <td key={fact} className={cn(TD, NUM)}>
                    {num(factNumber(item, fact))}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** A path and one number per row, no heading row unless asked for. */
function CountTable({
  items,
  h,
  fact,
  head,
}: {
  items: FirstLookLivingItem[];
  h: SectionHandlers;
  fact: (item: FirstLookLivingItem) => number | null;
  head?: [string, string];
}) {
  return (
    <div className="mt-1.5 overflow-x-auto">
      <table className="w-full border-collapse text-[13.5px]">
        {head ? (
          <thead>
            <tr>
              <th className={TH}>{head[0]}</th>
              <th className={cn(TH, "text-right")}>{head[1]}</th>
            </tr>
          </thead>
        ) : null}
        <tbody>
          {items.map((item) => (
            <tr key={item.id} id={item.id} data-status={item.status} className="scroll-mt-20">
              <PathCell item={item} h={h} />
              <td className={cn(TD, NUM)}>{num(fact(item))}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Intro({ text }: { text: string | undefined }) {
  return text ? <p className="mb-1 mt-2.5 max-w-[70ch] text-[var(--color-text-secondary)]">{inline(text)}</p> : null;
}

function MoreLink({ n, href, h }: { n: number; href: string | null; h: SectionHandlers }) {
  if (n <= 0 || !href) return null;
  return (
    <h.Link
      href={href}
      className={cn(CONTROL, "mt-2.5 inline-flex items-center gap-1.5 text-[13px] text-[var(--color-accent-primary)] hover:underline")}
    >
      {h.copy.moreInApp(formatNumber(n))}
      <ArrowUpRight aria-hidden className="size-4" />
    </h.Link>
  );
}

function Coverage({ section, copy }: { section: LivingSection; copy: FirstLookCopy }) {
  const stats = [
    [section.facts.line_pct, copy.linesCovered],
    [section.facts.branch_pct, copy.branchesCovered],
  ].filter((s): s is [number, string] => typeof s[0] === "number");
  if (!stats.length) return null;
  return (
    <div className="mt-3 flex flex-wrap gap-7">
      {stats.map(([pct, label]) => (
        <div key={label} className="grid">
          <span className="text-[26px] font-semibold tabular-nums">{pct.toFixed(1)}%</span>
          <span className="text-xs text-[var(--color-text-secondary)]">{label}</span>
          <span className="mt-1.5 h-1.5 w-[140px] overflow-hidden rounded-[3px] bg-[var(--color-bg-inset)]" aria-hidden>
            <i className="block h-full bg-[var(--color-warning)]" style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
          </span>
        </div>
      ))}
    </div>
  );
}

/**
 * A section's rows, capped, then what the first look had here that is gone
 * now. "More in the app" counts only rows the app still has.
 */
function SectionBody({
  section,
  resolved,
  intro,
  h,
}: {
  section: LivingSection;
  resolved: FirstLookLivingItem[];
  intro: string | undefined;
  h: SectionHandlers;
}) {
  const items = section.items;
  /** The first `cap` rows of `list`, then the gone rows of the same kind. */
  const shown = (list: FirstLookLivingItem[], cap: number, gone = resolved) => [...list.slice(0, cap), ...gone];
  const route = SECTION_ROUTE[section.key];
  const href = route ? h.hrefFor(route, {}) : null;
  switch (sectionLayout(section.key)) {
    case "functions": {
      const rows = shown(items, TABLE_ROWS.functions);
      const columns = functionColumns(rows);
      return (
        <>
          <Intro text={intro} />
          {!rows.length ? null : columns.length ? (
            <FunctionTable items={rows} columns={columns} h={h} />
          ) : (
            // No row has the numbers (an index from before they were recorded): the prose rows say it.
            <ul className="m-0 list-none p-0">
              {rows.map((item) => (
                <ItemRow key={item.id} item={item} h={h} />
              ))}
            </ul>
          )}
          <MoreLink n={items.length - TABLE_ROWS.functions} href={href} h={h} />
        </>
      );
    }
    case "dead_code": {
      const isFile = (i: FirstLookLivingItem) => i.rule === "unreachable_file";
      const files = items.filter(isFile);
      const exports = items.filter((i) => !isFile(i));
      const cap = TABLE_ROWS.dead_code;
      const fileRows = shown(files, cap, resolved.filter(isFile));
      const exportRows = shown(exports, cap, resolved.filter((i) => !isFile(i)));
      return (
        <>
          <Intro text={intro} />
          <div className="grid gap-x-10 md:grid-cols-2">
            {fileRows.length ? <CountTable items={fileRows} h={h} fact={(i) => factNumber(i, "lines")} /> : null}
            {exportRows.length ? <CountTable items={exportRows} h={h} fact={(i) => factNumber(i, "symbols")} /> : null}
          </div>
          <MoreLink
            n={items.length - Math.min(files.length, cap) - Math.min(exports.length, cap)}
            href={href}
            h={h}
          />
        </>
      );
    }
    case "documents":
      return (
        <>
          <Intro text={intro} />
          {items.length + resolved.length ? (
            <CountTable
              items={shown(items, TABLE_ROWS.documents)}
              h={h}
              fact={(i) => factNumber(i, "references")}
              head={[h.copy.table.document, h.copy.table.broken]}
            />
          ) : null}
          <MoreLink n={items.length - TABLE_ROWS.documents} href={href} h={h} />
        </>
      );
    case "coverage":
      return (
        <>
          <Coverage section={section} copy={h.copy} />
          <Intro text={intro} />
        </>
      );
    default:
      return (
        <>
          <Intro text={intro} />
          {items.length + resolved.length ? (
            <ul className="m-0 list-none p-0">
              {shown(items, FIRST_LOOK_SECTION_CAP).map((item) => (
                <ItemRow key={item.id} item={item} h={h} />
              ))}
            </ul>
          ) : null}
          <MoreLink n={items.length - FIRST_LOOK_SECTION_CAP} href={href} h={h} />
        </>
      );
  }
}

function headerCount(section: LivingSection, copy: FirstLookCopy): string | null {
  if (section.key === "tests") return null;
  const total = section.facts.production_causes;
  if (section.key === "performance" && typeof total === "number" && total > section.items.length) {
    return copy.countOf(formatNumber(section.items.length), formatNumber(total));
  }
  return stripValue(section);
}

/** One section: icon, title and count, its "Fix with agent", then its body. */
export function ReportSection({
  section,
  title,
  intro,
  resolved,
  h,
}: {
  section: LivingSection;
  title: string;
  intro: string | undefined;
  /** Items the first look had here that are gone now. */
  resolved: FirstLookLivingItem[];
  h: SectionHandlers;
}) {
  const Icon = sectionIcon(section.key);
  const count = headerCount(section, h.copy);
  const open = section.items.some((i) => !isClosed(i));
  const heading = `fl-${section.key}`;
  return (
    <section id={section.key} aria-labelledby={heading} data-section={section.key} className="mt-11 scroll-mt-[70px]">
      <div className="flex items-center gap-2.5 border-b border-[var(--color-border-default)] pb-2.5">
        <span
          aria-hidden
          className={cn(
            "grid size-[30px] place-items-center rounded-lg bg-[var(--color-bg-inset)] text-[var(--color-text-primary)]",
            section.key === "security" && open && "text-[var(--color-error)]",
          )}
        >
          <Icon className="size-4" />
        </span>
        <h2 id={heading} className="m-0 flex items-center gap-2.5 font-serif text-[21px] font-semibold">
          {title}
          {count ? <span className="font-sans text-[13px] font-medium text-[var(--color-text-secondary)]">{count}</span> : null}
        </h2>
        <span className="flex-1" />
        {open && section.key !== "tests" ? (
          <SectionPromptButton copy={h.copy} label={title} onCopy={() => h.onSectionPrompt(section.key)} />
        ) : null}
      </div>
      <SectionBody section={section} resolved={resolved} intro={intro} h={h} />
    </section>
  );
}
