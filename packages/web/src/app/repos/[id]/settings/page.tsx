import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Settings } from "lucide-react";
import { getRepo } from "@/lib/api/repos";
import { getCoordinatorHealth } from "@/lib/api/health";
import { RepoSettingsForm } from "@/components/repos/repo-settings-form";
import { CoordinatorHealthPanel } from "@/components/repos/coordinator-health-panel";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@repowise/ui/ui/card";
import { Separator } from "@repowise/ui/ui/separator";
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
    <div className="p-4 sm:p-6 space-y-6 max-w-2xl">
      <div>
        <h1 className="text-xl font-semibold text-[var(--color-text-primary)] flex items-center gap-2">
          <Settings className="h-5 w-5 text-[var(--color-accent-primary)]" />
          Repository Settings
        </h1>
        <p className="text-sm text-[var(--color-text-secondary)] mt-0.5">
          Manage {repo.name}
        </p>
      </div>

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
          <CardDescription>Coordinator drift across SQL, vector, and graph stores</CardDescription>
        </CardHeader>
        <CardContent className="pt-0">
          <CoordinatorHealthPanel repoId={id} initial={coordinatorHealth} />
        </CardContent>
      </Card>

      <Separator />

      <div>
        <h2 className="text-sm font-medium text-[var(--color-text-primary)] mb-1">Webhook URLs</h2>
        <p className="text-xs text-[var(--color-text-tertiary)] mb-3">
          Configure your repository host to call these endpoints on push events.
        </p>
        <div className="space-y-2">
          {(["github", "gitlab"] as const).map((host) => (
            <div key={host} className="rounded-md bg-[var(--color-bg-inset)] px-3 py-2">
              <p className="text-xs font-medium text-[var(--color-text-tertiary)] mb-0.5 capitalize">
                {host}
              </p>
              <p className="text-xs font-mono text-[var(--color-text-secondary)] break-all">
                {`[your-server]/api/webhooks/${host}`}
              </p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
