FROM localhost:5001/ax-task-runner:latest

USER root

# Install nmap, curl, jq
RUN apt-get update && apt-get install -y --no-install-recommends \
    nmap \
    curl \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Install OpenTelemetry SDK and GenAI semantic conventions
RUN pip install --no-cache-dir \
    opentelemetry-api \
    opentelemetry-sdk \
    opentelemetry-exporter-otlp-proto-http \
    opentelemetry-semantic-conventions \
    opentelemetry-semantic-conventions-ai

# Copy the agent code
COPY agent /agent

# Keep ax-task-runner as the entrypoint
WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/ax-task-runner"]
