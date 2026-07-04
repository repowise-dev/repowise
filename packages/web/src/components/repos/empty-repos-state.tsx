"use client";

import { useState } from "react";
import { FolderGit2 } from "lucide-react";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { AddRepoDialog } from "@/components/repos/add-repo-dialog";

/**
 * First-run state for the dashboard's repository list: a one-click path into
 * the add-repository dialog, with the CLI and docs as secondary routes.
 */
export function EmptyReposState() {
  const [open, setOpen] = useState(false);

  return (
    <div className="space-y-2">
      <EmptyState
        title="No repositories yet"
        description="Point repowise at a local git repository to build its index, docs, and health signals."
        icon={<FolderGit2 className="h-8 w-8" />}
        action={{ label: "Add repository", onClick: () => setOpen(true) }}
      />
      <p className="text-center text-xs text-[var(--color-text-tertiary)]">
        Prefer the terminal? Run <code className="font-mono">repowise init</code> in a repo, or see the{" "}
        <a
          href="https://github.com/repowise-dev/repowise#quick-start"
          target="_blank"
          rel="noreferrer"
          className="text-[var(--color-accent-primary)] hover:underline"
        >
          setup guide
        </a>
        .
      </p>
      <AddRepoDialog showTrigger={false} open={open} onOpenChange={setOpen} />
    </div>
  );
}
