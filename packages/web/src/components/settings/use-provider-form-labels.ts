"use client";

import { useTranslations } from "next-intl";
import type { ProviderSettingsLabels } from "@repowise-dev/ui/settings/provider-settings";

/** The provider form's copy. `scoped` is a repo's form (its config.yaml);
 *  otherwise the server-wide default. */
export function useProviderFormLabels(scoped: boolean): ProviderSettingsLabels {
  const f = useTranslations("settings.providerForm");
  return {
    model: f("model"),
    baseUrl: f("baseUrl"),
    baseUrlHint: f("baseUrlHint"),
    baseUrlInvalid: f("baseUrlInvalid"),
    localServer: f("localServer"),
    testConnection: f("testConnection"),
    connected: (count) => f("connected", { count }),
    save: f("save"),
    useProvider: (name) => f("useProvider", { name }),
    cancel: f("cancel"),
    regenerateNotice: scoped ? f("regenerateNotice") : f("regenerateNoticeGlobal"),
    unconfirmedUrl: (url) => f("unconfirmedUrl", { url }),
    trustUrl: f("trustUrl"),
    active: f("active"),
    noKeyRequired: f("noKeyRequired"),
    keySet: f("keySet"),
    noKey: f("noKey"),
    use: f("use"),
    useAria: (name) => f("useAria", { name }),
    test: f("test"),
    testAria: (name) => f("testAria", { name }),
    addKey: f("addKey"),
    updateKey: f("updateKey"),
    removeKeyAria: (name) => f("removeKeyAria", { name }),
    keyAria: (name) => f("keyAria", { name }),
    keyPlaceholder: (name) => f("keyPlaceholder", { name }),
    saveKeyAria: (name) => f("saveKeyAria", { name }),
    connectionVerified: f("connectionVerified"),
    connectionFailed: f("connectionFailed"),
    saveFailed: f("saveFailed"),
    provenance: (source) => {
      if (source === "config.yaml") return f("sourceConfig");
      if (source === "global") return f("sourceGlobal");
      if (source === "default") return f("sourceDefault");
      if (source === "autodetect") return f("sourceAutodetect");
      if (source.startsWith("env:")) return f("sourceEnv", { name: source.slice(4) });
      return null;
    },
  };
}
