# Security signals

Repowise records security signals while it indexes: matches from a registry of
22 patterns over source text, plus a scan for security-relevant symbol names.
It also scans git history for leaked credentials, and gates pull requests on
what a change adds.

It is a pattern registry, not a security scanner. There is no taint analysis,
no framework model and no dependency lookup. Run a real SAST tool and a secret
scanner alongside it.

No LLM key, no network. Working-tree signals cost a regex pass during
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
block. In the dashboard, findings are on the Security tab of the Code Health
page, and over REST at `GET /api/repos/{repo_id}/security`.

## Reading the results

### What the registry catches

22 pattern kinds plus the symbol-name scan: 23 kinds in all, in three
severities. Severity is fixed per kind, with one exception: a finding in test
material, or under a directory named `test`, `tests`, `__tests__`, `__test__`,
`fixtures`, `__fixtures__`, `spec`, `specs`, `mock`, `mocks`, `__mocks__`,
`example` or `examples`, is recorded at `low`. Nothing is scored or ranked.

| Kind | Severity | Matches |
|------|----------|---------|
| `eval_call` | high | `eval(...)`, including a receiver chain (`vm.eval(`) |
| `exec_call` | high | `exec(...)`. Outside Python, only a call through a name bound to `child_process` (`exec`, `execFile`, `execSync`) |
| `pickle_loads` | high | `pickle.loads` |
| `subprocess_shell_true` | high | `subprocess.*` with `shell=True`, also across lines |
| `os_system` | high | `os.system` |
| `hardcoded_password` | high | a quoted literal assigned to a name containing `password`, any case |
| `hardcoded_secret` | high | the same for `api_key`, `apikey`, `secret`, `token`, `access_key` |
| `aws_access_key` | high | `AKIA`/`ASIA` + 16 chars, any variable name |
| `github_token` | high | `ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_`/`github_pat_` tokens |
| `slack_token` | high | `xoxb-`, `xoxa-`, `xoxp-`, `xoxr-`, `xoxs-` tokens |
| `google_api_key` | high | `AIza` + 35 chars, not from inside a base64 run |
| `stripe_key` | high | `sk_` or `rk_` then `live_`/`test_`/`prod_`. Publishable `pk_` keys do not fire |
| `private_key_pem` | high | a `-----BEGIN ... PRIVATE KEY-----` header followed by a base64 body line (an escaped `\n` counts) |
| `public_env_secret` | high | `API_KEY`/`SECRET`/`TOKEN`/`PASSWORD` behind `NEXT_PUBLIC_` or `VITE_`, excluding `..._ANON_...` |
| `new_function_call` | high | `new Function(...)` |
| `fstring_sql` | med | a Python f-string with `SELECT` and an interpolation |
| `concat_sql` | med | `.execute("SELECT ... +` |
| `template_literal_sql` | med | a JS/TS template literal with `SELECT`+`FROM` or `UPDATE`+`SET` and an interpolation |
| `tls_verify_false` | med | `verify = False` |
| `reject_unauthorized_false` | med | `rejectUnauthorized: false` |
| `unsafe_inner_html` | med | `__html:` (React's `dangerouslySetInnerHTML`) assigned a non-literal |
| `weak_hash` | low | the words `md5` or `sha1` |
| `security_sensitive_symbol` | low | a symbol named with `auth`, `token`, `password`, `jwt`, `session` or `crypto` |

`security_sensitive_symbol` is informational: it marks where security code
lives, not a problem.

The eight **secret kinds** are `hardcoded_password`, `hardcoded_secret` and
the six vendor shapes (AWS, GitHub, Slack, Google, Stripe, PEM). History mode
and the in-change commit scan report only these by default.

### What fires and what does not

- **`eval` and `exec` in Python** are found by walking the AST, so a match is
  a real call. Other languages use source with comments and strings masked out.
- **Text describing code is not code.** `pickle_loads`, `os_system`,
  `subprocess_shell_true` and `new_function_call` match over masked source, so a
  comment naming the call does not fire. The keyword secret kinds need the name
  to be code, so `password = "..."` in a doctest does not fire.
- **Vendor key shapes match anywhere**, comments included: a real key in a
  comment is still a leak.
- **Prose files** (`.md`, `.mdx`, `.rst`, `.txt`, `.adoc`) are scanned for the
  secret kinds only.
- **Placeholders are skipped**: a keyword value that is elided or templated
  (`...`, `<your key>`, `xxx`, `example`, `placeholder`, `dummy`, `fake`,
  `changeme`, `{{ }}`, `${}`), shorter than eight characters, a short
  single-case word, or a snake_case key name. A value a vendor kind already
  reports is not reported twice.
- **`weak_hash`, the SQL kinds, the TLS kinds and `unsafe_inner_html`** run on
  raw source, comments included.

### Working tree versus history

**Working tree** runs during `repowise init` and `repowise update`. Rows have
an empty `commit_sha`. `repowise security scan` without `--history` only
prints a hint, since indexing already did this.

**History** runs on `repowise security scan --history` and walks every tracked
revision. Rows carry the SHA and author date of the commit that introduced the
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
4. Low findings last. `weak_hash` is a word match.

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

- **At the head**, every touched file is scanned, and a finding counts only
  when a line it spans was added or edited. In documentation files only the
  secret kinds count.
- **Inside the change**, every non-merge commit in the range is scanned for the
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
the gate and is still reported as suppressed. Exact rules are in the
[CLI reference](../reference/CLI_REFERENCE.md#repowise-security-check-revspec).

### Baselines

`--write-baseline FILE` records the change's findings in a committed JSON file
and exits 0; `--baseline FILE` accepts what it lists, so only new findings
fail. An entry is keyed on file, kind and masked line, so it survives line
shifts. Vendor shapes also key on a one-way hash of the matched text, so a new
key on an identical line is a new finding. Keyword kinds do not, since a short
password could be guessed back from its hash. Writing to an existing baseline
adds entries; remove one by deleting it. `sarif` marks accepted findings
suppressed; `gitlab` leaves them out.

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

This is a registry of regular expressions with a little parsing. Read every
result with that in mind.

- **No taint analysis or dataflow.** Whether attacker input reaches a call is
  not computed. `eval_call` fires the same on a constant and on a request
  parameter.
- **No framework semantics.** Missing authorization, permissive CORS,
  unvalidated redirects and missing ownership checks are out of reach.
- **Mostly Python and JS/TS.** `pickle_loads`, `os_system`,
  `subprocess_shell_true`, `tls_verify_false` and `fstring_sql` are Python
  idioms. On Go, Rust or Java, `eval`/`exec` and the secret kinds are most of
  what can fire, and a quiet report reflects the registry's shape.
- **Secret detection is shallow.** No entropy scoring; providers outside the
  six vendor shapes are invisible unless you add custom patterns. Use gitleaks
  or trufflehog for secret scanning proper.
- **No dependency or CVE scanning.**
- **False positives happen.** `weak_hash` fires on `md5` in a comment.
  `unsafe_inner_html` ships at `med` because it cannot tell a name pointing at
  a pinned constant from a dynamic value.
- **The five JS/TS kinds were checked by hand** on a 17-repository, 1,109-file
  corpus. After tightening: `unsafe_inner_html` 4 hits, `template_literal_sql`
  2 of 2 real, `public_env_secret` 3 of 3 real, `new_function_call` and
  `reject_unauthorized_false` 0.
- **A CI pass means no pattern matched a changed line**, nothing more.

## Where it shows up

- **CLI**: `repowise security scan --history`, `repowise security check`,
  `repowise hook install --security`.
- **MCP**: the `security_signals` block on `get_risk`.
- **Dashboard**: the Security tab of the Code Health page.
- **REST**: `GET /api/repos/{repo_id}/security`.
- **CI**: the GitHub Action's `security` gate (annotations, SARIF) and the
  GitLab template (Code Quality).

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
