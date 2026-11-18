# Change Policy

A change policy holds some changes to a higher bar than others. Rules live in
`.repowise/policies.yaml`, next to the code they govern, and `repowise check` evaluates
them against any change, locally or in CI. Nothing is sent anywhere.

```yaml
version: 1
rules:
  - name: payments-need-tests
    match:
      components: {tags: [pci]}
    when:
      reaching_tests: missing
    action: require_tests
  - name: no-silent-contract-breaks
    when:
      contract_break: true
    action: fail
  - name: no-new-secrets
    gate: security
    with: {fail_on: high}
    action: fail
```

## What a rule can look at

A rule selects [components](COMPONENTS.md) by name, tag or criticality, then tests
conditions over the change's [evidence checklist](CHANGES.md): provenance, reaching
tests, contract breaks, decisions crossed and bus factor. A rule can also wrap an
existing gate (`security`, `coverage`, `doc_drift` or `health`), which then runs exactly as the
command does on its own. There is no risk-score condition: rules rest on evidence you
can check.

A condition whose evidence is not available, for example with no index, is reported
as not evaluated and never fires.

## Running it

```bash
repowise check main..HEAD
repowise check main..HEAD --sarif-file policy.sarif --junit-file policy.xml
```

Actions are `warn`, `require_owner_review`, `require_tests` and `fail`. Only `fail`
exits 1; the others are reported in every format. SARIF 2.1.0 feeds GitHub code
scanning or reviewdog, and JUnit shows up in any CI test view. Teams that already run
OPA can pass the `evidence` object from `--format json` to Rego.

Every key, condition and exit code: [CONFIG.md](../reference/CONFIG.md#repowisepoliciesyaml)
and [CLI reference](../reference/CLI_REFERENCE.md#repowise-check-revspec).
