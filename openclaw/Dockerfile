FROM localhost:5001/ax-task-runner:latest

USER root

# Install curl, jq, nmap
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    jq \
    nmap \
    && rm -rf /var/lib/apt/lists/*

# Install OpenTelemetry SDK and urllib auto-instrumentation
RUN pip install --no-cache-dir \
    opentelemetry-api \
    opentelemetry-sdk \
    opentelemetry-exporter-otlp-proto-http \
    opentelemetry-instrumentation-urllib \
    opentelemetry-semantic-conventions

# Copy OpenClaw agent code
COPY agent /agent

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/ax-task-runner"]
