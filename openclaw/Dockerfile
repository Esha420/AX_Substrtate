FROM localhost:5001/ax-task-runner:latest

USER root

# Install curl, jq
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Copy OpenClaw agent code
COPY agent /agent

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/ax-task-runner"]
