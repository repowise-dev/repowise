import type { Metadata } from "next";
import Link from "next/link";
import { Users } from "lucide-react";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { OwnerAvatar } from "@repowise-dev/ui/owners/owner-avatar";
import { OwnerProfileView } from "@repowise-dev/ui/owners/owner-profile";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import { formatDate, formatRelativeTimeOrNull } from "@repowise-dev/ui/lib/format";
import { getOwnerProfile } from "@/lib/api/owners";

export const metadata: Metadata = { title: "Contributor" };

/**
 * A server component, where this was a client component behind one SWR wave.
 * The profile is a single fetch and nothing on the page is interactive, so the
 * whole thing arrives in the initial HTML instead of after a skeleton.
 *
 * Drill-ins are hrefs rather than `router.push` handlers, which is what lets
 * the page render on the server at all, and gives every file and contributor a
 * URL that survives a middle-click.
 */
export default async function OwnerProfilePage({
  params,
}: {
  params: Promise<{ id: string; owner: string }>;
}) {
  const { id, owner } = await params;
  const base = `/repos/${id}`;
  const ownerKey = decodeURIComponent(owner);

  const profile = await getOwnerProfile(id, ownerKey).catch(() => null);

  if (!profile) {
    return (
      <PageShell
        icon={<Users className="h-5 w-5 text-[var(--color-accent-primary)]" />}
        title="Contributor"
      >
        <EmptyState
          icon={<Users className="h-6 w-6" />}
          title="No profile for this contributor"
          description="Nobody by this name or address appears in the indexed git history. They may have committed under a different address, or the history may not have been synced yet."
        />
      </PageShell>
    );
  }

  const displayName = profile.name || profile.email || "unknown";
  const lastTouched = formatRelativeTimeOrNull(profile.last_commit_at);

  // Tenure as a sentence rather than a "new to this repo" badge. That badge
  // fired on anyone who joined after the repo's first 90 days, which on a young
  // repo is most people, and a marker that common marks nothing. A span of
  // dates says the same thing without dressing it as a verdict.
  const tenure = [
    profile.first_commit_at
      ? `Committing here since ${formatDate(profile.first_commit_at)}`
      : null,
    lastTouched ? `last touched ${lastTouched}` : null,
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <PageShell
      icon={<OwnerAvatar name={profile.name} email={profile.email} size="md" />}
      title={displayName}
      {...(tenure ? { description: tenure } : {})}
    >
      <OwnerProfileView
        owner={profile}
        base={base}
        hrefForFile={(path) => fileEntityPath(base, path)}
        hrefForModule={(mod) => `${base}/modules/${encodeURIComponent(mod)}`}
        hrefForCoAuthor={(c) =>
          `${base}/owners/${encodeURIComponent(c.email ?? `name:${c.name}`)}`
        }
        LinkComponent={Link}
      />
    </PageShell>
  );
}
