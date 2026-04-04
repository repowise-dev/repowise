# =============================================================================
# repowise — multi-stage Docker build (backend + frontend)
# =============================================================================
# Usage:
#   docker build -t repowise -f docker/Dockerfile .
#   docker run -p 7337:7337 -p 3000:3000 -v /path/to/repo/.repowise:/data -e GEMINI_API_KEY=... repowise
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Build the Next.js frontend
# ---------------------------------------------------------------------------
FROM node:20-alpine AS frontend-builder

WORKDIR /app

# Install dependencies first (cached layer)
COPY packages/web/package.json packages/web/package-lock.json* ./
RUN npm install --production=false

# Copy source and build
COPY packages/web/ ./
ENV NEXT_TELEMETRY_DISABLED=1
ENV NEXT_PUBLIC_REPOWISE_API_URL=http://localhost:7337
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: Python backend + frontend runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Install git (required by gitpython) and Node.js (required for Next.js server)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install repowise Python package
COPY pyproject.toml README.md LICENSE MANIFEST.in ./
COPY packages/core/ packages/core/
COPY packages/cli/ packages/cli/
COPY packages/server/ packages/server/
RUN pip install --no-cache-dir ".[all]"

# Copy built Next.js standalone output
COPY --from=frontend-builder /app/.next/standalone /app/web
COPY --from=frontend-builder /app/.next/static /app/web/.next/static
COPY --from=frontend-builder /app/public /app/web/public

# Data volume for .repowise directory
VOLUME /data

# Environment defaults
ENV REPOWISE_DB_URL=sqlite+aiosqlite:////data/wiki.db
ENV REPOWISE_EMBEDDER=mock
ENV PORT_BACKEND=7337
ENV PORT_FRONTEND=3000
ENV HOSTNAME=0.0.0.0

# Expose both ports
EXPOSE 7337 3000

# Startup script
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
