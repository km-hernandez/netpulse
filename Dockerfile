FROM python:3.13-slim

# iputils-ping gives the container a real ICMP ping binary
RUN apt-get update && apt-get install -y --no-install-recommends iputils-ping \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY netpulse ./netpulse
RUN pip install --no-cache-dir .
COPY config.toml ./

ENV NETPULSE_DB=/data/netpulse.db
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health')"
CMD ["netpulse", "--host", "0.0.0.0", "--port", "8080"]
