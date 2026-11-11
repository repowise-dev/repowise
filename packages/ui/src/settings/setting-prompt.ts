import { REPO_ROLE, type AiPromptFlavor } from "../health/ai-prompts/shared";
import type { RegistrySetting, RegistrySuggestion } from "./settings-registry-types";

const SOURCE_WORDS: Record<string, string> = {
  default: "the default",
  env: "from the environment",
  repo_file: "from .repowise/config.yaml",
  user_file: "from the user config in ~/.repowise",
};

/** A setting value as `repowise config` prints it. */
export function formatSettingValue(value: unknown): string {
  if (value === null || value === undefined) return "unset";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (Array.isArray(value)) return value.length ? value.map(String).join(", ") : "(none)";
  if (typeof value === "object") {
    return Object.keys(value).length ? JSON.stringify(value) : "(none)";
  }
  return String(value);
}

function ask(setting: RegistrySetting, suggestion: RegistrySuggestion | undefined): string {
  const explain = `Run \`repowise config explain ${setting.key}\` and tell me what changing it would do here.`;
  if (setting.scope === "env") {
    const name = setting.env?.name ?? setting.key;
    return `${explain} It is read from the \`${name}\` environment variable, not a file: tell me where it should be set for this machine, and set it once I agree.`;
  }
  const apply = suggestion
    ? `Once I agree, run \`${suggestion.command}\`.`
    : `Ask me which value I want, then run \`repowise config set ${setting.key} <value>\` once I agree.`;
  return `${explain} ${apply} It prints the undo command; tell me what it is. Then run \`repowise config get ${setting.key}\` to check.`;
}

/**
 * A prompt that hands one setting to an agent: what it does, where it stands,
 * the suggested value if there is one, and the commands to change and check
 * it. The agent asks before changing anything.
 */
export function buildSettingAiPrompt(
  setting: RegistrySetting,
  repoName: string,
  flavor: AiPromptFlavor = "generic",
  suggestion?: RegistrySuggestion,
): string {
  const source = SOURCE_WORDS[setting.source] ?? `from ${setting.source}`;
  const now = `It is ${formatSettingValue(setting.value)} now (${source}); the default is ${formatSettingValue(setting.default)}.`;
  const accepts = setting.accepts ? ` It accepts ${setting.accepts}.` : "";
  const lines = [
    REPO_ROLE[flavor],
    `The repowise setting \`${setting.key}\` in ${repoName}: ${setting.description.trim()}\n${now}${accepts}`,
  ];
  if (suggestion) {
    const impact = suggestion.impact ? ` ${suggestion.impact}` : "";
    lines.push(
      `Repowise suggests ${formatSettingValue(suggestion.suggested_value)} for this repository: ${suggestion.reason}${impact}`,
    );
  }
  lines.push(ask(setting, suggestion));
  return lines.join("\n\n");
}
