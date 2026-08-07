# =============================================================================
# repowise — multi-stage Docker build (backend + frontend)
# =============================================================================
# Usage:
#   docker build -t repowise -f docker/Dockerfile .
#   docker run -p 7337:7337 -p 3000:3000 -v /path/to/repo/.repowise:/data -e GEMINI_API_KEY=... repowise
# =============================================================================

# Use Debian-based Node builder (glibc) to match python:3.12-slim runtime.
# Alpine (musl) builds produce a node binary that fails to execute in Debian slim with exit code 127.
FROM node:20-bookworm-slim AS frontend-builder

WORKDIR /app

# Copy root manifest/lockfile and all packages for monorepo resolution
COPY package.json package-lock.json* ./
COPY packages/ ./packages/

# Change directory to web package and install dependencies
WORKDIR /app/packages/web
RUN npm install --production=false

# Build Next.js frontend
ENV NEXT_TELEMETRY_DISABLED=1
ENV NEXT_PUBLIC_REPOWISE_API_URL=http://localhost:7337
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: Python backend + frontend runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Install git (required by gitpython)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy Node.js from the official image instead of piping curl|bash
COPY --from=frontend-builder /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend-builder /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm

WORKDIR /app

# Install repowise Python package
COPY pyproject.toml README.md LICENSE MANIFEST.in ./
COPY packages/core/ packages/core/
COPY packages/cli/ packages/cli/
COPY packages/server/ packages/server/
RUN pip install --no-cache-dir ".[all]"

# Copy built Next.js standalone output
COPY --from=frontend-builder /app/packages/web/.next/standalone /app/web
COPY --from=frontend-builder /app/packages/web/.next/static /app/web/.next/static
COPY --from=frontend-builder /app/packages/web/public /app/web/public

# Data volume for .repowise directory
# mkdir is required: VOLUME only registers metadata and does not create the
# directory on the image filesystem at build time. Without it, the subsequent
# `chown -R repowise:repowise /data` (run at build time) fails with
# "cannot access '/data': No such file or directory".
RUN mkdir -p /data
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

# Run as non-root user (mitigates container escape → root escalation)
RUN groupadd -r repowise && useradd -r -g repowise -d /app -s /sbin/nologin repowise \
    && chown -R repowise:repowise /app /data
USER repowise

ENTRYPOINT ["/app/entrypoint.sh"]