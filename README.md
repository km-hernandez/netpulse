# NetPulse

A lightweight, real-time **network monitor dashboard**. It probes the hosts and services you care about, records latency / loss / jitter in SQLite, samples your machine's interface throughput, and streams everything live to a dependency-free web UI.

![CI](https://github.com/km-hernandez/netpulse/actions/workflows/ci.yml/badge.svg)

## Features

- **Four probe types** — ICMP `ping`, `tcp` connect, `dns` resolution and `http` status checks
- **Live dashboard** — updates pushed over Server-Sent Events, no polling, no page reloads
- **Interface throughput** — download / upload rate for the host, sampled every second
- **Per-target stats** — uptime, packet loss, avg / p95 / min / max latency and jitter over 15m, 1h, 6h or 24h
- **Heartbeat bars and sparklines** — see the last 40 checks of every target at a glance
- **Degraded state** — per-target latency threshold separates "slow" from "down"
- **History** — results persisted to SQLite with automatic retention pruning
- **Zero frontend dependencies** — vanilla JS + canvas, dark and light themes
- **REST API** with OpenAPI docs at `/docs`

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
netpulse                      # http://127.0.0.1:8080
```

Or with Docker:

```bash
docker compose up --build     # http://localhost:8080
```

> Inside a container the throughput chart reflects the container's network namespace, not the host's. Run natively (or with `network_mode: host` on Linux) to see host traffic.

## Configuration

Targets live in [`config.toml`](config.toml):

```toml
[defaults]
interval = 5            # seconds between checks
timeout = 2
latency_warn_ms = 150   # above this the target shows as "degraded"

[[targets]]
name = "Cloudflare DNS"
type = "ping"
host = "1.1.1.1"

[[targets]]
name = "GitHub HTTPS"
type = "tcp"
host = "github.com"
port = 443

[[targets]]
name = "GitHub API"
type = "http"
url = "https://api.github.com"
interval = 15
```

| Type   | Required fields | Measures                               |
|--------|-----------------|----------------------------------------|
| `ping` | `host`          | ICMP round-trip time (system `ping`)   |
| `tcp`  | `host`, `port`  | TCP handshake time                     |
| `dns`  | `host`          | Resolver lookup time                   |
| `http` | `url`           | Full request time, fails on status ≥ 400 |

Environment variables: `NETPULSE_CONFIG` (config path), `NETPULSE_DB` (SQLite path).
CLI flags: `--config`, `--host`, `--port`.

## API

| Endpoint                                   | Description                              |
|--------------------------------------------|------------------------------------------|
| `GET /api/targets?minutes=60`              | Targets with latest result and stats     |
| `GET /api/targets/{id}/history?minutes=15` | Raw check results                        |
| `GET /api/bandwidth`                       | Last ~5 minutes of throughput samples    |
| `GET /api/system`                          | Hostname and active interfaces           |
| `GET /api/stream`                          | SSE stream (`check`, `bandwidth` events) |
| `GET /api/health`                          | Liveness probe                           |

## Architecture

```
config.toml ─► Monitor ──► probes (ping / tcp / dns / http) ─┐
                 │                                           ├─► SQLite (Store)
                 └─► psutil throughput sampler               │
                                   │                         │
                             pub/sub queues ◄────────────────┘
                                   │
                 FastAPI ── REST + SSE ──► browser (vanilla JS + canvas)
```

- `netpulse/checks.py` — probes; each returns a `CheckResult` and never raises
- `netpulse/monitor.py` — one asyncio task per target, plus throughput and pruning loops
- `netpulse/store.py` — SQLite persistence and statistics
- `netpulse/main.py` — FastAPI app, SSE stream, static dashboard

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

## License

MIT
