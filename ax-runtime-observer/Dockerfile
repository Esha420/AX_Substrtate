FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl jq ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=localhost:5001/ax-toolbox:latest /usr/local/bin/ax /usr/local/bin/ax
COPY --from=localhost:5001/ax-toolbox:latest /usr/local/bin/kubectl /usr/local/bin/kubectl
COPY --from=localhost:5001/ax-toolbox:latest /usr/local/bin/kubectl-ate /usr/local/bin/kubectl-ate
WORKDIR /app
COPY observer.py /app/observer.py
ENTRYPOINT ["python3", "-u", "/app/observer.py"]
