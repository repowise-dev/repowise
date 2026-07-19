"use client";

import { useState } from "react";
import { MessageSquarePlus, Lock } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@repowise-dev/ui/ui/dialog";
import { Button } from "@repowise-dev/ui/ui/button";
import { toast } from "sonner";
import { submitFeedback, type FeedbackCategory } from "@/lib/api/feedback";

const CATEGORIES: { value: FeedbackCategory; label: string }[] = [
  { value: "ui_ux", label: "UI / UX" },
  { value: "bug", label: "Bug" },
  { value: "feature_request", label: "Feature request" },
  { value: "other", label: "Other" },
];

const MAX_LENGTH = 4000;

/**
 * Sidebar-footer feedback entry point for the self-hosted dashboard. Opens a
 * categorised dialog; submissions POST to the local server's `/api/feedback`,
 * which forwards them to the Repowise maintainers. Works without an account.
 */
export function FeedbackButton() {
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState<FeedbackCategory>("ui_ux");
  const [message, setMessage] = useState("");
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const reset = () => {
    setCategory("ui_ux");
    setMessage("");
    setEmail("");
  };

  const handleOpenChange = (next: boolean) => {
    if (submitting) return;
    setOpen(next);
    if (!next) reset();
  };

  const handleSubmit = async () => {
    const trimmed = message.trim();
    if (!trimmed) {
      toast.error("Please enter your feedback.");
      return;
    }
    setSubmitting(true);
    try {
      await submitFeedback({
        category,
        message: trimmed,
        ...(email.trim() && { email: email.trim() }),
        ...(typeof window !== "undefined" && { pageUrl: window.location.href }),
      });
      toast.success("Thanks for the feedback!");
      setOpen(false);
      reset();
    } catch {
      toast.error("Couldn't send feedback. Please try again.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="Help us improve Repowise"
        className="flex w-full items-center gap-2 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-2 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-accent-primary)]/50 hover:bg-[var(--color-accent-muted)] hover:text-[var(--color-text-primary)]"
      >
        <MessageSquarePlus className="h-4 w-4 shrink-0 text-[var(--color-accent-primary)]" />
        <span>Help us improve Repowise</span>
      </button>

      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Help us improve Repowise</DialogTitle>
            <DialogDescription>
              Found a bug or have an idea? It goes straight to the maintainers, and we read every
              message. It&apos;s anonymous by default — no account needed.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            {/* Category */}
            <div>
              <label className="mb-1.5 block text-xs font-medium text-[var(--color-text-tertiary)]">
                Category
              </label>
              <div className="flex flex-wrap gap-2">
                {CATEGORIES.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => setCategory(option.value)}
                    className={`cursor-pointer rounded-full border px-3 py-1.5 text-xs font-medium transition-colors ${
                      category === option.value
                        ? "border-[var(--color-accent-primary)] bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
                        : "border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:border-[var(--color-text-tertiary)]"
                    }`}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Message */}
            <div>
              <label
                htmlFor="feedback-message"
                className="mb-1.5 block text-xs font-medium text-[var(--color-text-tertiary)]"
              >
                Your feedback
              </label>
              <textarea
                id="feedback-message"
                rows={5}
                autoFocus
                maxLength={MAX_LENGTH}
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder="Tell us what's working, what's broken, or what you'd love to see..."
                className="w-full resize-none rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-2.5 text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] focus:border-[var(--color-accent-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-accent-primary)]"
              />
              <p className="mt-1 text-right text-[11px] text-[var(--color-text-tertiary)]">
                {message.length}/{MAX_LENGTH}
              </p>
            </div>

            {/* Optional email — so the maintainers can reply */}
            <div>
              <label
                htmlFor="feedback-email"
                className="mb-1.5 block text-xs font-medium text-[var(--color-text-tertiary)]"
              >
                Email{" "}
                <span className="font-normal text-[var(--color-text-tertiary)]">
                  (optional, if you&apos;d like a reply)
                </span>
              </label>
              <input
                id="feedback-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                className="w-full rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-2 text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] focus:border-[var(--color-accent-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-accent-primary)]"
              />
            </div>
          </div>

          <div className="flex items-start gap-1.5 text-[11px] text-[var(--color-text-tertiary)]">
            <Lock className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
            <span>
              Submitted anonymously. We only see your email if you add one above.
            </span>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => handleOpenChange(false)} disabled={submitting}>
              Cancel
            </Button>
            <Button onClick={handleSubmit} disabled={submitting || !message.trim()}>
              {submitting ? "Sending…" : "Send feedback"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
