# Repowise Helm Chart

Deploy [Repowise](https://github.com/repowise-dev/repowise) on Kubernetes.

## Prerequisites

- Kubernetes 1.24+
- Helm 3.x
- A container image built from `docker/Dockerfile` pushed to your registry

## Quick Start

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

## Persistence

Repowise stores its SQLite database and indexed repository data under `/data`. The chart creates a PVC by default. To disable (data lost on pod restart):

```bash
helm install repowise ./charts/repowise --set persistence.enabled=false
```
