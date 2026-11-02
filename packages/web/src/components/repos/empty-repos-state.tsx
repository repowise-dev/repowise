"use client";

import { useState } from "react";
import { FolderGit2 } from "lucide-react";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { AddRepoDialog } from "@/components/repos/add-repo-dialog";
import { useTranslations } from "next-intl";

/**
 * First-run state for the dashboard's repository list: a one-click path into
 * the add-repository dialog, with the CLI and docs as secondary routes.
 */
export function EmptyReposState() {
  const t = useTranslations("repos");
  const [open, setOpen] = useState(false);

  return (
    <div className="space-y-2">
      <EmptyState
        title={t("empty.title")}
        description={t("empty.description")}
        icon={<FolderGit2 className="h-8 w-8" />}
        action={{ label: t("empty.action"), onClick: () => setOpen(true) }}
      />
      <p className="text-center text-xs text-[var(--color-text-tertiary)]">
        {t.rich("empty.terminalHint", {
          code: (chunks) => <code className="font-mono">{chunks}</code>,
          link: (chunks) => (
            <a
              href="https://github.com/repowise-dev/repowise#quick-start"
              target="_blank"
              rel="noreferrer"
              className="text-[var(--color-accent-primary)] hover:underline"
            >
              {chunks}
            </a>
          ),
        })}
      </p>
      <AddRepoDialog showTrigger={false} open={open} onOpenChange={setOpen} />
    </div>
  );
}
