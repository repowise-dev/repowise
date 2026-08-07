#!/bin/bash
set -e

# Start the FastAPI backend
echo "Starting repowise API server on port ${PORT_BACKEND}..."
uvicorn repowise.server.app:create_app \
  --factory \
  --host 0.0.0.0 \
  --port "${PORT_BACKEND}" &

# Start the Next.js frontend
# After PR #1236 moves the lockfile to workspace root, Next.js standalone output
# nests server.js under packages/web/ relative to the standalone root directory.
echo "Starting repowise Web UI on port ${PORT_FRONTEND}..."
cd /app/web/packages/web
REPOWISE_API_URL="http://localhost:${PORT_BACKEND}" \
HOSTNAME="0.0.0.0" \
PORT="${PORT_FRONTEND}" \
  node server.js &

# Wait for either process to exit
wait -n
exit $?
