"use client";

import { useState } from "react";
import { Button } from "@repowise-dev/ui/ui/button";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { publishRepo, type PublishResult } from "@/lib/api/platform";
import { useHostedIdentity } from "@/lib/hooks/use-hosted-identity";

/** Runs `repowise publish` for one repo through the local server. The CLI
 *  owns the decision and every message; this only holds the request state. */
function usePublish(repoId: string) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<PublishResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
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

  return { busy, result, error, run };
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
  const { busy, result, error, run } = usePublish(repoId);
  // A server without the endpoint answers nothing; no button beats a broken one.
  if (!identity) return null;

  return (
    <div className="flex flex-col gap-3">
      {identity.signed_in ? (
        <div>
          <Button size="sm" onClick={run} disabled={busy}>
            {busy ? "Publishing…" : "Publish to repowise.dev"}
          </Button>
        </div>
      ) : (
        <p className="text-sm text-[var(--color-text-secondary)]">{SIGN_IN_FIRST}</p>
      )}
      <PublishOutcome result={result} error={error} />
      <p className="text-xs text-[var(--color-text-tertiary)]">
        Only what&apos;s pushed to GitHub is published. Public repos are free (up to 2);
        private repos need Pro, free for 10 days, card required.
      </p>
    </div>
  );
}

/** A tip's free next step on a repo page: the same one-click publish. */
export function PublishItFree({ repoId }: { repoId: string }) {
  const { busy, result, error, run } = usePublish(repoId);
  return (
    <span className="flex flex-col gap-1">
      <button
        type="button"
        onClick={run}
        disabled={busy}
        className="self-start font-medium text-[var(--color-accent-primary)] hover:underline disabled:opacity-60"
      >
        {busy ? "Publishing…" : "Publish it free"}
      </button>
      <PublishOutcome result={result} error={error} />
    </span>
  );
}
