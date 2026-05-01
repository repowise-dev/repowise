"use client";

import { useState } from "react";
import { Copy, Check } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@repowise/ui/ui/card";
import { Button } from "@repowise/ui/ui/button";

const MCP_CONFIG = JSON.stringify(
  {
    mcpServers: {
      repowise: {
        command: "repowise",
        args: ["mcp", "/path/to/your/repo", "--transport", "stdio"],
      },
    },
  },
  null,
  2,
);

export function McpSection() {
  const [copied, setCopied] = useState(false);

  async function copy() {
    await navigator.clipboard.writeText(MCP_CONFIG);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">MCP Config</CardTitle>
        <CardDescription>
          Add to your Claude Code, Cursor, or Cline MCP config to enable AI-powered codebase Q&amp;A.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="relative">
          <pre className="rounded border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-3 text-xs font-mono text-[var(--color-text-secondary)] overflow-x-auto">
            {MCP_CONFIG}
          </pre>
          <Button
            variant="ghost"
            size="icon"
            onClick={copy}
            className="absolute right-2 top-2 h-7 w-7"
          >
            {copied ? (
              <Check className="h-3.5 w-3.5 text-[var(--color-fresh)]" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
          </Button>
        </div>
        <p className="text-xs text-[var(--color-text-tertiary)]">
          Replace <code className="font-mono">/path/to/your/repo</code> with the local path
          of the repository you want to query. Run{" "}
          <code className="font-mono">repowise init</code> first to generate documentation.
        </p>
      </CardContent>
    </Card>
  );
}
