import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from netpulse.checks import CheckResult, check_dns, check_tcp, parse_ping_output, ping_command
from netpulse.config import Target, parse_settings
from netpulse.main import create_app
from netpulse.store import Store, summarize


# ---------- config ----------
def test_parse_settings_defaults_and_unique_ids():
    s = parse_settings({
        "defaults": {"interval": 7},
        "targets": [
            {"name": "Edge", "type": "ping", "host": "1.1.1.1"},
            {"name": "Edge", "type": "tcp", "host": "1.1.1.1", "port": 443, "interval": 2},
        ],
    })
    assert [t.id for t in s.targets] == ["edge", "edge-2"]
    assert s.targets[0].interval == 7
    assert s.targets[1].interval == 2
    assert s.targets[1].describe() == "1.1.1.1:443"


@pytest.mark.parametrize("raw", [
    {"name": "x", "type": "icmp6", "host": "h"},
    {"name": "x", "type": "http"},
    {"name": "x", "type": "tcp", "host": "h"},
    {"name": "x", "type": "ping"},
])
def test_parse_settings_rejects_invalid_targets(raw):
    with pytest.raises(ValueError):
        parse_settings({"targets": [raw]})


# ---------- ping parsing ----------
def test_parse_ping_output_variants():
    assert parse_ping_output("64 bytes from 1.1.1.1: icmp_seq=0 ttl=57 time=12.345 ms") == 12.345
    assert parse_ping_output("Reply from 1.1.1.1: bytes=32 time<1ms TTL=57") == 1.0
    assert parse_ping_output("Request timeout for icmp_seq 0") is None


def test_ping_command_targets_host():
    cmd = ping_command("example.com", 2)
    assert cmd[0] == "ping" and cmd[-1] == "example.com"


# ---------- probes against a local server ----------
async def test_tcp_check_success_and_refused():
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        ok = await check_tcp(Target("t", "t", "tcp", "127.0.0.1", port=port))
    assert ok.ok and ok.latency_ms is not None and ok.latency_ms >= 0

    refused = await check_tcp(Target("t", "t", "tcp", "127.0.0.1", port=port, timeout=1))
    assert not refused.ok and refused.latency_ms is None


async def test_dns_check_localhost_and_invalid():
    ok = await check_dns(Target("d", "d", "dns", "localhost"))
    assert ok.ok
    bad = await check_dns(Target("d", "d", "dns", "does-not-exist.invalid"))
    assert not bad.ok


# ---------- stats ----------
def test_summarize_empty():
    assert summarize([])["uptime"] is None


def test_summarize_uptime_loss_and_jitter():
    rows = [
        {"ok": True, "latency_ms": 10.0},
        {"ok": True, "latency_ms": 20.0},
        {"ok": False, "latency_ms": None},
        {"ok": True, "latency_ms": 30.0},
    ]
    s = summarize(rows)
    assert s["samples"] == 4
    assert s["uptime"] == 75.0 and s["loss"] == 25.0
    assert s["avg_ms"] == 20.0 and s["min_ms"] == 10.0 and s["max_ms"] == 30.0
    assert s["jitter_ms"] == 10.0


def test_store_history_window_and_prune(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    now = time.time()
    store.add(CheckResult("a", now - 7200, True, 5.0, "old"))
    store.add(CheckResult("a", now - 10, True, 6.0, "new"))
    store.add(CheckResult("b", now - 5, False, None, "timeout"))
    assert [r["detail"] for r in store.history("a", now - 60)] == ["new"]
    assert store.stats("b", now - 60)["uptime"] == 0.0
    assert store.prune(now - 3600) == 1
    assert len(store.history("a", 0)) == 1
    store.close()


# ---------- API ----------
@pytest.fixture
def client(tmp_path):
    settings = parse_settings({
        "general": {"db_path": str(tmp_path / "api.db")},
        "targets": [{"name": "Local", "type": "tcp", "host": "127.0.0.1", "port": 9}],
    })
    with TestClient(create_app(settings, start_monitor=False)) as c:
        yield c


def test_api_targets_and_history(client):
    store = client.app.state.store
    store.add(CheckResult("local", time.time(), True, 3.2, "connected"))

    targets = client.get("/api/targets").json()
    assert targets[0]["id"] == "local"
    assert targets[0]["stats"]["uptime"] == 100.0

    hist = client.get("/api/targets/local/history").json()
    assert len(hist) == 1 and hist[0]["latency_ms"] == 3.2
    assert client.get("/api/targets/nope/history").status_code == 404


def test_api_health_system_and_dashboard(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert "hostname" in client.get("/api/system").json()
    assert "NetPulse" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
