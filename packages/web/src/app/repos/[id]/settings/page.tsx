import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Settings } from "lucide-react";
import { WebhookSection } from "@/components/settings/webhook-section";
import { getRepo } from "@/lib/api/repos";
import { getCoordinatorHealth } from "@/lib/api/health";
import { RepoSettingsFormWrapper as RepoSettingsForm } from "@/components/repos/repo-settings-form-wrapper";
import { CoordinatorHealthPanel } from "@/components/repos/coordinator-health-panel";
import { DeleteRepoButton } from "@/components/repos/delete-repo-button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@repowise-dev/ui/ui/card";
import { Separator } from "@repowise-dev/ui/ui/separator";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { OperationsPanel } from "@/components/repos/operations-panel";

interface Props {
  params: Promise<{ id: string }>;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  try {
    const repo = await getRepo(id);
    return { title: `${repo.name} — Settings` };
  } catch {
    return { title: "Settings" };
  }
}

export default async function RepoSettingsPage({ params }: Props) {
  const { id } = await params;

  let repo;
  try {
    repo = await getRepo(id);
  } catch {
    notFound();
  }

  const coordinatorHealth = await getCoordinatorHealth(id).catch(() => null);

  return (
    <PageShell
      className="max-w-2xl"
      icon={<Settings className="h-5 w-5 text-[var(--color-accent-primary)]" />}
      title="Repository Settings"
      description={`Manage ${repo.name}`}
    >
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">General</CardTitle>
          <CardDescription>Name, branch, and path configuration</CardDescription>
        </CardHeader>
        <CardContent>
          <RepoSettingsForm repo={repo} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Sync & Indexing</CardTitle>
          <CardDescription>Trigger incremental sync or full re-indexing</CardDescription>
        </CardHeader>
        <CardContent className="pt-0">
          <OperationsPanel repoId={id} repoName={repo.name} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">System Health</CardTitle>
          <CardDescription>
            Per-population drift: wiki pages vs page vectors, and decision records vs decision
            vectors
          </CardDescription>
        </CardHeader>
        <CardContent className="pt-0">
          <CoordinatorHealthPanel repoId={id} initial={coordinatorHealth} />
        </CardContent>
      </Card>

      <Separator />

      {/* Shared with global settings — interpolates the real server URL. */}
      <WebhookSection />

      <p className="text-xs text-[var(--color-text-tertiary)]">
        Connection, provider and MCP configuration live in{" "}
        <Link
          href="/settings"
          className="text-[var(--color-accent-primary)] hover:underline"
        >
          global settings
        </Link>
        .
      </p>

      <Separator />

      <Card className="border-[var(--color-error)]/40 bg-[var(--color-error)]/5">
        <CardHeader>
          <CardTitle className="text-sm font-medium text-[var(--color-error)]">
            Danger Zone
          </CardTitle>
          <CardDescription>
            Permanently delete this repository and all its generated pages, symbols, and history.
            This cannot be undone.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <DeleteRepoButton repoId={id} repoName={repo.name} variant="button" redirectTo="/" />
        </CardContent>
      </Card>
    </PageShell>
  );
}
