"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type ReactNode } from "react";
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
  MOMENT_DISPLAY,
  MOMENT_EMPHASIS,
  MOMENT_ENTER,
  MOMENT_GROUND,
  MOMENT_MICRO,
  MomentBand,
} from "@/components/layout/moment-shell";
import { WelcomeStepRepo } from "./welcome-step-repo";
import { WelcomeStepAgent } from "./welcome-step-agent";
import { WelcomeStepYours } from "./welcome-step-yours";

const STEPS = ["repo", "agent", "yours"] as const;
type Step = (typeof STEPS)[number];

/** Below 561px the dialog is a full-height sheet; above, a centred dialog. */
const SHEET =
  "left-0 top-0 flex h-dvh max-h-none w-full max-w-none translate-x-0 translate-y-0 flex-col overflow-y-auto rounded-none border-0 " +
  "min-[561px]:left-[50%] min-[561px]:top-[50%] min-[561px]:h-auto min-[561px]:max-h-[85vh] min-[561px]:max-w-[720px] min-[561px]:translate-x-[-50%] min-[561px]:translate-y-[-50%] min-[561px]:rounded-[14px] min-[561px]:border";

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

/**
 * The three-step welcome: what repowise found, the agent that can read it,
 * and the optional parts. Built on the What's new shell with a plum-to-blue
 * band. Esc or the close button ends it at any step.
 */
export function WelcomeDialog(props: WelcomeDialogProps) {
  const { open, onOpenChange, repoId } = props;
  const t = useTranslations("welcome");
  const [index, setIndex] = useState(0);
  const step = STEPS[index]!;
  const heading = useRef<HTMLHeadingElement>(null);

  // Each opening starts at the first step.
  useEffect(() => {
    if (open) setIndex(0);
  }, [open]);

  // The heading names the step; moving focus there announces it.
  useEffect(() => {
    if (open) heading.current?.focus();
  }, [index, open]);

  const close = () => onOpenChange(false);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={`${MOMENT_GROUND} ${SHEET}`}
        onOpenAutoFocus={(e) => {
          e.preventDefault();
          heading.current?.focus();
        }}
      >
        <MomentBand tone="welcome" className="max-[560px]:px-[18px] max-[560px]:pb-4 max-[560px]:pt-[18px]">
          <p className={`${MOMENT_MICRO} pr-8`}>{t("eyebrow")}</p>
          <DialogTitle
            ref={heading}
            tabIndex={-1}
            className={`${MOMENT_DISPLAY} mt-3 font-normal leading-[1.08] outline-none max-[560px]:text-[29px] ${MOMENT_ENTER}`}
          >
            <StepTitle step={step} repoName={props.repoName} />
          </DialogTitle>
          <DialogDescription className="mt-2 text-sm text-[var(--color-text-secondary)] [text-wrap:pretty]">
            <StepLede step={step} fileCount={props.fileCount} years={props.years} />
          </DialogDescription>
          <StepIndicator index={index} />
        </MomentBand>

        <div className="flex-1 px-6 pb-2 pt-5 sm:px-8 max-[560px]:px-[18px]">
          {step === "repo" && (
            <WelcomeStepRepo repoId={repoId} lenses={props.lenses} onNavigate={close} />
          )}
          {step === "agent" && (
            <WelcomeStepAgent repoId={repoId} setup={props.setup} hotspotPath={props.hotspotPath} />
          )}
          {step === "yours" && <WelcomeStepYours repoId={repoId} setup={props.setup} />}
        </div>

        <footer className="sticky bottom-0 flex flex-col-reverse gap-3 border-t border-[var(--color-border-default)] bg-[var(--color-release-ground)] px-[18px] pb-[18px] pt-3 min-[561px]:flex-row min-[561px]:items-center min-[561px]:justify-between min-[561px]:border-t-0 min-[561px]:px-8 min-[561px]:pb-5 min-[561px]:pt-3.5">
          <span className="text-center text-xs text-[var(--color-text-tertiary)] min-[561px]:text-left">
            {t("stepOf", { step: index + 1, total: STEPS.length })}
          </span>
          <div className="flex flex-col-reverse gap-2 min-[561px]:flex-row">
            {index === 0 ? (
              <Button variant="ghost" size="sm" className="h-9 max-[560px]:w-full" onClick={close}>
                {t("skip")}
              </Button>
            ) : (
              <Button
                variant="ghost"
                size="sm"
                className="h-9 max-[560px]:w-full"
                onClick={() => setIndex(index - 1)}
              >
                {t("back")}
              </Button>
            )}
            {index < STEPS.length - 1 ? (
              <Button size="sm" className="h-9 max-[560px]:w-full" onClick={() => setIndex(index + 1)}>
                {index === 0 ? t("nextAgent") : t("nextYours")}
              </Button>
            ) : (
              <Button asChild size="sm" className="h-9 max-[560px]:w-full">
                <Link href={`/repos/${encodeURIComponent(repoId)}/settings/setup`} onClick={close}>
                  {t("finish")}
                </Link>
              </Button>
            )}
          </div>
        </footer>
      </DialogContent>
    </Dialog>
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

/** Three short bars with their names: done, current, then upcoming. */
function StepIndicator({ index }: { index: number }) {
  const t = useTranslations("welcome");
  return (
    <ol aria-label={t("stepsLabel")} className="mt-4 flex flex-wrap gap-x-4 gap-y-1 text-xs">
      {STEPS.map((s, i) => {
        const state = i < index ? "done" : i === index ? "current" : "next";
        return (
          <li
            key={s}
            aria-current={state === "current" ? "step" : undefined}
            className={`flex items-center gap-1.5 ${
              state === "current"
                ? "font-medium text-[var(--color-text-primary)]"
                : "text-[var(--color-text-tertiary)]"
            }`}
          >
            <span
              aria-hidden
              className={`inline-block h-0.5 w-4 ${
                state === "current"
                  ? "bg-[var(--color-text-primary)]"
                  : state === "done"
                    ? "bg-[var(--color-success)]"
                    : "bg-[var(--color-border-hover)]"
              }`}
            />
            {t(`steps.${s}`)}
            {state === "done" && <span className="sr-only">{t("stepDone")}</span>}
          </li>
        );
      })}
    </ol>
  );
}
