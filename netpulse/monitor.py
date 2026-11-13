"""Background scheduler: runs probes, samples interface throughput, fans out events."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque

import httpx
import psutil

from .checks import CheckResult, run_check
from .config import Settings, Target
from .store import Store

log = logging.getLogger("netpulse")


class Monitor:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self.latest: dict[str, CheckResult] = {}
        self.bandwidth: deque[dict] = deque(maxlen=300)  # ~5 minutes at 1s
        self._subscribers: set[asyncio.Queue] = set()
        self._tasks: list[asyncio.Task] = []
        self._http: httpx.AsyncClient | None = None

    # ---- pub/sub -------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def _publish(self, event: str, data: dict) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait((event, data))
            except asyncio.QueueFull:
                # Slow consumer: drop it rather than block the monitor.
                self._subscribers.discard(queue)

    # ---- lifecycle -----------------------------------------------------
    async def start(self) -> None:
        self._http = httpx.AsyncClient(follow_redirects=True, headers={"User-Agent": "netpulse/1.0"})
        for target in self.settings.targets:
            self._tasks.append(asyncio.create_task(self._probe_loop(target), name=f"probe:{target.id}"))
        self._tasks.append(asyncio.create_task(self._bandwidth_loop(), name="bandwidth"))
        self._tasks.append(asyncio.create_task(self._prune_loop(), name="prune"))
        log.info("Monitoring %d targets", len(self.settings.targets))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        if self._http:
            await self._http.aclose()

    # ---- loops ---------------------------------------------------------
    async def _probe_loop(self, target: Target) -> None:
        while True:
            started = time.monotonic()
            try:
                result = await run_check(target, self._http)
            except Exception as exc:  # defensive: a probe must never kill its loop
                log.exception("probe %s crashed", target.id)
                result = CheckResult(target.id, time.time(), False, None, f"error: {exc}")
            self.latest[target.id] = result
            await asyncio.to_thread(self.store.add, result)
            self._publish("check", result.to_dict())
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(0.1, target.interval - elapsed))

    async def _bandwidth_loop(self) -> None:
        interval = self.settings.bandwidth_interval
        prev = psutil.net_io_counters()
        prev_t = time.monotonic()
        while True:
            await asyncio.sleep(interval)
            now = psutil.net_io_counters()
            now_t = time.monotonic()
            dt = max(now_t - prev_t, 1e-6)
            sample = {
                "ts": time.time(),
                "rx_bps": max(0.0, (now.bytes_recv - prev.bytes_recv) * 8 / dt),
                "tx_bps": max(0.0, (now.bytes_sent - prev.bytes_sent) * 8 / dt),
                "rx_total": now.bytes_recv,
                "tx_total": now.bytes_sent,
                "errors": now.errin + now.errout,
                "drops": now.dropin + now.dropout,
            }
            prev, prev_t = now, now_t
            self.bandwidth.append(sample)
            self._publish("bandwidth", sample)

    async def _prune_loop(self) -> None:
        while True:
            cutoff = time.time() - self.settings.retention_hours * 3600
            removed = await asyncio.to_thread(self.store.prune, cutoff)
            if removed:
                log.info("Pruned %d old results", removed)
            await asyncio.sleep(600)
