#!/usr/bin/env node
/**
 * Key-parity check for the Web UI message catalogs.
 *
 * Every locale file must carry exactly the keys `messages/en.json` carries —
 * no missing translations, no orphans. Compares the flattened key paths, not
 * whole files, so a nested namespace can be reordered freely.
 *
 * Usage:  node scripts/check-i18n-keys.mjs
 * Exit:   0 when all catalogs agree, 1 with a per-locale diff otherwise.
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

let failed = false;

if (locales.length === 0) {
  console.error(`✖ no locale catalogs found beside ${baseFile}`);
  process.exit(1);
}

for (const file of locales) {
  const keys = load(file);
  const missing = [...base].filter((k) => !keys.has(k)).sort();
  const orphaned = [...keys].filter((k) => !base.has(k)).sort();

  if (missing.length === 0 && orphaned.length === 0) {
    console.log(`✔ ${file}: ${keys.size} keys, identical to ${baseFile}`);
    continue;
  }

  failed = true;
  console.error(
    `✖ ${file}: ${missing.length} missing, ${orphaned.length} orphaned`,
  );
  for (const key of missing) console.error(`    missing   ${key}`);
  for (const key of orphaned) console.error(`    orphaned  ${key}`);
}

if (failed) {
  console.error(`\ni18n catalogs differ from ${baseFile}.`);
  process.exit(1);
}

console.log(`\n${baseFile} and ${locales.length} locale(s) agree: ${base.size} keys.`);
