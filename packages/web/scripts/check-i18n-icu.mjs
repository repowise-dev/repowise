#!/usr/bin/env node
/**
 * ICU static check for the Web UI message catalogs.
 *
 * next-intl parses messages with ICU MessageFormat at render time, so a
 * malformed plural or a stray brace only shows up as a runtime throw on the
 * page that renders it. This script compiles every leaf message in
 * messages/*.json up front, and prints the exact key when one fails.
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

let failed = false;

for (const file of readdirSync(messagesDir).filter((f) => f.endsWith(".json")).sort()) {
  const locale = LOCALE_FOR[file.replace(/\.json$/, "")] ?? "en";
  const parsed = JSON.parse(readFileSync(path.join(messagesDir, file), "utf8"));
  let checked = 0;
  const errors = [];

  for (const [key, value] of flatten(parsed)) {
    if (typeof value !== "string") continue;
    checked += 1;
    try {
      new IntlMessageFormat(value, locale);
    } catch (error) {
      errors.push(`${key}: ${error.message}`);
    }
  }

  if (errors.length === 0) {
    console.log(`✔ ${file}: ${checked} messages compile`);
    continue;
  }
  failed = true;
  console.error(`✖ ${file}: ${errors.length} message(s) fail to compile`);
  for (const error of errors) console.error(`    ${error}`);
}

if (failed) {
  console.error("\nMalformed ICU in the message catalogs.");
  process.exit(1);
}
