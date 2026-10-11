# Multi-stage Dockerfile for the sample calculator service.
# Stage 1: build the Go service.
# Stage 2: run the Python service alongside the Go binary.

# ---------- Stage 1: Go build ----------
FROM golang:1.21-alpine AS go-builder

WORKDIR /src/go_pkg
COPY go_pkg/go.mod ./
COPY go_pkg/ ./
RUN go build -o /bin/calculator ./...

# ---------- Stage 2: Python runtime ----------
FROM python:3.11-slim AS runtime

WORKDIR /app

# Install Python dependencies
COPY python_pkg/ ./python_pkg/
RUN pip install --no-cache-dir -e ./python_pkg

# Copy the compiled Go binary
COPY --from=go-builder /bin/calculator /usr/local/bin/calculator

EXPOSE 8080

ENV PYTHONUNBUFFERED=1
ENV LOG_LEVEL=info

ENTRYPOINT ["python", "-m", "python_pkg"]
CMD ["--host", "0.0.0.0", "--port", "8080"]
