"use client";

import { useState, useEffect, useRef } from "react";
import { Spinner } from "@repowise-dev/ui/ui/spinner";
import { config } from "@/lib/config";
import { getHealth } from "@/lib/api/health";
import { OverviewSection } from "@repowise-dev/ui/overview";
import { Input } from "@repowise-dev/ui/ui/input";
import { Button } from "@repowise-dev/ui/ui/button";
import {
  SettingsRow,
  SettingsRows,
  SaveIndicator,
  StatusLine,
  type SaveState,
} from "@repowise-dev/ui/settings";
import type { HealthResponse } from "@/lib/api/types";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";

/**
 * Server connection, and the page's only connection test.
 *
 * `ProviderSection` used to ship a second "Server Connection" card with its own
 * Test button hitting the same `/api/health`, reporting the result in a
 * different vocabulary through a hand-rolled `<button>` whose border token did
 * not exist. This one reports more (version and DB), so that one went.
 */
export function ConnectionSection() {
  const [apiUrl, setApiUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [testing, setTesting] = useState(false);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("idle");

  const initialUrlRef = useRef("");
  const initialKeyRef = useRef("");
  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setApiUrl(config.getApiUrl());
    setApiKey(config.getApiKey());
    initialUrlRef.current = config.getApiUrl();
    initialKeyRef.current = config.getApiKey();
  }, []);

  useEffect(
    () => () => {
      if (savedTimer.current) clearTimeout(savedTimer.current);
    },
    [],
  );

  function save() {
    const changed =
      apiUrl !== initialUrlRef.current || apiKey !== initialKeyRef.current;
    config.setApiUrl(apiUrl);
    config.setApiKey(apiKey);
    if (!changed) return;
    initialUrlRef.current = apiUrl;
    initialKeyRef.current = apiKey;
    // Autosave, with the one save affordance the whole surface shares — no
    // toast, and nothing at rest.
    setSaveState("saved");
    if (savedTimer.current) clearTimeout(savedTimer.current);
    savedTimer.current = setTimeout(() => setSaveState("idle"), 2000);
  }

  async function testConnection() {
    save();
    setTesting(true);
    setHealth(null);
    setError(null);
    try {
      setHealth(await getHealth());
    } catch (e) {
      setError(toFriendlyMessage(e, "Connection failed"));
    } finally {
      setTesting(false);
    }
  }

  return (
    <OverviewSection
      title="Server"
      description="How this dashboard reaches your repowise server. Stored in this browser, not on the server."
      flush
      action={<SaveIndicator state={saveState} />}
    >
      <SettingsRows>
        <SettingsRow
          label="Server URL"
          htmlFor="api-url"
          hint="Leave blank to use the same origin, which is what a Next.js proxy setup wants."
        >
          <Input
            id="api-url"
            placeholder="http://localhost:7337"
            value={apiUrl}
            onChange={(e) => setApiUrl(e.target.value)}
            onBlur={save}
            className="font-mono"
          />
        </SettingsRow>

        <SettingsRow
          label="API key"
          htmlFor="api-key"
          hint="Only needed when REPOWISE_API_KEY is set on the server."
        >
          <Input
            id="api-key"
            type="password"
            placeholder="Optional"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            onBlur={save}
            className="font-mono"
          />
        </SettingsRow>

        <SettingsRow
          label="Connection"
          hint="Checks that the server answers and reports what it is running."
        >
          <div className="flex flex-wrap items-center gap-3">
            <Button
              variant="outline"
              size="sm"
              onClick={testConnection}
              disabled={testing}
            >
              {testing ? (
                <>
                  <Spinner size="sm" />
                  Testing
                </>
              ) : (
                "Test connection"
              )}
            </Button>

            {health && (
              <StatusLine status="ok">
                <span className="tabular-nums">
                  Connected · v{health.version} · DB {health.db}
                </span>
              </StatusLine>
            )}
            {error && <StatusLine status="error">{error}</StatusLine>}
          </div>
        </SettingsRow>
      </SettingsRows>
    </OverviewSection>
  );
}
