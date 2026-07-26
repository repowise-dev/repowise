# Security Scan Example

Walk through Repowise security signals: the working-tree scan that runs during
index, and the OSS full-history secret scan via `repowise security scan
--history`. No LLM key required.

This walkthrough uses **synthetic** examples only — never paste real credentials
into docs, commits, or chat.

## Prerequisites

1. A git repository you can index locally.
2. `repowise` on `PATH` (`uv tool install repowise` or from this repo:
   `uv sync --all-packages`).
3. Index once (working-tree security patterns run as part of index):

```bash
cd /path/to/your-repo
repowise init --index-only --yes
```

## 1. Working-tree scan (automatic)

During `repowise init` / `repowise update`, Repowise already pattern-scans the
current tree for signals such as `eval` / `exec`, `pickle.loads`, `shell=True`,
hardcoded password/secret shapes, concat / f-string SQL, `verify=False`, and
weak hashes. Findings land in the local `security_findings` table and show up
in the local server Security UI / API.

There is no separate "rescan working tree" CLI subcommand today —
`repowise security scan` without `--history` only prints a short hint and
exits.

```bash
repowise security scan
# → hint to use --history for full-history secret scanning
```

## 2. Full-history secret scan (OSS)

History mode walks unique git blobs (not just `HEAD`) so secrets that were
committed and later removed still surface, attributed to the introducing
commit.

```bash
# Default: secret patterns only (hardcoded_password / hardcoded_secret)
repowise security scan --history

# Limit the revision window
repowise security scan --history --since v1.0.0 --to HEAD

# Also report non-secret code-smell patterns in history
repowise security scan --history --all-patterns

# Machine-readable
repowise security scan --history --output json
```

Re-runs are idempotent (unique constraint on findings). Point at another repo
with `--path /path/to/repo` when cwd is not the target.

### What "a finding" looks like (synthetic)

JSON output is a list of finding objects. Field shapes vary by version; treat
this as illustrative only:

```json
{
  "kind": "hardcoded_secret",
  "severity": "high",
  "file_path": "config/example.env.sample",
  "line": 12,
  "commit_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "message": "possible hardcoded secret (redacted)"
}
```

Repowise stores fingerprints / redacted previews for review — treat any hit as
a rotation candidate, not as a safe-to-copy value.

## 3. After the scan

1. Rotate anything that looks like a real credential (even if later deleted
   from `HEAD`).
2. Prefer env vars / a secret manager over in-repo literals.
3. Re-run `repowise security scan --history` after cleanup; the table should
   stay consistent on re-scan.

Hosted / commercial extras (graph-aware enrichment, live-at-`HEAD`
fingerprinting, incremental detection, SBOM/VEX, compliance reports) are
separate from this local OSS history walk — see
[COMMERCIAL.md](../../docs/business/COMMERCIAL.md) and
[SECURITY_COMPLIANCE.md](../../docs/business/SECURITY_COMPLIANCE.md).

## Smoke checklist

| Step | Expected |
|------|----------|
| `repowise init --index-only --yes` | Completes; working-tree findings may appear in Security UI |
| `repowise security scan` | Short hint mentioning `--history` |
| `repowise security scan --history` | Table (or empty) of history findings |
| `repowise security scan --history --output json` | JSON list; re-run stays stable |

## Related docs

- [CLI: `repowise security`](../../docs/reference/CLI_REFERENCE.md)
- [Commercial capabilities](../../docs/business/COMMERCIAL.md)
- [Security & compliance](../../docs/business/SECURITY_COMPLIANCE.md)
