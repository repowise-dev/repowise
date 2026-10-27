"use client";

import { useState, useEffect, useRef, type KeyboardEvent } from "react";
import { Spinner } from "@repowise-dev/ui/ui/spinner";
import { config } from "@/lib/config";
import { getHealth } from "@/lib/api/health";
import { Input } from "@repowise-dev/ui/ui/input";
import { Button } from "@repowise-dev/ui/ui/button";
import { SettingsRow, SettingsRows, StatusLine, useSaveState } from "@repowise-dev/ui/settings";
import type { HealthResponse } from "@/lib/api/types";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { useTranslations } from "next-intl";

type Field = "url" | "key";

/**
 * Server connection, and the page's only connection test.
 *
 * `ProviderSection` used to ship a second "Server Connection" card with its own
 * Test button hitting the same `/health`, reporting the result in a
 * different vocabulary through a hand-rolled `<button>` whose border token did
 * not exist. This one reports more (version and DB), so that one went.
 *
 * Both fields save to this browser on blur or Enter, with a tick on the row.
 */
export function ConnectionSection() {
  const t = useTranslations("settings");
  const [apiUrl, setApiUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [testing, setTesting] = useState(false);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [field, setField] = useState<Field | null>(null);
  const save = useSaveState();

  const initialUrlRef = useRef("");
  const initialKeyRef = useRef("");

  useEffect(() => {
    setApiUrl(config.getApiUrl());
    setApiKey(config.getApiKey());
    initialUrlRef.current = config.getApiUrl();
    initialKeyRef.current = config.getApiKey();
  }, []);

  function saveField(which: Field) {
    const [value, ref, write] =
      which === "url"
        ? [apiUrl, initialUrlRef, config.setApiUrl]
        : [apiKey, initialKeyRef, config.setApiKey];
    if (value === ref.current) return;
    ref.current = value;
    setField(which);
    void save.track(async () => write(value));
  }

  async function testConnection() {
    saveField("url");
    saveField("key");
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

  const tick = (which: Field) => (field === which ? { saveState: save.state } : {});
  const onEnter = (which: Field) => (e: KeyboardEvent) => {
    if (e.key === "Enter") saveField(which);
  };

  return (
    <SettingsRows>
      <SettingsRow label={t("connection.urlLabel")} htmlFor="api-url" hint={t("connection.urlHint")} {...tick("url")}>
        <Input
          id="api-url"
          placeholder="http://localhost:7337"
          value={apiUrl}
          onChange={(e) => setApiUrl(e.target.value)}
          onBlur={() => saveField("url")}
          onKeyDown={onEnter("url")}
          className="font-mono"
        />
      </SettingsRow>

      <SettingsRow
        label={t("connection.apiKeyLabel")}
        htmlFor="api-key"
        hint={t("connection.apiKeyHint")}
        {...tick("key")}
      >
        <Input
          id="api-key"
          type="password"
          placeholder={t("connection.apiKeyPlaceholder")}
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          onBlur={() => saveField("key")}
          onKeyDown={onEnter("key")}
          className="font-mono"
        />
      </SettingsRow>

      <SettingsRow label={t("connection.testLabel")} hint={t("connection.testHint")}>
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="outline" size="sm" onClick={testConnection} disabled={testing}>
            {testing ? (
              <>
                <Spinner size="sm" />
                {t("connection.testing")}
              </>
            ) : (
              t("connection.test")
            )}
          </Button>

          {health && (
            <StatusLine status="ok">
              <span className="tabular-nums">
                {t("connection.status", { version: health.version, db: health.db })}
              </span>
            </StatusLine>
          )}
          {error && <StatusLine status="error">{error}</StatusLine>}
        </div>
      </SettingsRow>
    </SettingsRows>
  );
}
