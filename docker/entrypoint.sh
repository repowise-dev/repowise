#!/bin/bash
set -e

# Both servers bind 0.0.0.0 inside the container, so without a key the only
# thing standing between the API and the network is the port publishing.
if [ -z "${REPOWISE_API_KEY}" ]; then
  echo "WARNING: REPOWISE_API_KEY is not set. Requests from outside the container" \
       "will be refused; the API is only usable from inside it. Set REPOWISE_API_KEY."
fi

# Start the FastAPI backend
echo "Starting repowise API server on port ${PORT_BACKEND}..."
uvicorn repowise.server.app:create_app \
  --factory \
  --host 0.0.0.0 \
  --port "${PORT_BACKEND}" &

# Start the Next.js frontend
# outputFileTracingRoot points to the repo root, so Next.js standalone output
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
