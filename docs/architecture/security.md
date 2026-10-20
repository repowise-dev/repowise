# Security signals internals

Contributor reference for the security layer. For what users see and how to read
findings, start with [layers/SECURITY.md](../layers/SECURITY.md). The scanner
lives in `packages/core/src/repowise/core/analysis/security_scan.py` and the CI
gate in `packages/core/src/repowise/core/analysis/security_gate.py`.

## Matching rules

Each kind matches over one view of the source. The view decides whether a
comment or a quoted string can fire it.

| Rule | Detail |
|---|---|
| Python `eval` and `exec` | Found by walking the AST, so a match is a real call. Other languages use source with comments and strings masked out |
| Text describing code | `pickle_loads`, `os_system`, `subprocess_shell_true` and `new_function_call` match over masked source, so a comment naming the call does not fire. The keyword secret kinds need the name to be code, so `password = "..."` in a doctest does not fire |
| Vendor key shapes | Match anywhere, comments included: a real key in a comment is still a leak |
| Prose, generated and vendored files | `.md`, `.mdx`, `.rst`, `.txt` and `.adoc` files are scanned for the secret kinds only. So are generated and vendored files (a minified or webpack bundle included) and `.d.ts` declarations, since nobody in the repository wrote their calls |
| JavaScript kinds | `unsafe_inner_html`, `template_literal_sql`, `new_function_call`, `reject_unauthorized_false` and `public_env_secret` do not fire in Python files, where such a line can only be quoted text |
| Raw source kinds | `concat_sql`, `template_literal_sql` and `unsafe_inner_html` run on raw source, comments included |
| Code-start kinds | `fstring_sql` and `weak_hash` need their match to start in code, so a comment or a fixture string quoting one does not fire |

### Placeholders

A keyword secret value is skipped when it is elided or templated (`...`,
`<your key>`, `xxx`, `example`, `placeholder`, `dummy`, `fake`, `redacted`,
`changeme`, `{{ }}`, `${}`), shorter than eight characters, a short single-case
word, or a snake_case key name. A value a vendor kind already reports is not
reported twice.

## Baseline keys

A baseline entry is keyed on file, kind and masked line, so it survives line
shifts. Vendor shapes also key on a one-way hash of the matched text, so a new key
on an identical line is a new finding. Keyword kinds carry no hash, because a
short password could be guessed back from its hash.

## Precision checks

The five JS/TS kinds are hand-checked on a 17-repository, 1,109-file corpus, with
their rules at their current strictness: `unsafe_inner_html` fires 4 times,
`template_literal_sql` hits are 2 of 2 real, `public_env_secret` hits are 3 of 3
real, and `new_function_call` and `reject_unauthorized_false` do not fire.
`unsafe_inner_html` ships at `med` because it cannot tell a name pointing at a
pinned constant from a dynamic value.
