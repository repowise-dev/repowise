"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Trash2, AlertTriangle } from "lucide-react";
import { deleteRepo } from "@/lib/api/repos";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";

interface DeleteRepoButtonProps {
  repoId: string;
  repoName: string;
  variant?: "icon" | "button";
  redirectTo?: string;
}

export function DeleteRepoButton({
  repoId,
  repoName,
  variant = "icon",
  redirectTo,
}: DeleteRepoButtonProps) {
  const [open, setOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const router = useRouter();

  async function handleDelete() {
    setDeleting(true);
    try {
      const result = await deleteRepo(repoId);
      toast.success(`Deleted ${repoName} — ${result.deleted_pages} pages removed`);
      setOpen(false);
      if (redirectTo) {
        router.push(redirectTo);
      } else {
        router.refresh();
      }
    } catch (err) {
      toast.error(`Failed to delete: ${err instanceof Error ? err.message : "Unknown error"}`);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <>
      {variant === "button" ? (
        <Button
          variant="destructive"
          size="sm"
          onClick={() => setOpen(true)}
        >
          <Trash2 className="h-3.5 w-3.5 mr-1.5" />
          Delete Repository
        </Button>
      ) : (
        <button
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setOpen(true);
          }}
          className="opacity-0 group-hover:opacity-100 p-1 text-[var(--color-text-tertiary)] hover:text-red-400 transition-all"
          title="Delete repository"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-[var(--color-stale)]" />
              Delete Repository
            </DialogTitle>
          </DialogHeader>
          <p className="text-sm text-[var(--color-text-secondary)]">
            This will permanently delete{" "}
            <span className="font-medium text-[var(--color-text-primary)]">{repoName}</span>{" "}
            and all its generated pages, symbols, and history.
          </p>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleDelete} disabled={deleting}>
              {deleting ? "Deleting..." : "Delete Repository"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
