"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Trash2, AlertTriangle } from "lucide-react";
import { deleteRepo } from "@/lib/api/repos";
import { Button } from "@repowise-dev/ui/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@repowise-dev/ui/ui/dialog";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { useTranslations } from "next-intl";

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
  const t = useTranslations("repos");
  const tc = useTranslations("common");

  async function handleDelete() {
    setDeleting(true);
    try {
      const result = await deleteRepo(repoId);
      toast.success(
        t("delete.toastDeleted", {
          name: repoName,
          pages: result.deleted_pages,
        }),
      );
      setOpen(false);
      if (redirectTo) {
        router.push(redirectTo);
      } else {
        router.refresh();
      }
    } catch (err) {
      toast.error(t("delete.toastFailed", { error: toFriendlyMessage(err) }));
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
          {t("delete.button")}
        </Button>
      ) : (
        <button
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setOpen(true);
          }}
          className="p-1 text-[var(--color-text-tertiary)] hover:text-[var(--color-error)] transition-all md:opacity-0 md:group-hover:opacity-100 md:focus-visible:opacity-100"
          title={t("delete.title")}
          aria-label={t("delete.aria", { name: repoName })}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-[var(--color-stale)]" />
              {t("delete.button")}
            </DialogTitle>
          </DialogHeader>
          <p className="text-sm text-[var(--color-text-secondary)]">
            {t.rich("delete.confirm", {
              name: repoName,
              repo: (chunks) => (
                <span className="font-medium text-[var(--color-text-primary)]">
                  {chunks}
                </span>
              ),
            })}
          </p>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              {tc("cancel")}
            </Button>
            <Button variant="destructive" onClick={handleDelete} disabled={deleting}>
              {deleting ? t("delete.deleting") : t("delete.button")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
