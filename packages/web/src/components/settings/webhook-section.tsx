"use client";

import { useState } from "react";
import { Copy, Check } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@repowise-dev/ui/ui/card";
import { Button } from "@repowise-dev/ui/ui/button";

function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="space-y-1">
      <p className="text-xs text-[var(--color-text-tertiary)]">{label}</p>
      <div className="flex items-center gap-2">
        <code className="flex-1 rounded border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-3 py-1.5 text-xs font-mono text-[var(--color-text-secondary)] truncate">
          {value}
        </code>
        <Button variant="ghost" size="icon" onClick={copy} className="h-7 w-7 shrink-0">
          {copied ? (
            <Check className="h-3.5 w-3.5 text-[var(--color-fresh)]" />
          ) : (
            <Copy className="h-3.5 w-3.5" />
          )}
        </Button>
      </div>
    </div>
  );
}

export function WebhookSection() {
  const serverUrl = "http://your-server:7337";

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Webhook Config</CardTitle>
        <CardDescription>
          Register these URLs in GitHub or GitLab to trigger automatic re-sync on push.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <CopyField label="GitHub Webhook URL" value={`${serverUrl}/api/webhooks/github`} />
        <CopyField label="GitLab Webhook URL" value={`${serverUrl}/api/webhooks/gitlab`} />

        <div className="rounded border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-3 space-y-1">
          <p className="text-xs font-medium text-[var(--color-text-secondary)]">
            Required env vars on the server
          </p>
          <p className="text-xs font-mono text-[var(--color-text-tertiary)]">
            REPOWISE_GITHUB_WEBHOOK_SECRET=your-secret
          </p>
          <p className="text-xs font-mono text-[var(--color-text-tertiary)]">
            REPOWISE_GITLAB_WEBHOOK_TOKEN=your-token
          </p>
          <p className="text-xs text-[var(--color-text-tertiary)] mt-1">
            Omit these vars to skip signature verification during development.
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
