"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Plus } from "lucide-react";
import { toast } from "sonner";
import { useSWRConfig } from "swr";
import { createRepo, preflightIndex, startIndexJob } from "@/lib/api/repos";
import { getProviders } from "@/lib/api/providers";
import { Button } from "@repowise-dev/ui/ui/button";
import {
  AddRepoWizard,
  type AddRepoWizardAdapter,
} from "@repowise-dev/ui/onboarding/add-repo-wizard";

interface Props {
  /** Render as a sidebar button (icon + label) vs standalone button */
  variant?: "sidebar" | "default";
  /** Controlled open state — pass both to drive the dialog from an external trigger. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Hide the built-in trigger button (for externally-triggered use). */
  showTrigger?: boolean;
}

export function AddRepoDialog({
  variant = "default",
  open: controlledOpen,
  onOpenChange,
  showTrigger = true,
}: Props) {
  const { mutate } = useSWRConfig();
  const router = useRouter();
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false);
  const open = controlledOpen ?? uncontrolledOpen;
  const setOpen = onOpenChange ?? setUncontrolledOpen;

  const adapter: AddRepoWizardAdapter = useMemo(
    () => ({
      createRepo: async (input) => {
        const repo = await createRepo({
          name: input.name,
          local_path: input.local_path,
          url: input.url,
          default_branch: input.default_branch,
          settings: input.wiki_style ? { wiki_style: input.wiki_style } : undefined,
          // Register first; the wizard runs preflight (provider check + cost
          // estimate) before committing to generation spend.
          index: false,
        });
        await mutate("/api/repos");
        return { id: repo.id, name: repo.name };
      },
      preflight: (repoId) => preflightIndex(repoId),
      startIndex: (repoId) => startIndexJob(repoId),
      getProviderStatus: async () => {
        const res = await getProviders();
        return res.active;
      },
      settingsHref: "/settings",
      onDone: (repoId, jobId) => {
        toast.success(jobId ? "Repository added, indexing started" : "Repository added");
        router.push(`/repos/${repoId}/overview`);
        router.refresh();
      },
    }),
    [mutate, router],
  );

  return (
    <>
      {!showTrigger ? null : variant === "sidebar" ? (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-xs text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-secondary)] transition-colors"
        >
          <Plus className="h-3.5 w-3.5 shrink-0" />
          <span>Add Repository</span>
        </button>
      ) : (
        <Button variant="default" size="sm" onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4 mr-1" />
          Add Repository
        </Button>
      )}

      <AddRepoWizard adapter={adapter} open={open} onOpenChange={setOpen} />
    </>
  );
}
