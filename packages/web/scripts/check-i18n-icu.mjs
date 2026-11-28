#!/usr/bin/env node
/**
 * ICU static check for the Web UI message catalogs.
 *
 * next-intl parses messages with ICU MessageFormat at render time, so a
 * malformed plural or a stray brace only shows up as a runtime throw on the
 * page that renders it. This script compiles every leaf message in
 * messages/*.json up front, and prints the exact key when one fails.
 *
 * It also rejects a message that compiles but prints a brace as text. An ASCII
 * apostrophe directly before `{` starts an ICU quoted literal, so `'{name}'`
 * renders as the text `{name}`: the apostrophes are consumed and the argument is
 * never substituted. Nothing throws, so only the page shows it.
 *
 * Usage:  node scripts/check-i18n-icu.mjs
 * Exit:   0 when every message compiles, 1 with the offending keys otherwise.
 */

import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
let IntlMessageFormat;
try {
  ({ default: IntlMessageFormat } = require("intl-messageformat"));
} catch {
  ({ IntlMessageFormat } = require("@formatjs/intl-messageformat"));
}

const here = path.dirname(fileURLToPath(import.meta.url));
const messagesDir = path.resolve(here, "../messages");

/** Locales ICU has to know about for `{n, plural}` to compile. */
const LOCALE_FOR = { en: "en", "zh-CN": "zh-CN" };

function flatten(value, prefix = "", out = []) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    out.push([prefix, value]);
    return out;
  }
  for (const [key, child] of Object.entries(value)) {
    flatten(child, prefix ? `${prefix}.${key}` : key, out);
  }
  return out;
}

/** Every literal text run in a compiled message, plural and select arms and tags included. */
function literalText(elements, out = []) {
  for (const element of elements) {
    if (typeof element.value === "string" && element.type === 0) out.push(element.value);
    if (element.options) {
      for (const option of Object.values(element.options)) literalText(option.value, out);
    }
    if (element.children) literalText(element.children, out);
  }
  return out;
}

let failed = false;

for (const file of readdirSync(messagesDir).filter((f) => f.endsWith(".json")).sort()) {
  const locale = LOCALE_FOR[file.replace(/\.json$/, "")] ?? "en";
  const parsed = JSON.parse(readFileSync(path.join(messagesDir, file), "utf8"));
  let checked = 0;
  const errors = [];

  for (const [key, value] of flatten(parsed)) {
    if (typeof value !== "string") continue;
    checked += 1;
    let format;
    try {
      format = new IntlMessageFormat(value, locale);
    } catch (error) {
      errors.push(`${key}: ${error.message}`);
      continue;
    }
    const quoted = literalText(format.getAst()).find((text) => /[{}]/.test(text));
    if (quoted !== undefined) {
      errors.push(
        `${key}: an apostrophe quotes a brace, so ${JSON.stringify(quoted)} is printed as text. ` +
          `Use "{name}" or “{name}” around an argument, or '' for a literal apostrophe.`,
      );
    }
  }

  if (errors.length === 0) {
    console.log(`✔ ${file}: ${checked} messages compile`);
    continue;
  }
  failed = true;
  console.error(`✖ ${file}: ${errors.length} message(s) fail the ICU check`);
  for (const error of errors) console.error(`    ${error}`);
}

if (failed) {
  console.error("\nMalformed ICU in the message catalogs.");
  process.exit(1);
}
