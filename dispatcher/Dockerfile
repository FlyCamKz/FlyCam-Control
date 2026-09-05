FROM python:3.12-slim

WORKDIR /app
COPY server.py /app/server.py
COPY crypto.py /app/crypto.py
COPY requirements.txt /app/requirements.txt
COPY web /app/web

RUN pip install --no-cache-dir -r /app/requirements.txt

ENV FLYCAM_HOST=0.0.0.0 \
    FLYCAM_PORT=8088 \
    FLYCAM_DB=/data/flycam-dispatcher.sqlite3

RUN mkdir -p /data && chown 65534:65534 /data

VOLUME ["/data"]
EXPOSE 8088

USER 65534:65534
CMD ["python", "/app/server.py"]
