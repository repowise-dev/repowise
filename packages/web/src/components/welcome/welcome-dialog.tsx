"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@repowise-dev/ui/ui/dialog";
import { Button } from "@repowise-dev/ui/ui/button";
import type { SetupResponse } from "@/lib/api/setup";
import type { WelcomeLens } from "@/lib/welcome-moment";
import {
  MOMENT_BODY,
  MOMENT_DISPLAY,
  MOMENT_EMPHASIS,
  MOMENT_ENTER,
  MOMENT_GROUND,
  MOMENT_GUTTER,
  MOMENT_MICRO,
  MOMENT_SHEET,
  MomentBand,
} from "@/components/layout/moment-shell";
import { WelcomeStepRepo } from "./welcome-step-repo";
import { WelcomeStepAgent } from "./welcome-step-agent";
import { WelcomeStepYours } from "./welcome-step-yours";

const STEPS = ["repo", "agent", "yours"] as const;
type Step = (typeof STEPS)[number];

/** One fixed height on a desktop, so the footer stays put between steps;
 *  the body scrolls inside it. A phone gets the full-height sheet. */
const SIZE = "min-[561px]:h-[min(680px,85vh)] min-[561px]:max-w-[720px]";

/** 44px on a phone, the compact 36px above. */
const FOOTER_BUTTON = "h-11 min-[561px]:h-9";

export interface WelcomeDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  repoId: string;
  repoName: string;
  fileCount: number;
  years: number | null;
  lenses: WelcomeLens[];
  setup: SetupResponse | null;
  /** The busiest file, for the second example question. */
  hotspotPath: string | null;
}

/** Enter on the dialog itself (not on a control inside it) moves on a step.
 *  Keydowns from portalled children (the model sheet, the sync confirm)
 *  bubble here through React, so the target must sit in this dialog's DOM. */
function advancesOnEnter(e: KeyboardEvent<HTMLElement>): boolean {
  const modified = [e.shiftKey, e.metaKey, e.ctrlKey, e.altKey, e.nativeEvent.isComposing].some(Boolean);
  if (e.key !== "Enter" || modified) return false;
  const target = e.target as HTMLElement;
  if (!e.currentTarget.contains(target)) return false;
  return !target.closest(
    "button, a, input, textarea, select, summary, [role=button], [role=switch], [role=radio], [contenteditable=true]",
  );
}

/**
 * The three-step welcome: what repowise found, the agent that can read it,
 * and the optional parts. Built on the What's new shell with a plum-to-blue
 * band. Esc or the close button ends it at any step; Enter moves on.
 */
export function WelcomeDialog(props: WelcomeDialogProps) {
  const { open, onOpenChange, repoId } = props;
  const t = useTranslations("welcome");
  const [index, setIndex] = useState(0);
  const step = STEPS[index]!;
  const last = index === STEPS.length - 1;
  const heading = useRef<HTMLHeadingElement>(null);
  const body = useRef<HTMLDivElement>(null);

  // Each opening starts at the first step.
  useEffect(() => {
    if (open) setIndex(0);
  }, [open]);

  // The heading names the step; moving focus there announces it. A new step
  // starts at the top of the scroll.
  useEffect(() => {
    if (!open) return;
    body.current?.scrollTo?.({ top: 0 });
    heading.current?.focus();
  }, [index, open]);

  const close = () => onOpenChange(false);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-moment
        className={`${MOMENT_GROUND} ${MOMENT_SHEET} ${SIZE}`}
        onOpenAutoFocus={(e) => {
          e.preventDefault();
          heading.current?.focus();
        }}
        onKeyDown={(e) => {
          if (!last && advancesOnEnter(e)) {
            e.preventDefault();
            setIndex(index + 1);
          }
        }}
      >
        {/* Band and body scroll; the footer and the close button stay put. */}
        <div ref={body} className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
          <MomentBand tone="welcome">
            <p className={`${MOMENT_MICRO} pr-10`}>{t("eyebrow")}</p>
            <div key={step} className={MOMENT_ENTER}>
              <DialogTitle ref={heading} tabIndex={-1} className={`${MOMENT_DISPLAY} mt-3 outline-none`}>
                <StepTitle step={step} repoName={props.repoName} />
              </DialogTitle>
              <DialogDescription
                className={`${MOMENT_BODY} mt-3 max-w-[56ch] text-[var(--color-text-secondary)] [text-wrap:pretty]`}
              >
                <StepLede step={step} fileCount={props.fileCount} years={props.years} />
              </DialogDescription>
            </div>
          </MomentBand>

          <div key={step} className={`${MOMENT_GUTTER} py-6 ${MOMENT_ENTER}`}>
            {step === "repo" && (
              <WelcomeStepRepo repoId={repoId} lenses={props.lenses} onNavigate={close} />
            )}
            {step === "agent" && (
              <WelcomeStepAgent repoId={repoId} setup={props.setup} hotspotPath={props.hotspotPath} />
            )}
            {step === "yours" && <WelcomeStepYours repoId={repoId} setup={props.setup} />}
          </div>
        </div>

        <StepFooter index={index} repoId={repoId} onStep={setIndex} onClose={close} />
      </DialogContent>
    </Dialog>
  );
}

/** Stays put across steps: the step indicator, then Skip or Back and the one
 *  ink primary on the right. */
function StepFooter({
  index,
  repoId,
  onStep,
  onClose,
}: {
  index: number;
  repoId: string;
  onStep: (index: number) => void;
  onClose: () => void;
}) {
  const t = useTranslations("welcome");
  const primary = `${FOOTER_BUTTON} flex-1 min-[561px]:flex-none`;
  return (
    <footer
      className={`${MOMENT_GUTTER} flex shrink-0 flex-col gap-3 border-t border-[var(--color-border-default)] bg-[var(--color-release-ground)] pb-[max(1rem,env(safe-area-inset-bottom))] pt-4 min-[561px]:flex-row min-[561px]:items-center min-[561px]:justify-between`}
    >
      <StepIndicator index={index} />
      <div className="flex gap-2">
        {index === 0 ? (
          <Button variant="ghost" className={FOOTER_BUTTON} onClick={onClose}>
            {t("skip")}
          </Button>
        ) : (
          <Button variant="ghost" className={FOOTER_BUTTON} onClick={() => onStep(index - 1)}>
            {t("back")}
          </Button>
        )}
        {index < STEPS.length - 1 ? (
          <Button className={primary} onClick={() => onStep(index + 1)}>
            {index === 0 ? t("nextAgent") : t("nextYours")}
          </Button>
        ) : (
          <Button asChild className={primary}>
            <Link href={`/repos/${encodeURIComponent(repoId)}/settings/setup`} onClick={onClose}>
              {t("finish")}
            </Link>
          </Button>
        )}
      </div>
    </footer>
  );
}

function StepTitle({ step, repoName }: { step: Step; repoName: string }) {
  const t = useTranslations("welcome");
  const em = (chunks: ReactNode) => <span style={MOMENT_EMPHASIS}>{chunks}</span>;
  if (step === "repo") return <>{t.rich("repo.title", { name: repoName, em })}</>;
  if (step === "agent") return <>{t.rich("agent.title", { em })}</>;
  return <>{t.rich("yours.title", { em })}</>;
}

function StepLede({ step, fileCount, years }: { step: Step; fileCount: number; years: number | null }) {
  const t = useTranslations("welcome");
  if (step === "agent") return <>{t("agent.lede")}</>;
  if (step === "yours") return <>{t("yours.lede")}</>;
  return (
    <>
      {years != null
        ? t("repo.lede", { files: fileCount, years })
        : t("repo.ledeNoHistory", { files: fileCount })}
    </>
  );
}

/** Three short segments, filled up to the current step, then the count. The
 *  step names are for screen readers; the heading names the step on screen. */
function StepIndicator({ index }: { index: number }) {
  const t = useTranslations("welcome");
  return (
    <div className="flex items-center gap-3">
      <ol aria-label={t("stepsLabel")} className="flex items-center gap-1">
        {STEPS.map((s, i) => {
          const state = i < index ? "done" : i === index ? "current" : "next";
          return (
            <li key={s} aria-current={state === "current" ? "step" : undefined} className="flex">
              <span
                aria-hidden
                className={`block h-1 rounded-full motion-safe:transition-[width,background-color] motion-safe:duration-200 motion-safe:ease-out ${
                  state === "current" ? "w-6" : "w-3"
                } ${state === "next" ? "bg-[var(--color-border-hover)]" : "bg-[var(--color-text-primary)]"}`}
              />
              <span className="sr-only">
                {t(`steps.${s}`)}
                {state === "done" && ` ${t("stepDone")}`}
              </span>
            </li>
          );
        })}
      </ol>
      <span className="text-xs tabular-nums text-[var(--color-text-tertiary)]">
        {t("stepOf", { step: index + 1, total: STEPS.length })}
      </span>
    </div>
  );
}
