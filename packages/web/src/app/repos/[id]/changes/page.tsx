import type { Metadata } from "next";
import Link from "next/link";
import { GitPullRequest } from "lucide-react";
import { getTranslations } from "next-intl/server";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { ChangesTable } from "@/components/changes/changes-table";
import { listChanges } from "@/lib/api/changes";

export const metadata: Metadata = { title: "Changes" };

const PAGE_SIZE = 50;
const CATEGORIES = ["feature", "fix", "refactor", "docs", "test", "deps", "chore", "other"];

/**
 * Merged changes, newest first. A server component: filters and paging are
 * query parameters, so every view is a link.
 */
export default async function ChangesPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ offset?: string; category?: string }>;
}) {
  const { id } = await params;
  const sp = await searchParams;
  const offset = Math.max(0, Number.parseInt(sp.offset ?? "0", 10) || 0);
  const category = CATEGORIES.includes(sp.category ?? "") ? sp.category : undefined;
  const base = `/repos/${id}/changes`;
  const t = await getTranslations("views.changes");

  let page;
  try {
    page = await listChanges(id, { limit: PAGE_SIZE, offset, category });
  } catch {
    page = null;
  }

  if (!page || (page.total === 0 && !category)) {
    return (
      <PageShell icon={<GitPullRequest />} title={t("title")} description={t("description")}>
        <EmptyState
          title={t("emptyTitle")}
          description={t("emptyHint")}
          action={{ label: t("emptyAction"), href: `/repos/${id}/overview` }}
        />
      </PageShell>
    );
  }

  const href = (next: { offset?: number; category?: string | undefined }) => {
    const q = new URLSearchParams();
    if (next.category) q.set("category", next.category);
    if (next.offset) q.set("offset", String(next.offset));
    const s = q.toString();
    return s ? `${base}?${s}` : base;
  };
  const chip =
    "rounded-full border px-2.5 py-0.5 text-xs transition-colors border-[var(--color-border-default)] hover:bg-[var(--color-bg-elevated)]";
  const active = "bg-[var(--color-bg-elevated)] text-[var(--color-text-primary)] font-medium";

  return (
    <PageShell icon={<GitPullRequest />} title={t("title")} description={t("description")}>
      <nav className="mb-4 flex flex-wrap gap-2" aria-label={t("category")}>
        <Link href={href({})} className={`${chip} ${category ? "" : active}`}>
          {t("allCategories")}
        </Link>
        {CATEGORIES.map((c) => (
          <Link
            key={c}
            href={href({ category: c })}
            className={`${chip} ${category === c ? active : ""}`}
          >
            {c}
          </Link>
        ))}
      </nav>
      <ChangesTable
        repoId={id}
        rows={page.items}
        showProvenance={page.attribution}
        labels={{
          change: t("change"),
          merged: t("merged"),
          category: t("category"),
          files: t("files"),
          lines: t("lines"),
          title: t("title_col"),
          provenance: t("provenance"),
        }}
      />
      <div className="mt-4 flex items-center justify-between text-xs text-[var(--color-text-secondary)]">
        <span>{t("showing", { shown: page.items.length, total: page.total })}</span>
        <span className="flex gap-3">
          {offset > 0 && (
            <Link href={href({ category, offset: Math.max(0, offset - PAGE_SIZE) })}>
              {t("newer")}
            </Link>
          )}
          {offset + PAGE_SIZE < page.total && (
            <Link href={href({ category, offset: offset + PAGE_SIZE })}>{t("older")}</Link>
          )}
        </span>
      </div>
    </PageShell>
  );
}
