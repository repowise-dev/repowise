# Repowise Helm Chart

Deploy [Repowise](https://github.com/repowise-dev/repowise) on Kubernetes.

## Prerequisites

- Kubernetes 1.24+
- Helm 3.x
- A container image built from `docker/Dockerfile` pushed to your registry

## Quick Start

The chart expects you to build and push your own container image from the repo's `docker/Dockerfile`:

```bash
# Build and push the image
docker build -t your-registry/repowise:0.1.0 -f docker/Dockerfile .
docker push your-registry/repowise:0.1.0

# Install the chart
helm install repowise ./charts/repowise \
  --set image.repository=your-registry/repowise \
  --set image.tag=0.1.0 \
  --set apiKeys.anthropic=sk-ant-...
```

> **Important:** Change the default PostgreSQL credentials (`repowise`/`repowise`) before deploying to production:
> ```bash
> --set postgresql.auth.username=myuser \
> --set postgresql.auth.password=<strong-password>
> ```

## Configuration

| Parameter | Description | Default |
|-----------|-------------|---------|
| `replicaCount` | Number of replicas (only 1 supported with SQLite) | `1` |
| `image.repository` | Container image repository | `repowise/repowise` |
| `image.tag` | Image tag (defaults to appVersion) | `""` |
| `repowise.embedder` | Embedder backend (`mock`, `openai`, `gemini`) | `mock` |
| `repowise.dbUrl` | Database connection URL | `sqlite+aiosqlite:////data/wiki.db` |
| `repowise.backendPort` | API server port | `7337` |
| `repowise.frontendPort` | Web UI port | `3000` |
| `apiKeys.anthropic` | Anthropic API key | `""` |
| `apiKeys.openai` | OpenAI API key | `""` |
| `apiKeys.gemini` | Gemini API key | `""` |
| `existingSecret` | Use an existing Secret for API keys | `""` |
| `persistence.enabled` | Enable PVC for `/data` | `true` |
| `persistence.size` | PVC size | `10Gi` |
| `persistence.storageClass` | Storage class (empty = cluster default) | `""` |
| `ingress.enabled` | Enable Ingress | `false` |
| `ingress.className` | Ingress class name | `""` |
| `ingress.hosts` | Ingress host rules | see `values.yaml` |

## Using an Existing Secret

If you manage secrets externally (e.g., Sealed Secrets, External Secrets):

```bash
kubectl create secret generic my-repowise-keys \
  --from-literal=ANTHROPIC_API_KEY=sk-ant-... \
  --from-literal=OPENAI_API_KEY= \
  --from-literal=GEMINI_API_KEY=

helm install repowise ./charts/repowise \
  --set existingSecret=my-repowise-keys
```

## Ingress Example

```yaml
ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
  hosts:
    - host: repowise.example.com
      paths:
        - path: /
          pathType: Prefix
          servicePort: frontend
  tls:
    - secretName: repowise-tls
      hosts:
        - repowise.example.com
```

## Auto-Cloning Repositories

You can declare repos in `values.yaml` and the chart will automatically clone them via init containers and write a repos manifest (`/data/repos.json`) for the app to auto-register on startup.

### Public repos

```bash
helm install repowise ./charts/repowise \
  --set repos[0].name=my-app \
  --set repos[0].url=https://github.com/org/my-app.git \
  --set repos[0].branch=main
```

### Private repos (GitHub PAT)

```bash
helm install repowise ./charts/repowise \
  --set repos[0].name=my-private-app \
  --set repos[0].url=https://github.com/org/my-private-app.git \
  --set gitCredentials.github.username=my-user \
  --set gitCredentials.github.token=ghp_...
```

### Private repos (existing Secret)

```bash
kubectl create secret generic git-creds \
  --from-literal=git-credentials='https://my-user:ghp_token@github.com'

helm install repowise ./charts/repowise \
  --set repos[0].name=my-private-app \
  --set repos[0].url=https://github.com/org/my-private-app.git \
  --set gitCredentials.secretName=git-creds
```

### Multiple repos

```yaml
repos:
  - name: frontend
    url: https://github.com/org/frontend.git
    branch: main
  - name: backend
    url: https://github.com/org/backend.git
    branch: develop
  - name: infra
    url: https://github.com/org/infra.git
```

The init container clones repos to `/data/repos/<name>` and writes a manifest to `/data/repos.json`. The app picks up the manifest on startup, registers each repo, and triggers indexing. Monitor clone progress with:

```bash
kubectl logs <pod-name> -c clone-repos -n <namespace>
```

## Persistence

Repowise stores its SQLite database and indexed repository data under `/data`. The chart creates a PVC by default. To disable (data lost on pod restart):

```bash
helm install repowise ./charts/repowise --set persistence.enabled=false
```
