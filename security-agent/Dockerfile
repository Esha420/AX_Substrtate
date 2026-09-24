FROM localhost:5001/ax-task-runner:latest

USER root

# Install nmap, curl, jq
RUN apt-get update && apt-get install -y --no-install-recommends \
    nmap \
    curl \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Copy the agent code
COPY agent /agent

# Keep ax-task-runner as the entrypoint
WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/ax-task-runner"]
