# Components

Name the parts of your repository and see code facts per part. Copy
[components.yaml](components.yaml) to `.repowise/components.yaml` (or use its
documents in a repo-root `catalog-info.yaml`), adjust the paths, then:

```bash
repowise components check   # validate; exits 1 on errors
repowise components list    # declared and derived components with file counts
```

Agents read the same model through `get_context(targets=["component:payments-api"])`
and `get_overview(include=["components"])`. No file is needed to start: without
one, components are derived from the index. The format is described in
[CONFIG.md](../../docs/reference/CONFIG.md#repowisecomponentsyaml).
