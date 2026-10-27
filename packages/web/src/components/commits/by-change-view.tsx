import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { ApiError } from "@repowise-dev/ui/shared/api-error";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { InfoTip } from "@repowise-dev/ui/shared/info-tip";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { ChangesTable } from "@/components/changes/changes-table";
import { listChanges } from "@/lib/api/changes";
import {
  CHANGE_CATEGORIES,
  byChangeHref,
  categoryLabel,
  type ByChangeQuery,
} from "@/lib/changes";

const PAGE_SIZE = 50;

export interface ByChangeParams {
  category?: string | undefined;
  attention?: string | undefined;
  offset?: string | undefined;
}

const CHIP =
  "rounded-full border px-2.5 py-0.5 text-xs transition-colors border-[var(--color-border-default)] hover:bg-[var(--color-bg-elevated)]";
const ACTIVE = "bg-[var(--color-bg-elevated)] text-[var(--color-text-primary)] font-medium";

type Href = (next: ByChangeQuery) => string;

/** The Needs attention toggle and the category chips; each chip keeps the other filter. */
function FilterChips({
  href,
  category,
  attention,
  categories,
  labels,
}: {
  href: Href;
  category: string | undefined;
  attention: boolean;
  categories: { value: string; label: string }[];
  labels: { needsAttention: string; category: string; allCategories: string };
}) {
  return (
    <div className="mb-4 flex flex-wrap items-center gap-2">
      <Link
        href={href({ category, attention: !attention })}
        aria-pressed={attention}
        className={`${CHIP} ${attention ? ACTIVE : ""}`}
      >
        {labels.needsAttention}
      </Link>
      <span aria-hidden className="mx-1 h-4 w-px bg-[var(--color-border-default)]" />
      <nav className="flex flex-wrap gap-2" aria-label={labels.category}>
        <Link href={href({ attention })} className={`${CHIP} ${category ? "" : ACTIVE}`}>
          {labels.allCategories}
        </Link>
        {categories.map((c) => (
          <Link
            key={c.value}
            href={href({ category: c.value, attention })}
            className={`${CHIP} ${category === c.value ? ACTIVE : ""}`}
          >
            {c.label}
          </Link>
        ))}
      </nav>
    </div>
  );
}

interface PagerLink {
  href: string;
  label: string;
}

/** "Showing n of total" and the newer / older links. */
function Pager({ showing, newer, older }: { showing: string; newer: PagerLink | null; older: PagerLink | null }) {
  return (
    <div className="mt-4 flex items-center justify-between text-xs text-[var(--color-text-secondary)]">
      <span>{showing}</span>
      <span className="flex gap-3">
        {newer && <Link href={newer.href}>{newer.label}</Link>}
        {older && <Link href={older.href}>{older.label}</Link>}
      </span>
    </div>
  );
}

/**
 * The Commits page grouped by merged change, with each change's kept
 * evidence checklist summarised. Server-rendered: filters and paging are
 * query parameters, so every view is a link. The list reads checklists the
 * change pages already built and never builds one itself, so it says how
 * many are built: a short Needs attention list is otherwise unreadable.
 */
export async function ByChangeView({ repoId, params }: { repoId: string; params: ByChangeParams }) {
  const t = await getTranslations("views.changes");
  const offset = Math.max(0, Number.parseInt(params.offset ?? "0", 10) || 0);
  const category = CHANGE_CATEGORIES.includes(params.category ?? "") ? params.category : undefined;
  const attention = params.attention === "1";

  const page = await listChanges(repoId, {
    limit: PAGE_SIZE,
    offset,
    category,
    evidence: true,
    needs_attention: attention || undefined,
  }).catch(() => null);

  // A failed read is an error, never an empty (clean-looking) list.
  if (page === null) {
    return <ApiError title={t("loadErrorTitle")} message={t("loadErrorBody")} />;
  }
  const isEmpty = page.total === 0 && !category && !attention;
  if (isEmpty) {
    return (
      <EmptyState
        title={t("emptyTitle")}
        description={t("emptyHint")}
        action={{ label: t("emptyAction"), href: `/repos/${repoId}/overview` }}
      />
    );
  }

  const href: Href = (next) => byChangeHref(repoId, next);
  const pageLink = (to: number, label: string): PagerLink => ({
    href: href({ category, attention, offset: to }),
    label,
  });
  const built = page.checklists_built;

  return (
    <OverviewSection title={t("byChangeTitle")} description={t("byChangeDescription")}>
      <FilterChips
        href={href}
        category={category}
        attention={attention}
        categories={CHANGE_CATEGORIES.map((c) => ({ value: c, label: categoryLabel(t, c) }))}
        labels={{
          needsAttention: t("needsAttention"),
          category: t("category"),
          allCategories: t("allCategories"),
        }}
      />
      {/* One scope line; how a checklist gets built is behind the tip. */}
      <p className="mb-3 flex items-center gap-1.5 text-xs text-[var(--color-text-tertiary)]">
        <span>
          {attention && `${t("attentionScope")} `}
          {built && t("checklistsBuilt", { built: built.built, changes: built.changes })}
        </span>
        <InfoTip content={t("checklistsBuildHint")} />
      </p>
      {page.items.length === 0 ? (
        <p className="py-6 text-sm text-[var(--color-text-secondary)]">
          {t(attention ? "attentionEmpty" : "filterEmpty")}
        </p>
      ) : (
        <ChangesTable repoId={repoId} rows={page.items} />
      )}
      <Pager
        showing={t("showing", { shown: page.items.length, total: page.total })}
        newer={offset > 0 ? pageLink(Math.max(0, offset - PAGE_SIZE), t("newer")) : null}
        older={offset + PAGE_SIZE < page.total ? pageLink(offset + PAGE_SIZE, t("older")) : null}
      />
    </OverviewSection>
  );
}
