FROM localhost:5001/ax-toolbox:latest AS toolbox

FROM python:3.12-slim

COPY --from=toolbox /usr/local/bin/ax /usr/local/bin/ax

WORKDIR /app
COPY receiver.py /app/receiver.py

ENV PORT=8080
ENV AX_SERVER_URL=http://ax-server.ax-system.svc.cluster.local:8080

EXPOSE 8080
ENTRYPOINT ["python3", "/app/receiver.py"]
