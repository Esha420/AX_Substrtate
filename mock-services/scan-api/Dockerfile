FROM python:3.12-slim

WORKDIR /app
COPY main.py /app/main.py

ENV PORT=8080
ENV JOB_DELAY_SECONDS=60

EXPOSE 8080
ENTRYPOINT ["python3", "/app/main.py"]
