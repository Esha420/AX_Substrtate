FROM localhost:5001/ax-task-runner:latest

USER root

# Install python3 and curl if not already present
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    jq \
    && rm -rf /var/lib/apt/lists/*

COPY probe.py /app/probe.py

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/ax-task-runner"]
