"use client";

import { useEffect, useState } from "react";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { Button } from "@repowise-dev/ui/ui/button";
import { ConfirmDialog } from "@repowise-dev/ui/ui/confirm-dialog";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { publishRepo, type PublishResult } from "@/lib/api/platform";
import { config } from "@/lib/config";
import { useHostedIdentity } from "@/lib/hooks/use-hosted-identity";

/** Runs `repowise publish` for one repo through the local server, after the
 *  person confirms. The CLI owns the decision and every message; this only
 *  holds the request state and the confirmation. */
function usePublish(repoId: string) {
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState<PublishResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setConfirming(false);
    setBusy(true);
    setError(null);
    try {
      setResult(await publishRepo(repoId));
    } catch (e) {
      setResult(null);
      setError(toFriendlyMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const confirmDialog = (
    <ConfirmDialog
      open={confirming}
      onOpenChange={setConfirming}
      title="Publish to repowise.dev?"
      description="repowise.dev will index this repo from its GitHub remote. Only what's pushed to GitHub is used; nothing on this machine is uploaded."
      confirmLabel="Publish"
      onConfirm={run}
    />
  );

  return { busy, result, error, ask: () => setConfirming(true), confirmDialog };
}

function PublishOutcome({ result, error }: { result: PublishResult | null; error: string | null }) {
  if (error) {
    return (
      <p role="status" className="text-xs text-[var(--color-error)]">
        {error}
      </p>
    );
  }
  if (!result) return null;
  return (
    <div role="status" className="flex flex-col gap-1 text-xs text-[var(--color-text-secondary)]">
      <p className="text-[var(--color-text-primary)]">{result.message}</p>
      {result.url && (
        <a
          href={result.url}
          target="_blank"
          rel="noopener noreferrer"
          className="break-all text-[var(--color-accent-primary)] hover:underline"
        >
          {result.url}
        </a>
      )}
      {result.details.map((line) => (
        <p key={line} className="break-words text-[var(--color-text-tertiary)]">
          {line}
        </p>
      ))}
    </div>
  );
}

const SIGN_IN_FIRST = (
  <>
    Sign in first: run{" "}
    <code className="rounded bg-[var(--color-bg-elevated)] px-1.5 py-0.5 text-xs">
      repowise login
    </code>{" "}
    in a terminal.
  </>
);

/** The "Publish to repowise.dev" button with its result, for a repo page. */
export function PublishPanel({ repoId }: { repoId: string }) {
  const { identity } = useHostedIdentity();
  const { busy, result, error, ask, confirmDialog } = usePublish(repoId);
  // A server without the endpoint answers nothing; no button beats a broken one.
  if (!identity) return null;

  return (
    <div className="flex flex-col gap-3">
      {identity.signed_in ? (
        <div>
          <Button size="sm" onClick={ask} disabled={busy}>
            {busy ? "Publishing…" : "Publish to repowise.dev"}
          </Button>
        </div>
      ) : (
        <p className="text-sm text-[var(--color-text-secondary)]">{SIGN_IN_FIRST}</p>
      )}
      <PublishOutcome result={result} error={error} />
      <p className="text-xs text-[var(--color-text-tertiary)]">
        {/* True on every plan: the panel doesn't know which one this account has. */}
        Only what&apos;s pushed to GitHub is published. On a free account, public repos are
        free (up to 2); private repos and more repos need Pro, free for 10 days, card required.
      </p>
      {confirmDialog}
    </div>
  );
}

/** The panel as a repo overview section. Unlike repo settings, the overview
 *  is not where someone goes to publish, so it follows the tips switches. */
export function PublishOverviewSection({ repoId }: { repoId: string }) {
  const { identity } = useHostedIdentity();
  // Off until read after mount, so SSR and the first client render agree.
  const [tipsShown, setTipsShown] = useState(false);
  useEffect(() => setTipsShown(!config.getHostedTipsHidden()), []);
  if (!identity?.hints_enabled || !tipsShown) return null;

  return (
    <OverviewSection title="Publish on repowise.dev">
      <PublishPanel repoId={repoId} />
    </OverviewSection>
  );
}

/** A tip's free next step on a repo page: the same publish, confirmed first. */
export function PublishItFree({ repoId }: { repoId: string }) {
  const { busy, result, error, ask, confirmDialog } = usePublish(repoId);
  return (
    <span className="flex flex-col gap-1">
      <button
        type="button"
        onClick={ask}
        disabled={busy}
        className="self-start font-medium text-[var(--color-accent-primary)] hover:underline disabled:opacity-60"
      >
        {busy ? "Publishing…" : "Publish it free"}
      </button>
      <PublishOutcome result={result} error={error} />
      {confirmDialog}
    </span>
  );
}
