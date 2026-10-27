# Security signals

Repowise records security signals while it indexes: matches from a registry of
25 patterns over source text, plus a scan for security-relevant symbol names.
It also scans git history for leaked credentials, and gates pull requests on
what a change adds.

It works as a pattern registry: it marks places to read, without taint analysis,
framework models or dependency lookup. Run a SAST tool and a dedicated secret
scanner alongside it.

It needs no LLM key and no network. Working-tree signals cost a regex pass during
indexing; the CI check needs only git.

## Quick start

```bash
repowise init                                  # working-tree signals populate during indexing
repowise security scan --history               # walk every revision for leaked secrets
repowise security scan --history --since v1.0.0 --to HEAD
repowise security check origin/main...HEAD     # CI gate on what a change adds
```

`scan --history` stores what it finds and prints counts:

```
  Commits scanned: 412
  Blobs scanned:   2318
  Files scanned:   1604
  Findings stored: 8
  By severity:     high=6, low=2
  By kind:         aws_access_key=1, hardcoded_password=2, hardcoded_secret=5
```

From an agent, `get_risk(targets=["src/api/"])` includes a `security_signals`
block, without the informational `security_sensitive_symbol` rows. In the
dashboard, findings are on the Security tab of the Code Health page, and over REST
at `GET /api/repos/{repo_id}/security`.

## Reading the results

### What the registry catches

25 pattern kinds plus the symbol-name scan: 26 kinds in all, in three
severities. Severity is fixed per kind, with one exception: a finding in test
material, or under a directory named `test`, `tests`, `__tests__`, `__test__`,
`fixtures`, `__fixtures__`, `spec`, `specs`, `mock`, `mocks`, `__mocks__`,
`example` or `examples`, is recorded at `low`. A non-secret finding in a
script, tool, benchmark, migration or docs example is `low` too; a secret there
keeps its severity. Nothing is scored or ranked. `security_sensitive_symbol` is
informational: it marks where security code lives, not a problem.

| Kind | Severity | Matches |
|------|----------|---------|
| `eval_call` | high | `eval(...)`, including a receiver chain (`vm.eval(`) |
| `exec_call` | high | `exec(...)`. Outside Python, only a call through a name bound to `child_process` (`exec`, `execSync`). `execFile` runs no shell and does not fire |
| `pickle_loads` | high | `pickle.loads`, except a round trip of an own object (`pickle.loads(pickle.dumps(x))`) |
| `subprocess_shell_true` | high | `subprocess.*` with `shell=True`, also across lines |
| `os_system` | high | `os.system` |
| `hardcoded_password` | high | a quoted literal assigned to a name containing `password`, any case |
| `hardcoded_secret` | high | the same for `api_key`, `apikey`, `secret`, `token`, `access_key` |
| `aws_access_key` | high | `AKIA`/`ASIA` + 16 chars, any variable name |
| `github_token` | high | `ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_`/`github_pat_` tokens |
| `slack_token` | high | `xoxb-`, `xoxa-`, `xoxp-`, `xoxr-`, `xoxs-` tokens |
| `google_api_key` | high | `AIza` + 35 chars, not from inside a base64 run |
| `stripe_key` | high | `sk_` or `rk_` then `live_`/`test_`/`prod_`. Publishable `pk_` keys do not fire |
| `gitlab_token` | high | `glpat-` (classic and routable), `gldt-`, `glrt-`, `glptt-`, `gloas-`, `glimt-`, `glcbt-` and `GR1348941` runner registration tokens |
| `azure_devops_pat` | high | the 84-char Azure DevOps PAT with `AZDO` at offset 76. The legacy 52-char PAT is only caught by `hardcoded_secret` |
| `git_url_credentials` | high | a password in a git remote URL (`https://user:password@host/repo`): a GitHub, GitLab, Bitbucket or Azure DevOps host, or a path ending in `.git`. Database URLs, local and `example.*` hosts, `$VAR`/`${VAR}`/`%VAR%`/`<slot>` and word-like passwords do not fire |
| `private_key_pem` | high | a `-----BEGIN ... PRIVATE KEY-----` header followed by a base64 body line (an escaped `\n` counts) |
| `public_env_secret` | high | `API_KEY`/`SECRET`/`TOKEN`/`PASSWORD` behind `NEXT_PUBLIC_` or `VITE_`, excluding `..._ANON_...` |
| `new_function_call` | high | `new Function(...)` |
| `fstring_sql` | med | a Python f-string query (`SELECT` with `FROM`) that interpolates a value after a comparison or `LIKE` (`WHERE id = {uid}`). A table, column, fragment or placeholder list cannot be bound, so interpolating one does not fire |
| `concat_sql` | med | `.execute("SELECT ... +` |
| `template_literal_sql` | med | a JS/TS template literal with `SELECT`+`FROM` or `UPDATE`+`SET` and an interpolation |
| `tls_verify_false` | med | `verify=False` passed to an HTTP client call or set as `verify`, in a file that imports an HTTP client library (`requests`, `httpx`, `aiohttp`, `urllib3`, boto3). A call through a plain name must go through that import or a value built from it |
| `reject_unauthorized_false` | med | `rejectUnauthorized: false` |
| `unsafe_inner_html` | med | `__html:` (React's `dangerouslySetInnerHTML`) assigned a non-literal |
| `weak_hash` | low | MD5 or SHA-1 in use in code: a call (`hashlib.md5(`), a type (`MD5.Create()`) or the algorithm named to a hashing call (`createHash("sha1")`). A call passing `usedforsecurity=False` does not fire |
| `security_sensitive_symbol` | low | a symbol named `auth`, `password`, `jwt` or `crypto`. A bare `session` or `token` is usually a database session or a lexer token and does not fire |

The eleven secret kinds are `hardcoded_password`, `hardcoded_secret`,
`git_url_credentials` and the eight vendor shapes (AWS, GitHub, GitLab, Azure
DevOps, Slack, Google, Stripe, PEM). History mode and the in-change commit scan
report only these by default.

### What fires and what does not

Python `eval` and `exec` are found from the AST. Most other kinds match over
source with comments and strings masked out, so text describing a call does not
fire. Vendor key shapes match anywhere, comments included: a real key in a comment
is still a leak. Prose, generated and vendored files are scanned for the secret
kinds only, and placeholder values (`<your key>`, `changeme`, `${}`) are skipped.
The full rules per kind are in
[architecture/security.md](../architecture/security.md#matching-rules).

### Working tree versus history

The working-tree scan runs during `repowise init` and `repowise update`. Rows
have an empty `commit_sha`. `repowise security scan` without `--history` only
prints a hint, since indexing already did this.

The history scan runs on `repowise security scan --history` and walks every
tracked revision. Rows carry the SHA and author date of the commit that introduced the
match. This finds a credential committed in March and removed in April: absent
from HEAD, present in every clone. By default it reports only the secret kinds.
A committed secret stays valid until rotated; an `eval` that was later removed
is just history. `--all-patterns` adds the code kinds, with a lot more volume.

Both write the `security_findings` table, unique on file, kind, line and
commit, so re-running is idempotent.

### Line verification

A stored line number goes stale as the file moves on, so each served finding is
re-checked against the live file using its stored snippet:

| `line_verified` | `line_number` | Meaning |
|---|---|---|
| `true` | a line | The snippet is on that line (possibly after correction) |
| `false` | a line | The snippet occurs more than once; the line is a guess |
| `false` | `null` | The snippet is gone; the finding is stale |

`security_sensitive_symbol` is checked in place and never relocated, since a
name recurs throughout a file. When the file cannot be read, the stored line is
returned unverified.

Snippets are trimmed to 120 characters with every credential value masked to
its first four characters and `****`. The raw value is never stored.

### A suggested reading order

1. Secret kinds from history mode. Rotate first, then clean history.
2. High working-tree kinds, as places to read. The layer found a call; you
   decide whether it is reachable.
3. `security_sensitive_symbol`, as a map of where auth and crypto code lives.
4. Low findings last. `weak_hash` cannot tell a checksum from a password hash.

A low finding count means the registry did not match. On most repos, in most
languages, that is all it means.

## In CI: `repowise security check`

The PR surface. It needs git only: no index, no database, no API key, and it
stores nothing.

```bash
repowise security check origin/main...HEAD
repowise security check --fail-on med --format github
repowise security check --format sarif > security.sarif
```

It judges what the change adds:

- At the head, every touched file is scanned, and a finding counts only
  when a line it spans was added or edited. In documentation files only the
  secret kinds count.
- Inside the change, every non-merge commit in the range is scanned for the
  secret kinds. A key added in one commit and deleted in the next is reported
  with the commit that added it.

`--fail-on` sets the lowest failing severity (`high` by default, then `med`,
`low`). Findings in test paths are `low`, so under the default they warn
without failing. Exit codes: `0` passed, `1` failed, `2` could not evaluate
(not a git repo, unknown revision, missing merge-base or commits in a shallow
clone, unreadable baseline). A git error is never read as a clean change.

Every format carries masked snippets only: `table`, `json`, `markdown`,
`github` (annotations plus job summary), `sarif` and `gitlab` (Code Quality).
In the Code Quality report a failing finding is `critical` when high and
`major` otherwise; one below `--fail-on` is `minor`.

Without a revspec, the base comes from the CI's pull-request variables, else
the remote's default branch. The GitHub Action, GitLab template and SARIF
upload are in [Repowise in CI](../start/CI.md).

## Tuning and suppressing

### Silencing one finding: `repowise-security-ignore`

Put `repowise-security-ignore` on the finding's own line in any comment
syntax; `repowise-security-ignore: aws_access_key` silences only the kinds it
lists. There is no next-line or whole-file form. A silenced finding never fails
the gate and is still reported there as suppressed. The scan that indexing
stores leaves it out, so it does not reach the dashboard, `get_risk` or REST.
Exact rules are in the [CLI reference](../reference/CLI_REFERENCE.md#repowise-security-check-revspec).

The scanner also honours other scanners' comments: `# pragma: allowlist secret`
on the finding's line, or `# pragma: allowlist nextline secret` on the line
above it (detect-secrets), and `gitleaks:allow`, `trufflehog:ignore`, `nosec`
(Bandit, gosec) and `noqa: S105`/`S106`/`S107` (Ruff) on the finding's line
(`//` and `/*` comments work too). Unlike the Repowise gate-only marker,
allowlisted secret findings are excluded from the stored scan and the gate; they
do not appear in the gate's suppressed count. Non-secret findings on the same line
still report. A marker inside a string does not silence a finding.

### A repository's `.gitleaks.toml`

A path the root `.gitleaks.toml` allowlists has no secret findings, in the
stored scan, the history scan or the gate. The scanner reads the `paths` of
`[allowlist]` and `[[allowlists]]` as gitleaks matches them, plus gitleaks'
default allowlist paths under `[extend] useDefault = true`. Entries scoped by
`targetRules`, or by `condition = "AND"` with `regexes`, `stopwords` or
`commits`, are not applied. The file is read from the checked-out tree, as
gitleaks does, so a change's own allowlist entries apply to it.

### Baselines

`--write-baseline FILE` records the change's findings in a committed JSON file
and exits 0; `--baseline FILE` accepts what it lists, so only new findings fail.
An entry is keyed on file, kind and masked line, so it survives line shifts; a
new vendor-shaped key on an identical line is still a new finding
([how entries are keyed](../architecture/security.md#baseline-keys)). Writing to
an existing baseline adds entries; remove one by deleting it. `sarif` marks
accepted findings suppressed; `gitlab` leaves them out.

### Custom patterns: `security.patterns`

Add your own secret shapes under `security.patterns` in `.repowise/config.yaml`
as a name, a regex and a severity. Each becomes the secret kind
`custom:<name>`, scanned and masked like the built-in ones. An invalid pattern
stops the check with exit 2. See
[the `security:` block](../reference/CONFIG.md#the-security-block).

### Before a commit: `--staged`

`repowise security check --staged` checks the staged lines, read from the
index. `repowise hook install --security` runs it as a pre-commit hook that
blocks a commit on a finding at or above `high`. Skip it once with
`git commit --no-verify`. See
[`repowise hook install`](../reference/CLI_REFERENCE.md#repowise-hook-install).

## Accuracy and limits

Each result is a regular-expression match with a little parsing. Read every
result as a place to look.

- No taint analysis or dataflow. Whether attacker input reaches a call is not
  computed: `eval_call` fires the same on a constant and on a request parameter.
- No framework semantics. Missing authorization, permissive CORS, unvalidated
  redirects and missing ownership checks are out of reach.
- The registry is shaped around Python and JS/TS. `pickle_loads`, `os_system`,
  `subprocess_shell_true`, `tls_verify_false` and `fstring_sql` are Python
  idioms. On Go, Rust or Java, `eval`/`exec` and the secret kinds are most of
  what can fire, and a quiet report reflects the registry's shape.
- Secret detection matches shapes, with no entropy scoring. Providers outside
  the eight vendor shapes are caught only through custom patterns. Use gitleaks
  or trufflehog for full secret scanning.
- No dependency or CVE scanning.
- `weak_hash` fires on an MD5 checksum unless the call passes
  `usedforsecurity=False`. `unsafe_inner_html` ships at `med` because it cannot
  tell a name pointing at a pinned constant from a dynamic value.
- The five JS/TS kinds are hand-checked on a 17-repository, 1,109-file corpus;
  per-kind results are in
  [architecture/security.md](../architecture/security.md#precision-checks).
- A CI pass means no pattern matched a changed line, nothing more.

## Where it shows up

| Surface | Where |
|---|---|
| CLI | `repowise security scan --history`, `repowise security check`, `repowise hook install --security` |
| MCP | The `security_signals` block on `get_risk` |
| Dashboard | The Security tab of the Code Health page |
| REST | `GET /api/repos/{repo_id}/security` |
| CI | The GitHub Action's `security` gate (annotations, SARIF) and the GitLab template (Code Quality) |

## Reference

### `repowise security scan`

| Flag | Meaning |
|---|---|
| `--history` | Walk every tracked revision. Without it the command only prints a hint |
| `--since REV` | Lower bound (exclusive) |
| `--to REV` | Upper bound (inclusive) |
| `--all-patterns` | History mode: report the code kinds as well as secrets |
| `--path DIR` | Repository path |
| `--format` | `table` or `json` |

### `repowise security check [REVSPEC]`

| Flag | Default | Meaning |
|---|---|---|
| `--fail-on` | `high` | Lowest severity that fails: `high`, `med`, `low` |
| `--baseline FILE` | none | Accept findings listed in this file |
| `--write-baseline FILE` | none | Add this change's findings to the file and exit 0 |
| `--fail-on-baseline-growth` | off | Exit 1 when `--baseline` lists a finding the file at the change's base does not |
| `--staged` | off | Check staged changes; takes no revspec |
| `--path DIR` | cwd | A path inside the repository |
| `--format` | `table` | `table`, `json`, `markdown`, `github`, `sarif`, `gitlab` |

Full entries: [`repowise security`](../reference/CLI_REFERENCE.md#repowise-security).
MCP schema: [`get_risk`](../agent/MCP_TOOLS.md#get_risk).

## See also

- [CHANGE_RISK.md](CHANGE_RISK.md): the other CI gate on a change.
- [CODE_HEALTH.md](CODE_HEALTH.md): the Code Health page that hosts the
  Security tab.
- [Repowise in CI](../start/CI.md).
