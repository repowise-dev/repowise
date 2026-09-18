# repowise Helm Chart

Deploy [Repowise](https://repowise.dev) — codebase intelligence for developers
and AI — on Kubernetes. The chart runs the official Docker image
(`docker/Dockerfile`), mapping the same environment variables and volume
mounts into k8s-native resources.

## Quick start

```bash
helm install repowise ./charts/repowise \
  --set secret.repowiseApiKey="$(openssl rand -hex 16)"
```

This gives you:

- The API server (port 7337) and web UI (port 3000) in one pod
- SQLite on a 10Gi PVC at `/data` (the project's default store)
- Liveness/readiness probes against the unauthenticated `/health` endpoint
- A generated Secret for `REPOWISE_API_KEY` and any LLM provider keys

Port-forward to reach it:

```bash
kubectl port-forward svc/repowise 7337:7337 3000:3000
```

## Configuration

### API keys

Keys are mounted from a Secret — never bake them into `values.yaml` in
version control. Either let the chart generate one:

```yaml
secret:
  createSecret: true
  repowiseApiKey: "..."        # required for external access
  anthropicApiKey: "sk-ant-..."  # only set what you use
  openaiApiKey: ""
  geminiApiKey: ""
```

or reference an existing Secret you manage yourself:

```yaml
secret:
  createSecret: false
  existingSecret: my-repowise-secret
```

The referenced Secret must carry the same key names (`REPOWISE_API_KEY`,
`ANTHROPIC_API_KEY`, ...).

### Persistence

**SQLite (default)** — a PVC is created and mounted at `/data`:

```yaml
persistence:
  enabled: true
  storageClass: ""      # cluster default
  size: 10Gi
```

**PostgreSQL (opt-in)** — set `postgresql.enabled: true` and the chart
switches `REPOWISE_DB_URL` to a PostgreSQL DSN and stops using the SQLite
PVC. Bring your own server:

```yaml
postgresql:
  enabled: true
  host: my-postgres.default.svc
  port: 5432
  database: repowise
  user: repowise
  existingSecret: pg-credentials   # key: password
```

### Indexed repositories

Mount repositories read-only, mirroring `docker-compose.yml`. Each entry
becomes a read-only volume at `/repos/<name>`:

```yaml
repos:
  - name: my-repo
    hostPath: /srv/repos/my-repo
  - name: other-repo
    pvc: my-repo-pvc
```

### Ingress

```yaml
ingress:
  enabled: true
  className: nginx
  hosts:
    - host: repowise.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - hosts: [repowise.example.com]
      secretName: repowise-tls
```

### Embedder

```yaml
env:
  REPOWISE_EMBEDDER: mock   # mock (default, keyless), openai, ollama, ...
  extra:
    OLLAMA_HOST: http://ollama.default.svc:11434
```

### Resources

```yaml
resources:
  requests:
    cpu: 500m
    memory: 512Mi
  limits:
    cpu: "2"
    memory: 2Gi
```

## Notes

- **Replicas are pinned to 1.** Repowise keeps its state in the SQLite file
  and the vector store on the PVC; a second replica would race on the same
  volume. Scale out by indexing more repos, not by replicating the pod.
- **`REPOWISE_API_KEY` is required for external access.** Without it the API
  only answers from inside the cluster (the entrypoint warns on startup).
- **Probes** hit `/health`, which is deliberately unauthenticated.
