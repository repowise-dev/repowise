#!/usr/bin/env node
/**
 * Usage check for the Web UI message catalogs — the gate that would have caught
 * the `MISSING_MESSAGE` storm this branch shipped before it was fixed.
 *
 * Background (2026-09-24, task t_cabc4827): 487 of the 881 keys had been added
 * as *flat* names inside a namespace —
 *
 *     "shell": { "feedback.title": "Help us improve Repowise" }
 *
 * next-intl resolves `useTranslations("shell") + t("feedback.title")` as the
 * nested path shell.feedback.title, so those keys never resolved: the server
 * logged `MISSING_MESSAGE: shell.feedback.title (en)` and the raw key path was
 * rendered as visible text. Key parity (i18n:check) and the untranslated-string
 * scanner (i18n:todo) both pass on such a catalog — parity compares key sets,
 * the scanner reads source code — so neither could see it. This script closes
 * that hole:
 *
 *   1. structure — no key *name* may contain a dot; keys must nest.
 *   2. resolution — every literal key passed to a `t("...")` / `t.rich("...")`
 *      call in a file must resolve under one of the namespaces that file
 *      declares via useTranslations()/getTranslations().
 *
 * Usage:  node scripts/check-i18n-usage.mjs
 * Exit:   0 when the catalogs are well formed and every checked call resolves.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(here, "..");
const srcRoot = path.join(webRoot, "src");

/** Flatten nested message objects into dotted key paths (leaves only). */
function flatten(value, prefix = "") {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return [prefix];
  }
  return Object.entries(value).flatMap(([key, child]) =>
    flatten(child, prefix ? `${prefix}.${key}` : key),
  );
}

/** Key *names* that contain a dot — the shape that breaks next-intl lookup. */
function dottedNames(value, prefix = "") {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return [];
  }
  return Object.entries(value).flatMap(([key, child]) => {
    const here = prefix ? `${prefix}.${key}` : key;
    const own = key.includes(".") ? [here] : [];
    return own.concat(dottedNames(child, here));
  });
}

function walk(dir) {
  return readdirSync(dir).flatMap((entry) => {
    const full = path.join(dir, entry);
    return statSync(full).isDirectory()
      ? walk(full)
      : /\.(ts|tsx)$/.test(entry)
        ? [full]
        : [];
  });
}

const catalog = JSON.parse(
  readFileSync(path.join(webRoot, "messages", "en.json"), "utf8"),
);
const keys = new Set(flatten(catalog));

let failed = false;

const dotted = dottedNames(catalog);
if (dotted.length > 0) {
  failed = true;
  console.error(
    `✖ messages/en.json: ${dotted.length} key name(s) contain a dot ` +
      `(next-intl resolves dots as path separators, so these never resolve):`,
  );
  for (const k of dotted.slice(0, 20)) console.error(`    ${k}`);
  if (dotted.length > 20) console.error(`    … and ${dotted.length - 20} more`);
}

const DECL =
  /(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:await\s+)?(?:useTranslations|getTranslations)\(\s*"([^"]+)"\s*\)/g;

let filesChecked = 0;
let callsChecked = 0;
const problems = [];

for (const file of walk(srcRoot)) {
  if (/\.(test|spec)\.tsx?$/.test(file)) continue;
  const source = readFileSync(file, "utf8");
  const spaces = [...source.matchAll(DECL)].map((m) => m[2]);
  if (spaces.length === 0) continue; // keys arrive via props/children — skipped
  filesChecked += 1;

  const rel = path.relative(webRoot, file);
  const CALL = /([A-Za-z_$][\w$]*)(?:\.(?:rich|markup|raw))?\(\s*"([^"]+)"/g;
  const declared = new Set([...source.matchAll(DECL)].map((m) => m[1]));
  for (const m of source.matchAll(CALL)) {
    const [, variable, key] = m;
    if (!declared.has(variable)) continue; // not a translator receiver
    callsChecked += 1;
    const ok = spaces.some((ns) => keys.has(`${ns}.${key}`));
    if (!ok) {
      problems.push(
        `${rel}: ${variable}("${key}") resolves under none of ` +
          `[${spaces.join(", ")}]`,
      );
    }
  }
}

if (problems.length > 0) {
  failed = true;
  console.error(
    `✖ ${problems.length} message key(s) used in code do not resolve in en.json:`,
  );
  for (const p of problems.slice(0, 40)) console.error(`    ${p}`);
  if (problems.length > 40) console.error(`    … and ${problems.length - 40} more`);
}

if (failed) {
  console.error("\ni18n usage check failed.");
  process.exit(1);
}

console.log(
  `✔ messages/en.json: ${keys.size} keys, no dotted key names\n` +
    `✔ ${callsChecked} literal t()/t.rich() calls in ${filesChecked} file(s) all resolve`,
);
