"use client";

import { useState } from "react";
import { Plus } from "lucide-react";
import { useSWRConfig } from "swr";
import { createRepo } from "@/lib/api/repos";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";

interface Props {
  /** Render as a sidebar button (icon + label) vs standalone button */
  variant?: "sidebar" | "default";
}

export function AddRepoDialog({ variant = "default" }: Props) {
  const { mutate } = useSWRConfig();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [localPath, setLocalPath] = useState("");
  const [url, setUrl] = useState("");
  const [branch, setBranch] = useState("main");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setName("");
    setLocalPath("");
    setUrl("");
    setBranch("main");
    setError(null);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !localPath.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      await createRepo({
        name: name.trim(),
        local_path: localPath.trim(),
        url: url.trim() || undefined,
        default_branch: branch.trim() || "main",
      });
      // Invalidate the repos list so sidebar/dashboard refresh
      await mutate("/api/repos");
      setOpen(false);
      reset();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to add repository");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      {variant === "sidebar" ? (
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

      <Dialog open={open} onOpenChange={(v) => { setOpen(v); if (!v) reset(); }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Add Repository</DialogTitle>
          </DialogHeader>

          <form onSubmit={handleSubmit} className="space-y-4 py-2">
            <div className="space-y-1.5">
              <Label htmlFor="repo-name">Name</Label>
              <Input
                id="repo-name"
                placeholder="my-project"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="repo-path">Local Path</Label>
              <Input
                id="repo-path"
                placeholder="C:\Users\you\projects\my-project"
                value={localPath}
                onChange={(e) => setLocalPath(e.target.value)}
                className="font-mono"
                required
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="repo-url">
                Remote URL{" "}
                <span className="font-normal text-[var(--color-text-tertiary)]">(optional)</span>
              </Label>
              <Input
                id="repo-url"
                placeholder="https://github.com/org/repo"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="repo-branch">Default Branch</Label>
              <Input
                id="repo-branch"
                placeholder="main"
                value={branch}
                onChange={(e) => setBranch(e.target.value)}
              />
            </div>

            {error && (
              <p className="text-sm text-[var(--color-outdated)]">{error}</p>
            )}

            <DialogFooter>
              <Button
                type="button"
                variant="ghost"
                onClick={() => { setOpen(false); reset(); }}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={submitting || !name.trim() || !localPath.trim()}>
                {submitting ? "Adding…" : "Add Repository"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
