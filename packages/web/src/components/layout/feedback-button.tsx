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
import { useTranslations } from "next-intl";

/** The categories in the order they are offered. The labels are read from the
 *  active locale inside the component, so the list is built there. */
const CATEGORY_VALUES: FeedbackCategory[] = [
  "ui_ux",
  "bug",
  "feature_request",
  "other",
];

const MAX_LENGTH = 4000;

/** Suffix per category, so the label keys stay greppable and typed. */
const CATEGORY_KEYS: Record<FeedbackCategory, string> = {
  ui_ux: "UiUx",
  bug: "Bug",
  feature_request: "Feature",
  other: "Other",
};

/**
 * Sidebar-footer feedback entry point for the self-hosted dashboard. Opens a
 * categorised dialog; submissions POST to the local server's `/api/feedback`,
 * which forwards them to the Repowise maintainers. Works without an account.
 */
export function FeedbackButton() {
  const t = useTranslations("shell");
  const tc = useTranslations("common");
  const categories = CATEGORY_VALUES.map((value) => ({
    value,
    label: t(`feedback.category${CATEGORY_KEYS[value]}`),
  }));
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
      toast.error(t("feedback.empty"));
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
      toast.success(t("feedback.thanks"));
      setOpen(false);
      reset();
    } catch {
      toast.error(t("feedback.failed"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={t("feedback.title")}
        className="flex w-full items-center gap-2 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-2 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-accent-primary)]/50 hover:bg-[var(--color-accent-muted)] hover:text-[var(--color-text-primary)]"
      >
        <MessageSquarePlus className="h-4 w-4 shrink-0 text-[var(--color-accent-primary)]" />
        <span>{t("feedback.title")}</span>
      </button>

      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{t("feedback.title")}</DialogTitle>
            <DialogDescription>{t("feedback.description")}</DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            {/* Category */}
            <div>
              <label className="mb-1.5 block text-xs font-medium text-[var(--color-text-tertiary)]">
                {t("feedback.category")}
              </label>
              <div className="flex flex-wrap gap-2">
                {categories.map((option) => (
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
                {t("feedback.messageLabel")}
              </label>
              <textarea
                id="feedback-message"
                rows={5}
                autoFocus
                maxLength={MAX_LENGTH}
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder={t("feedback.messagePlaceholder")}
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
                {t("feedback.emailLabel")}{" "}
                <span className="font-normal text-[var(--color-text-tertiary)]">
                  {t("feedback.emailOptional")}
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
            <span>{t("feedback.anonymous")}</span>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => handleOpenChange(false)} disabled={submitting}>
              {tc("cancel")}
            </Button>
            <Button onClick={handleSubmit} disabled={submitting || !message.trim()}>
              {submitting ? t("feedback.sending") : t("feedback.send")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
