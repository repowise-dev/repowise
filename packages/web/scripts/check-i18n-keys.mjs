#!/usr/bin/env node
/**
 * Catalog freshness report for the Web UI message catalogs.
 *
 * English (`messages/en.json`) is the source of truth. A locale catalog may
 * lag behind it: a key the translator has not reached yet still renders the
 * English string at runtime (see `src/i18n/request.ts`), so a gap is a
 * warning, not a failure. This script prints those gaps so they are visible
 * instead of silent, and still exits 0 — UI copy can change without every
 * locale being updated in the same commit.
 *
 * Orphaned keys (a locale carries a key `en.json` does not) are always
 * reported: they are dead weight or a typo, and would never render.
 *
 * Usage:  node scripts/check-i18n-keys.mjs
 * Exit:   0 always (warnings on stderr), 1 only when a catalog is not valid
 *         JSON or `en.json` itself is missing.
 */

import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const messagesDir = path.resolve(here, "../messages");
const baseFile = "en.json";

/** Flatten nested message objects into dotted key paths (leaves only). */
function flatten(value, prefix = "") {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return [prefix];
  }
  return Object.entries(value).flatMap(([key, child]) =>
    flatten(child, prefix ? `${prefix}.${key}` : key),
  );
}

function load(file) {
  const raw = readFileSync(path.join(messagesDir, file), "utf8");
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (error) {
    console.error(`✖ ${file} is not valid JSON: ${error.message}`);
    process.exit(1);
  }
  return new Set(flatten(parsed));
}

const base = load(baseFile);
const locales = readdirSync(messagesDir)
  .filter((f) => f.endsWith(".json") && f !== baseFile)
  .sort();

if (locales.length === 0) {
  console.log(`✔ ${baseFile}: ${base.size} keys (no locale catalogs to compare).`);
  process.exit(0);
}

let warned = false;

for (const file of locales) {
  const keys = load(file);
  const missing = [...base].filter((k) => !keys.has(k)).sort();
  const orphaned = [...keys].filter((k) => !base.has(k)).sort();

  if (missing.length === 0 && orphaned.length === 0) {
    console.log(`✔ ${file}: ${keys.size} keys, identical to ${baseFile}`);
    continue;
  }

  warned = true;
  console.warn(`⚠ ${file}: ${missing.length} missing, ${orphaned.length} orphaned`);
  for (const key of missing) {
    console.warn(`    missing   ${key} (falls back to ${baseFile})`);
  }
  for (const key of orphaned) {
    console.warn(`    orphaned  ${key} (never renders — remove or add to ${baseFile})`);
  }
}

if (warned) {
  console.warn(
    `\ni18n catalogs differ from ${baseFile} — warnings only; missing keys render English.`,
  );
} else {
  console.log(`\n${baseFile} and ${locales.length} locale(s) agree: ${base.size} keys.`);
}
