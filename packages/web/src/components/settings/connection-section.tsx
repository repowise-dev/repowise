"use client";

import { useState, useEffect, useRef } from "react";
import { CheckCircle, XCircle, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { config } from "@/lib/config";
import { getHealth } from "@/lib/api/health";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@repowise/ui/ui/card";
import { Label } from "@repowise/ui/ui/label";
import { Input } from "@repowise/ui/ui/input";
import { Button } from "@repowise/ui/ui/button";
import type { HealthResponse } from "@/lib/api/types";

export function ConnectionSection() {
  const [apiUrl, setApiUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [testing, setTesting] = useState(false);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setApiUrl(config.getApiUrl());
    setApiKey(config.getApiKey());
  }, []);

  const initialUrlRef = useRef("");
  const initialKeyRef = useRef("");

  useEffect(() => {
    initialUrlRef.current = config.getApiUrl();
    initialKeyRef.current = config.getApiKey();
  }, []);

  function save() {
    const changed =
      apiUrl !== initialUrlRef.current || apiKey !== initialKeyRef.current;
    config.setApiUrl(apiUrl);
    config.setApiKey(apiKey);
    if (changed) {
      initialUrlRef.current = apiUrl;
      initialKeyRef.current = apiKey;
      toast.success("Connection settings saved");
    }
  }

  async function testConnection() {
    save();
    setTesting(true);
    setHealth(null);
    setError(null);
    try {
      const h = await getHealth();
      setHealth(h);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Connection failed");
    } finally {
      setTesting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">API Connection</CardTitle>
        <CardDescription>
          Configure how the UI connects to your repowise server.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="api-url">Server URL</Label>
          <Input
            id="api-url"
            placeholder="http://localhost:7337"
            value={apiUrl}
            onChange={(e) => setApiUrl(e.target.value)}
            onBlur={save}
            className="font-mono"
          />
          <p className="text-xs text-[var(--color-text-tertiary)]">
            Leave blank to use the same origin (when proxied via Next.js).
          </p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="api-key">API Key</Label>
          <Input
            id="api-key"
            type="password"
            placeholder="Optional — only required if REPOWISE_API_KEY is set on the server"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            onBlur={save}
            className="font-mono"
          />
        </div>

        <div className="flex items-center gap-3">
          <Button
            variant="outline"
            size="sm"
            onClick={testConnection}
            disabled={testing}
          >
            {testing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              "Test Connection"
            )}
          </Button>

          {health && (
            <span className="flex items-center gap-1.5 text-sm text-[var(--color-fresh)]">
              <CheckCircle className="h-4 w-4" />
              Connected · v{health.version} · DB {health.db}
            </span>
          )}
          {error && (
            <span className="flex items-center gap-1.5 text-sm text-[var(--color-outdated)]">
              <XCircle className="h-4 w-4" />
              {error}
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
