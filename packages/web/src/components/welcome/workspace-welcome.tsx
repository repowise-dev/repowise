"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { useSearchParams } from "next/navigation";
import useSWR from "swr";
import { useFormatter, useTranslations } from "next-intl";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@repowise-dev/ui/ui/dialog";
import { seriesColor } from "@repowise-dev/ui/shared/series";
import { workspacePrompt } from "@repowise-dev/ui/onboarding/first-prompts";
import { getOnboardingState, updateOnboardingState } from "@/lib/api/setup";
import { getWorkspaceSetup, type WorkspaceSetupResponse } from "@/lib/api/workspace";
import { getMetaVersion } from "@/lib/api/meta";
import { isNewBrowser, markVersionSeen, sessionMomentTaken, takeSessionMoment } from "@/lib/release-moment";
import { decideWelcome } from "@/lib/welcome-moment";
import {
  MOMENT_BODY,
  MOMENT_CAPTION,
  MOMENT_CARD,
  MOMENT_DISPLAY,
  MOMENT_EMPHASIS,
  MOMENT_ENTER,
  MOMENT_FIGURE,
  MOMENT_GROUND,
  MOMENT_GUTTER,
  MOMENT_LINK,
  MOMENT_MICRO,
  MOMENT_SHEET,
  MomentBand,
  MomentLabel,
} from "@/components/layout/moment-shell";
import { FIRST_CALL_POLL_MS, SIZE, StepFooter, advancesOnEnter } from "./welcome-dialog";
import { CopyQuestion, TIPS_AFTER_MS } from "./welcome-step-agent";

const STEPS = ["repos", "agent", "ask"] as const;
type Step = (typeof STEPS)[number];

/** The per-machine mark the workspace welcome stamps, beside the repo ids. */
export const WORKSPACE_WELCOME_KEY = "workspace";

/** Cross-repo figures the workspace landing already read on the server. */
export interface WorkspaceWelcomeFacts {
  /** The workspace's name; null when it has none. */
  name: string | null;
  /** Indexed members and their files. */
  repoCount: number;
  fileCount: number;
  /** Contract links between members; null when not read. */
  contractLinks: number | null;
  /** The pair that changed together most. */
  /** `sessions`: the work sessions the busiest cross-repo file pair shared. */
  topCoChange: { a: string; b: string; sessions: number } | null;
  /** The least healthy member, 0-10. */
  lowest: { name: string; score: number } | null;
  /** Members with no index yet. */
  notIndexed: string[];
  /** Indexed members' aliases, for the first questions. */
  members: string[];
}

/**
 * The workspace's welcome on `/workspace`: once per machine for a new
 * browser, or with `?tour=1`. Members then skip their own (see
 * `WelcomeMoment`). Shares the session's one interruption with What's new.
 */
export function WorkspaceWelcomeMoment({ facts }: { facts: WorkspaceWelcomeFacts }) {
  const search = useSearchParams()?.toString() ?? "";
  const [open, setOpen] = useState(false);
  const decided = useRef<string | null>(null);

  useEffect(() => {
    if (decided.current === search) return;
    decided.current = search;
    void decideWorkspaceWelcome(search, facts.repoCount).then((show) => {
      if (show) setOpen(true);
    });
  }, [search, facts.repoCount]);

  return <WorkspaceWelcomeDialog open={open} onOpenChange={setOpen} facts={facts} />;
}

/** Whether it opens, stamping what an automatic open records. */
async function decideWorkspaceWelcome(search: string, repoCount: number): Promise<boolean> {
  const state = await getOnboardingState().catch(() => null);
  const seen = state ? Boolean(state.welcome_seen?.[WORKSPACE_WELCOME_KEY]) : null;
  const mode = decideWelcome({
    search: search ? `?${search}` : "",
    fileCount: repoCount,
    seen,
    newBrowser: isNewBrowser(),
    sessionTaken: sessionMomentTaken(),
  });
  if (mode === "none") return false;
  if (mode === "auto") {
    takeSessionMoment();
    try {
      const meta = await getMetaVersion();
      if (meta.server_version) markVersionSeen(meta.server_version);
    } catch {
      /* What's new waits for the next session */
    }
  }
  if (mode === "auto" || seen === false) {
    await updateOnboardingState({ welcome_seen_repo: WORKSPACE_WELCOME_KEY }).catch(() => undefined);
  }
  dropTourParam();
  return true;
}

function dropTourParam(): void {
  const url = new URL(window.location.href);
  if (!url.searchParams.has("tour")) return;
  url.searchParams.delete("tour");
  window.history.replaceState(window.history.state, "", url);
}

/** The first call any member's index has seen; null while none has. */
export function firstWorkspaceCall(setup: WorkspaceSetupResponse | null | undefined): string | null {
  const calls = (setup?.repos ?? []).map((r) => r.first_call_at).filter((v): v is string => Boolean(v));
  return calls.sort()[0] ?? null;
}

export function WorkspaceWelcomeDialog({
  open,
  onOpenChange,
  facts,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  facts: WorkspaceWelcomeFacts;
}) {
  const t = useTranslations("welcome");
  const [index, setIndex] = useState(0);
  const step: Step = STEPS[index]!;
  const last = index === STEPS.length - 1;
  const heading = useRef<HTMLHeadingElement>(null);
  const body = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) setIndex(0);
  }, [open]);
  useEffect(() => {
    if (!open) return;
    body.current?.scrollTo?.({ top: 0 });
    heading.current?.focus();
  }, [index, open]);

  const { setup, firstCall } = useWorkspaceFirstCall(open, open && step === "agent");
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
        <div ref={body} className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
          <MomentBand tone="welcome">
            <p className={`${MOMENT_MICRO} pr-10`}>{t("eyebrow")}</p>
            <div key={step} className={MOMENT_ENTER}>
              <WorkspaceStepText step={step} facts={facts} headingRef={heading} />
            </div>
          </MomentBand>

          <div key={step} className={`${MOMENT_GUTTER} py-6 ${MOMENT_ENTER}`}>
            {step === "repos" && <WorkspaceCards facts={facts} onNavigate={close} />}
            {step === "agent" && <WorkspaceAgent setup={setup} firstCall={firstCall} />}
            {step === "ask" && <WorkspaceAsk members={facts.members} />}
          </div>
        </div>

        <StepFooter
          steps={STEPS}
          index={index}
          nextLabel={index === 0 ? t("nextAgent") : t("nextAsk")}
          finishHref="/workspace/setup"
          onStep={setIndex}
          onClose={close}
        />
      </DialogContent>
    </Dialog>
  );
}

/** The members' setup while the dialog is open; while `watching` (step 2),
 *  it checks for the first call until one lands on any member. */
function useWorkspaceFirstCall(open: boolean, watching: boolean) {
  const [polling, setPolling] = useState(false);
  const { data: setup } = useSWR(open ? ["workspace-setup", "welcome"] : null, () => getWorkspaceSetup(), {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
    refreshInterval: polling ? FIRST_CALL_POLL_MS : 0,
  });
  const firstCall = firstWorkspaceCall(setup);
  useEffect(() => {
    setPolling(watching && Boolean(setup) && !firstCall);
  }, [watching, setup, firstCall]);
  return { setup: setup ?? null, firstCall };
}

/** A step's title and lede in the band. */
function WorkspaceStepText({
  step,
  facts,
  headingRef,
}: {
  step: Step;
  facts: WorkspaceWelcomeFacts;
  headingRef: RefObject<HTMLHeadingElement | null>;
}) {
  const t = useTranslations("welcome");
  const em = (chunks: ReactNode) => <span style={MOMENT_EMPHASIS}>{chunks}</span>;
  return (
    <>
      <DialogTitle ref={headingRef} tabIndex={-1} className={`${MOMENT_DISPLAY} mt-3 outline-none`}>
        {step === "repos" &&
          (facts.name
            ? t.rich("workspace.title", { count: facts.repoCount, name: facts.name, em })
            : t.rich("workspace.titleNoName", { count: facts.repoCount, em }))}
        {step === "agent" && t.rich("agent.title", { em })}
        {step === "ask" && t.rich("ask.title", { em })}
      </DialogTitle>
      <DialogDescription
        className={`${MOMENT_BODY} mt-3 max-w-[56ch] text-[var(--color-text-secondary)] [text-wrap:pretty]`}
      >
        {step === "repos" && t("workspace.lede", { files: facts.fileCount })}
        {step === "agent" && t("workspace.agentLede")}
        {step === "ask" && t("ask.ledeWorkspace")}
      </DialogDescription>
    </>
  );
}

const mono = (chunks: ReactNode) => <span className="font-mono text-[0.95em]">{chunks}</span>;

/** Step 1: what the workspace view already measured across members. A card
 *  whose figure was not read is left out. */
function WorkspaceCards({ facts, onNavigate }: { facts: WorkspaceWelcomeFacts; onNavigate: () => void }) {
  const t = useTranslations("welcome.workspace");
  const format = useFormatter();
  const cards: { key: string; series: number; label: string; value: string; unit: string; caption: ReactNode; href: string; open: string }[] = [];
  if (facts.contractLinks != null && facts.contractLinks > 0) {
    cards.push({
      key: "contracts",
      series: 1,
      label: t("contracts.label"),
      value: format.number(facts.contractLinks),
      unit: t("contracts.unit"),
      caption: t("contracts.caption"),
      href: "/workspace/contracts",
      open: t("contracts.open"),
    });
  }
  if (facts.topCoChange) {
    cards.push({
      key: "coChanges",
      series: 3,
      label: t("coChanges.label"),
      value: format.number(facts.topCoChange.sessions),
      unit: t("coChanges.unit", { count: facts.topCoChange.sessions }),
      caption: t.rich("coChanges.caption", { a: facts.topCoChange.a, b: facts.topCoChange.b, mono }),
      href: "/workspace/co-changes",
      open: t("coChanges.open"),
    });
  }
  if (facts.lowest) {
    cards.push({
      key: "lowest",
      series: 2,
      label: t("lowest.label"),
      value: facts.lowest.score.toFixed(1),
      unit: "",
      caption: t.rich("lowest.caption", { name: facts.lowest.name, count: facts.repoCount, mono }),
      href: "/workspace",
      open: t("lowest.open"),
    });
  }
  if (facts.notIndexed.length > 0) {
    cards.push({
      key: "notIndexed",
      series: 4,
      label: t("notIndexed.label"),
      value: format.number(facts.notIndexed.length),
      unit: t("notIndexed.unit", { count: facts.notIndexed.length }),
      caption: t.rich("notIndexed.caption", {
        names: facts.notIndexed.slice(0, 3).join(", "),
        count: facts.notIndexed.length,
        mono,
      }),
      href: "/workspace",
      open: t("notIndexed.open"),
    });
  }
  if (cards.length === 0) return null;
  return (
    <section aria-labelledby="workspace-standout">
      <h3 id="workspace-standout" className={`${MOMENT_MICRO} mb-3`}>
        {t("standout")}
      </h3>
      <ul className="grid grid-cols-1 gap-3 min-[561px]:grid-cols-2 min-[561px]:[&>li:last-child:nth-child(odd)]:col-span-2">
        {cards.map((c) => (
          <li key={c.key} className={`${MOMENT_CARD} flex min-w-0 flex-col p-4`}>
            <MomentLabel color={seriesColor(c.series)}>{c.label}</MomentLabel>
            <p className="mt-2 flex items-baseline gap-2">
              <span className={MOMENT_FIGURE}>{c.value}</span>
              {c.unit && <span className="text-xs text-[var(--color-text-secondary)]">{c.unit}</span>}
            </p>
            <p className={`${MOMENT_CAPTION} mt-1 [overflow-wrap:anywhere]`}>{c.caption}</p>
            <Link href={c.href} onClick={onNavigate} className={`${MOMENT_LINK} mt-auto self-start pt-3`}>
              {c.open}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Step 2: one workspace server answers about every member, so the wait is
 *  for the first call on any of them. Tips show after two minutes. */
function WorkspaceAgent({
  setup,
  firstCall,
}: {
  setup: WorkspaceSetupResponse | null;
  firstCall: string | null;
}) {
  const t = useTranslations("welcome.agent");
  const tw = useTranslations("welcome.workspace");
  const format = useFormatter();
  const waiting = Boolean(setup) && !firstCall;
  const [tipsDue, setTipsDue] = useState(false);
  useEffect(() => {
    setTipsDue(false);
    if (!waiting) return;
    const timer = window.setTimeout(() => setTipsDue(true), TIPS_AFTER_MS);
    return () => window.clearTimeout(timer);
  }, [waiting]);

  const members = (setup?.repos ?? []).filter((r) => r.status === "available");
  return (
    <section aria-labelledby="workspace-agent">
      <h3 id="workspace-agent" className={`${MOMENT_MICRO} mb-3`}>
        {tw("membersLabel")}
      </h3>
      {members.length === 0 ? (
        <p className={MOMENT_CAPTION}>{tw("agentUnknown")}</p>
      ) : (
        <ul className="divide-y divide-[var(--color-border-default)]">
          {members.map((r) => (
            <li key={r.alias} className="flex items-center justify-between gap-4 py-3 first:pt-0">
              <span className={`${MOMENT_BODY} min-w-0 font-medium text-[var(--color-text-primary)] [overflow-wrap:anywhere]`}>
                {r.alias}
              </span>
              <span className="shrink-0 text-xs text-[var(--color-text-tertiary)]">
                {r.first_call_at ? t("firstCall", { when: format.relativeTime(new Date(r.first_call_at)) }) : t("waiting")}
              </span>
            </li>
          ))}
        </ul>
      )}
      {waiting && (
        <p role="status" className={`${MOMENT_CAPTION} mt-4`}>
          {tw("waitingNote")}
        </p>
      )}
      {tipsDue && (
        <div role="status" className="mt-3 space-y-1">
          <p className={`${MOMENT_CAPTION} font-medium text-[var(--color-text-primary)]`}>{t("tips.title")}</p>
          <p className={MOMENT_CAPTION}>{tw("tip")}</p>
          <p className={MOMENT_CAPTION}>{t.rich("tips.doctor", { mono: (c) => <span className="font-mono">{c}</span> })}</p>
        </div>
      )}
    </section>
  );
}

/** Step 3: the cross-repo question from the shared first prompts. */
function WorkspaceAsk({ members }: { members: string[] }) {
  const questions = members.length > 0 ? [workspacePrompt(members)] : [];
  if (questions.length === 0) return null;
  return (
    <ul className={`${MOMENT_CARD} divide-y divide-[var(--color-border-default)]`}>
      {questions.map((q) => (
        <li key={q} className="flex items-center justify-between gap-4 py-1 pl-4 pr-2">
          <span className={`${MOMENT_BODY} min-w-0 py-2 text-[var(--color-text-primary)] [overflow-wrap:anywhere]`}>{q}</span>
          <CopyQuestion text={q} />
        </li>
      ))}
    </ul>
  );
}
