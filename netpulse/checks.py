"""Network probes. Every probe returns a CheckResult and never raises."""

from __future__ import annotations

import asyncio
import re
import shutil
import socket
import sys
import time
from dataclasses import dataclass

import httpx

from .config import Target

_PING_TIME = re.compile(r"time[=<]\s*([\d.]+)\s*ms")


@dataclass
class CheckResult:
    target_id: str
    ts: float
    ok: bool
    latency_ms: float | None
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "target_id": self.target_id,
            "ts": self.ts,
            "ok": self.ok,
            "latency_ms": None if self.latency_ms is None else round(self.latency_ms, 2),
            "detail": self.detail,
        }


def parse_ping_output(output: str) -> float | None:
    """Extract the round-trip time (ms) from `ping -c 1` output."""
    match = _PING_TIME.search(output)
    return float(match.group(1)) if match else None


def ping_command(host: str, timeout: float) -> list[str]:
    if sys.platform == "darwin":
        # macOS: -W is in milliseconds
        return ["ping", "-n", "-c", "1", "-W", str(int(timeout * 1000)), host]
    if sys.platform.startswith("win"):
        return ["ping", "-n", "1", "-w", str(int(timeout * 1000)), host]
    # Linux / BusyBox: -W is in seconds
    return ["ping", "-n", "-c", "1", "-W", str(max(1, round(timeout))), host]


async def check_ping(target: Target) -> CheckResult:
    ts = time.time()
    if not shutil.which("ping"):
        # No ping binary (e.g. slim containers): fall back to a TCP probe on 443.
        fallback = Target(**{**target.__dict__, "type": "tcp", "port": target.port or 443})
        result = await check_tcp(fallback)
        result.detail = f"ping unavailable, tcp/{fallback.port}: {result.detail}"
        return result
    try:
        proc = await asyncio.create_subprocess_exec(
            *ping_command(target.host, target.timeout),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=target.timeout + 1.5)
    except TimeoutError:
        proc.kill()
        return CheckResult(target.id, ts, False, None, "timeout")
    except OSError as exc:
        return CheckResult(target.id, ts, False, None, str(exc))

    text = out.decode(errors="replace")
    rtt = parse_ping_output(text)
    if proc.returncode == 0 and rtt is not None:
        return CheckResult(target.id, ts, True, rtt, "reply")
    last = text.strip().splitlines()[-1] if text.strip() else "no reply"
    return CheckResult(target.id, ts, False, None, "packet lost" if "100" in last else last[:120])


async def check_tcp(target: Target) -> CheckResult:
    ts = time.time()
    start = time.perf_counter()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(target.host, target.port), timeout=target.timeout
        )
        latency = (time.perf_counter() - start) * 1000
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        return CheckResult(target.id, ts, True, latency, "connected")
    except TimeoutError:
        return CheckResult(target.id, ts, False, None, "timeout")
    except OSError as exc:
        return CheckResult(target.id, ts, False, None, exc.strerror or str(exc))


async def check_dns(target: Target) -> CheckResult:
    ts = time.time()
    start = time.perf_counter()
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(target.host, None, type=socket.SOCK_STREAM), timeout=target.timeout
        )
        latency = (time.perf_counter() - start) * 1000
        addrs = sorted({info[4][0] for info in infos})
        return CheckResult(target.id, ts, True, latency, ", ".join(addrs[:3]))
    except TimeoutError:
        return CheckResult(target.id, ts, False, None, "timeout")
    except socket.gaierror as exc:
        return CheckResult(target.id, ts, False, None, exc.strerror or str(exc))


async def check_http(target: Target, client: httpx.AsyncClient | None = None) -> CheckResult:
    ts = time.time()
    own_client = client is None
    client = client or httpx.AsyncClient(follow_redirects=True)
    start = time.perf_counter()
    try:
        resp = await client.get(target.url, timeout=target.timeout)
        latency = (time.perf_counter() - start) * 1000
        ok = resp.status_code < 400
        return CheckResult(target.id, ts, ok, latency, f"HTTP {resp.status_code}")
    except httpx.TimeoutException:
        return CheckResult(target.id, ts, False, None, "timeout")
    except httpx.HTTPError as exc:
        return CheckResult(target.id, ts, False, None, type(exc).__name__)
    finally:
        if own_client:
            await client.aclose()


async def run_check(target: Target, http_client: httpx.AsyncClient | None = None) -> CheckResult:
    if target.type == "ping":
        return await check_ping(target)
    if target.type == "tcp":
        return await check_tcp(target)
    if target.type == "dns":
        return await check_dns(target)
    if target.type == "http":
        return await check_http(target, http_client)
    return CheckResult(target.id, time.time(), False, None, f"unknown type {target.type}")
